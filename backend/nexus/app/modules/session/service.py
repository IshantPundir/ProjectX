"""Session service — orchestration layer.

This task fills:
  - create_session       — insert sessions row (state=created)
  - mint_token           — insert candidate_session_tokens row + return JWT
  - supersede_token      — atomic SET superseded_at on prior row, link to successor

Later tasks extend this file with pre-check/consent/OTP/start/list functions.

Rules (per Phase 3B lessons-learned):
  * Services flush only — never commit. Session factories auto-commit on context exit.
  * `log_event(db, *, tenant_id=, actor_id=, actor_email=, action=, resource=, resource_id=, payload=)`
  * `user.user.id` / `user.user.email` (never `user.user_id`)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateSessionToken,
    JobPipelineStage,
    JobPosting,
    OrganizationalUnit,
    Session,
)
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.auth.service import create_candidate_token
from app.modules.org_units.service import find_company_profile_in_ancestry
from app.modules.session.errors import (
    IllegalStartStateError,
    InvalidOtpError,
    InvalidSessionStateError,
    OtpExpiredError,
    OtpMaxAttemptsReachedError,
    OtpRateLimitedError,
    OtpRequiredError,
    SessionNotFoundError,
    TokenAlreadyUsedError,
)
from app.modules.session.otp import generate_code, hash_code, verify_code
from app.modules.session.schemas import (
    PreCheckResponse,
    SessionDetailResponse,
    SessionListPage,
    SessionState,
)
from app.modules.session.state_machine import advance_on_pre_check_load, transition


OTP_RATE_LIMIT_SECONDS = 60
OTP_LIFETIME_SECONDS = 600  # 10 minutes
OTP_MAX_ATTEMPTS = 3


async def create_session(
    db: AsyncSession,
    *,
    assignment: CandidateJobAssignment,
    stage: JobPipelineStage,
    otp_required: bool,
    user: UserContext,
) -> Session:
    """Insert a sessions row at state='created'.

    Caller (scheduler.send_invite) provides the already-loaded assignment + stage
    so this function does not re-query. `otp_required` is the *final* flag —
    callers are responsible for applying the stage-default/invite-override
    resolution before calling here.
    """
    sess = Session(
        tenant_id=assignment.tenant_id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        otp_required=otp_required,
        created_by=user.user.id,
    )
    db.add(sess)
    await db.flush()
    return sess


async def mint_token(
    db: AsyncSession,
    *,
    session: Session,
    candidate_id: UUID,
) -> tuple[str, CandidateSessionToken]:
    """Mint a candidate JWT + insert the matching candidate_session_tokens row.

    Returns (token_str, token_row). The caller is responsible for any
    state-machine / audit logging.
    """
    jti = uuid.uuid4()
    token_str, expires_at = create_candidate_token(
        jti=jti,
        candidate_id=candidate_id,
        session_id=session.id,
        tenant_id=session.tenant_id,
    )
    # Use Python-side wall-clock timestamp so consecutive mints within the
    # same transaction get strictly increasing `issued_at` values. PG's
    # `NOW()` (server_default) returns transaction start time and would tie
    # a resend's new token with the prior — breaking ordered-history reads.
    row = CandidateSessionToken(
        jti=jti,
        tenant_id=session.tenant_id,
        session_id=session.id,
        issued_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return token_str, row


async def supersede_token(
    db: AsyncSession,
    *,
    prior: CandidateSessionToken,
    successor: CandidateSessionToken,
) -> None:
    """Mark `prior` as superseded by `successor`. Caller flushes.

    Idempotent — if prior.superseded_at is already set, leaves it alone.
    """
    if prior.superseded_at is not None:
        return
    prior.superseded_at = datetime.now(UTC)
    prior.superseded_by = successor.jti
    await db.flush()


async def _load_session_or_404(db: AsyncSession, session_id: UUID) -> Session:
    result = await db.execute(select(Session).where(Session.id == session_id))
    sess = result.scalar_one_or_none()
    if sess is None:
        raise SessionNotFoundError()
    return sess


async def get_pre_check_context(
    db: AsyncSession, session_id: UUID
) -> PreCheckResponse:
    """Load the session + contextual info for the candidate-facing /pre-check endpoint.

    Advances state created → pre_check on first load (monotonic — no regression
    from any later state). Emits `session.pre_check_loaded` audit event ONLY on
    the first transition (idempotent loads don't spam the audit log).
    """
    sess = await _load_session_or_404(db, session_id)
    prior_state = SessionState(sess.state)
    new_state = advance_on_pre_check_load(prior_state)

    if new_state != prior_state:
        sess.state = new_state.value
        sess.state_changed_at = datetime.now(UTC)
        await db.flush()
        await log_event(
            db,
            tenant_id=sess.tenant_id,
            actor_id=None,      # candidate-driven; no Supabase user
            actor_email=None,
            action="session.pre_check_loaded",
            resource="session",
            resource_id=sess.id,
            payload={},
        )

    # Resolve presentation context
    stage = (await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == sess.stage_id)
    )).scalar_one()
    assignment = (await db.execute(
        select(CandidateJobAssignment)
        .where(CandidateJobAssignment.id == sess.assignment_id)
    )).scalar_one()
    job = (await db.execute(
        select(JobPosting).where(JobPosting.id == assignment.job_posting_id)
    )).scalar_one()
    company_profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    company_name = (company_profile or {}).get("name") or ""

    return PreCheckResponse(
        session_id=sess.id,
        company_name=company_name,
        job_title=job.title,
        stage_name=stage.name,
        duration_minutes=stage.duration_minutes,
        consent_text=_CONSENT_TEXT,
        state=SessionState(sess.state),
        otp_required=sess.otp_required,
        otp_verified_at=sess.otp_verified_at,
    )


_CONSENT_TEXT = (
    "I consent to this interview being recorded and reviewed by the hiring team. "
    "I understand this is an AI-led interview and my responses will be analyzed. "
    "I understand I can withdraw at any time before the interview starts."
)


async def record_consent(
    db: AsyncSession,
    *,
    session_id: UUID,
    user_agent: str,
    ip_address: str | None,
) -> None:
    """Stamp consent_recorded_at and transition pre_check → consented.

    Idempotent — if already consented, refreshes nothing (AIVIA record must
    preserve the original timestamp).
    """
    sess = await _load_session_or_404(db, session_id)
    if sess.state == SessionState.CONSENTED.value:
        return  # Idempotent — no re-stamp
    if sess.state != SessionState.PRE_CHECK.value:
        raise InvalidSessionStateError(
            f"Cannot consent from state={sess.state!r}"
        )

    sess.state = transition(SessionState.PRE_CHECK, SessionState.CONSENTED).value
    sess.consent_recorded_at = datetime.now(UTC)
    sess.state_changed_at = datetime.now(UTC)
    await db.flush()

    await log_event(
        db,
        tenant_id=sess.tenant_id,
        actor_id=None,
        actor_email=None,
        action="session.consent_recorded",
        resource="session",
        resource_id=sess.id,
        payload={"user_agent": user_agent, "ip": ip_address},
    )


async def request_otp(db: AsyncSession, session_id: UUID) -> str:
    """Generate + hash + persist a fresh OTP. Returns plaintext code for email dispatch.

    Rate-limited: rejects if `now() - otp_issued_at < 60s`.
    Resets `otp_attempts = 0` on every new issuance.
    Emits `session.otp_issued` audit event.
    """
    sess = await _load_session_or_404(db, session_id)
    now = datetime.now(UTC)

    if sess.otp_issued_at is not None:
        elapsed = (now - sess.otp_issued_at).total_seconds()
        if elapsed < OTP_RATE_LIMIT_SECONDS:
            retry_after = int(OTP_RATE_LIMIT_SECONDS - elapsed)
            raise OtpRateLimitedError(retry_after_seconds=retry_after)

    code = generate_code()
    sess.otp_hash = hash_code(code)
    sess.otp_issued_at = now
    sess.otp_attempts = 0
    sess.otp_verified_at = None  # new code → prior verification invalid
    await db.flush()

    await log_event(
        db,
        tenant_id=sess.tenant_id,
        actor_id=None,
        actor_email=None,
        action="session.otp_issued",
        resource="session",
        resource_id=sess.id,
        payload={},
    )
    return code


async def verify_otp(db: AsyncSession, *, session_id: UUID, code: str) -> None:
    """Verify candidate-supplied OTP. Emits audit on verify + each failure.

    Order of checks (matters for error surface):
      1. No active code?   → InvalidOtpError(attempts_remaining=OTP_MAX_ATTEMPTS)
      2. Expired?          → wipe + OtpExpiredError
      3. Match?            → wipe + stamp otp_verified_at + success
      4. Mismatch?
         - attempts+1 == MAX → wipe + OtpMaxAttemptsReachedError
         - else              → keep hash, InvalidOtpError(attempts_remaining)
    """
    sess = await _load_session_or_404(db, session_id)
    now = datetime.now(UTC)

    if sess.otp_hash is None or sess.otp_issued_at is None:
        raise InvalidOtpError(attempts_remaining=OTP_MAX_ATTEMPTS)
    if (now - sess.otp_issued_at).total_seconds() > OTP_LIFETIME_SECONDS:
        sess.otp_hash = None
        await db.flush()
        await _log_otp_failure(db, sess, reason="expired", attempts=sess.otp_attempts)
        # Commit the hash wipe BEFORE raising — the surrounding session.begin()
        # context rolls back on exception, which would otherwise discard the
        # side-effect we need to persist.
        await db.commit()
        raise OtpExpiredError()

    if verify_code(code, sess.otp_hash):
        sess.otp_hash = None
        sess.otp_verified_at = now
        await db.flush()
        await log_event(
            db,
            tenant_id=sess.tenant_id,
            actor_id=None,
            actor_email=None,
            action="session.otp_verified",
            resource="session",
            resource_id=sess.id,
            payload={"attempts_consumed": sess.otp_attempts},
        )
        return

    # Mismatch — commit the increment (and hash wipe on max-attempts) BEFORE
    # raising. The surrounding session.begin() context rolls back on exception,
    # which would otherwise lose the otp_attempts increment and make every
    # failed attempt look like the first.
    sess.otp_attempts = (sess.otp_attempts or 0) + 1
    if sess.otp_attempts >= OTP_MAX_ATTEMPTS:
        sess.otp_hash = None
        await db.flush()
        await _log_otp_failure(db, sess, reason="max_attempts", attempts=sess.otp_attempts)
        await db.commit()
        raise OtpMaxAttemptsReachedError()

    await db.flush()
    await _log_otp_failure(db, sess, reason="invalid", attempts=sess.otp_attempts)
    await db.commit()
    raise InvalidOtpError(attempts_remaining=OTP_MAX_ATTEMPTS - sess.otp_attempts)


async def _log_otp_failure(
    db: AsyncSession, sess: Session, *, reason: str, attempts: int
) -> None:
    await log_event(
        db,
        tenant_id=sess.tenant_id,
        actor_id=None,
        actor_email=None,
        action="session.otp_verification_failed",
        resource="session",
        resource_id=sess.id,
        payload={"reason": reason, "attempts_consumed": attempts},
    )


async def start_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    jti: uuid.UUID,
    ip_address: str | None,
    user_agent: str | None,
) -> str:
    """Atomic single-use start.

    Returns 'pending' — the router converts this to a 501 LIVEKIT_INTEGRATION_PENDING.
    When LiveKit wires in Phase 3D, this function returns room credentials instead.

    Raises:
        IllegalStartStateError — state != 'consented'
        OtpRequiredError        — otp_required but otp_verified_at is None
        TokenAlreadyUsedError   — atomic UPDATE matched 0 rows (replay or expired/superseded)
    """
    sess = await _load_session_or_404(db, session_id)

    # Replay disambiguation: if the session has already progressed past
    # 'consented' *and* this exact token row has `used_at` set, the caller
    # is re-posting a previously-consumed single-use token. That is a
    # distinct failure mode from "wrong state for this action" — surface
    # it as TOKEN_ALREADY_USED (409) rather than INVALID_SESSION_STATE
    # so clients can distinguish the two on the wire.
    if sess.state != SessionState.CONSENTED.value:
        token_row = (await db.execute(
            select(CandidateSessionToken).where(CandidateSessionToken.jti == jti)
        )).scalar_one_or_none()
        if token_row is not None and token_row.used_at is not None:
            await log_event(
                db,
                tenant_id=sess.tenant_id,
                actor_id=None,
                actor_email=None,
                action="session.token_replay_blocked",
                resource="session",
                resource_id=sess.id,
                payload={"jti": str(jti), "ip": ip_address, "ua": user_agent},
            )
            # Commit the audit row before raising — the outer session.begin()
            # rolls back on exception, which would otherwise discard the
            # audit trail of the replay attempt.
            await db.commit()
            raise TokenAlreadyUsedError()
        raise IllegalStartStateError()

    if sess.otp_required and sess.otp_verified_at is None:
        raise OtpRequiredError()

    # Atomic single-use — the load-bearing invariant
    result = await db.execute(
        update(CandidateSessionToken)
        .where(
            CandidateSessionToken.jti == jti,
            CandidateSessionToken.used_at.is_(None),
            CandidateSessionToken.expires_at > datetime.now(UTC),
            CandidateSessionToken.superseded_at.is_(None),
        )
        .values(
            used_at=datetime.now(UTC),
            used_ip=ip_address,
            used_user_agent=user_agent,
        )
        .returning(CandidateSessionToken.jti)
    )
    updated_jti = result.scalar_one_or_none()
    if updated_jti is None:
        await log_event(
            db,
            tenant_id=sess.tenant_id,
            actor_id=None,
            actor_email=None,
            action="session.token_replay_blocked",
            resource="session",
            resource_id=sess.id,
            payload={"jti": str(jti), "ua": user_agent, "ip": ip_address},
        )
        # Commit the audit row before raising — see rationale above.
        await db.commit()
        raise TokenAlreadyUsedError()

    # Transition → active
    sess.state = transition(SessionState.CONSENTED, SessionState.ACTIVE).value
    sess.started_at = datetime.now(UTC)
    sess.state_changed_at = datetime.now(UTC)
    await db.flush()

    await log_event(
        db,
        tenant_id=sess.tenant_id,
        actor_id=None,
        actor_email=None,
        action="session.token_used",
        resource="session",
        resource_id=sess.id,
        payload={"jti": str(jti), "ip": ip_address},
    )
    return "pending"


async def get_session(
    db: AsyncSession, *, session_id: UUID
) -> SessionDetailResponse:
    """Load a session + its stage name for recruiter-side detail views."""
    sess = await _load_session_or_404(db, session_id)
    stage_name = (await db.execute(
        select(JobPipelineStage.name).where(JobPipelineStage.id == sess.stage_id)
    )).scalar_one()
    return SessionDetailResponse(
        id=sess.id,
        assignment_id=sess.assignment_id,
        stage_id=sess.stage_id,
        stage_name=stage_name,
        state=SessionState(sess.state),
        state_changed_at=sess.state_changed_at,
        otp_required=sess.otp_required,
        consent_recorded_at=sess.consent_recorded_at,
        scheduled_for=sess.scheduled_for,
        started_at=sess.started_at,
        completed_at=sess.completed_at,
        created_at=sess.created_at,
    )


async def list_sessions(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filters: dict,
    offset: int = 0,
    limit: int = 50,
) -> SessionListPage:
    """List sessions with filters: assignment_id, state, created_after, created_before.

    Batch-loads stage names via a single IN query to avoid N+1.
    """
    from sqlalchemy import func

    base = select(Session).where(Session.tenant_id == tenant_id)
    if (aid := filters.get("assignment_id")) is not None:
        base = base.where(Session.assignment_id == aid)
    if (st := filters.get("state")) is not None:
        base = base.where(Session.state == st)
    if (after := filters.get("created_after")) is not None:
        base = base.where(Session.created_at >= after)
    if (before := filters.get("created_before")) is not None:
        base = base.where(Session.created_at <= before)

    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar_one()

    rows = list((await db.execute(
        base.order_by(Session.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all())

    # Batch-load stage names
    stage_ids = {r.stage_id for r in rows}
    stage_names: dict[UUID, str] = {}
    if stage_ids:
        stage_names = dict((await db.execute(
            select(JobPipelineStage.id, JobPipelineStage.name)
            .where(JobPipelineStage.id.in_(stage_ids))
        )).all())

    items = [
        SessionDetailResponse(
            id=r.id,
            assignment_id=r.assignment_id,
            stage_id=r.stage_id,
            stage_name=stage_names.get(r.stage_id, ""),
            state=SessionState(r.state),
            state_changed_at=r.state_changed_at,
            otp_required=r.otp_required,
            consent_recorded_at=r.consent_recorded_at,
            scheduled_for=r.scheduled_for,
            started_at=r.started_at,
            completed_at=r.completed_at,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return SessionListPage(items=items, total=total, offset=offset, limit=limit)
