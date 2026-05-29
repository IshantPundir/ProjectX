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

import contextlib
import uuid
from datetime import datetime, timedelta, UTC
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.audit import log_event
from app.modules.auth import UserContext, create_candidate_token
from app.modules.candidates import Candidate, CandidateJobAssignment
from app.modules.jd import JobPosting
from app.modules.org_units import OrganizationalUnit, find_company_profile_in_ancestry
from app.modules.pipelines import JobPipelineStage
from app.modules.session.models import CandidateSessionToken, Session
from app.modules.session.errors import (
    AgentDispatchFailedError,
    IllegalStartStateError,
    InvalidOtpError,
    InvalidSessionStateError,
    OtpExpiredError,
    OtpMaxAttemptsReachedError,
    OtpRateLimitedError,
    OtpRequiredError,
    SessionNotFoundError,
    SessionNotRejoinableError,
    TokenAlreadyUsedError,
)
from app.modules.session.livekit import (
    build_room_egress,
    cancel_room,
    create_room,
    dispatch_agent,
    mint_candidate_lk_token,
    recording_object_key,
)
from app.modules.session.otp import generate_code, hash_code, verify_code
from app.modules.session.error_codes import ErrorCode
from app.modules.session.proctoring import classify_severity, decide_termination
from app.modules.session.schemas import (
    AudioProcessingHints,
    PreCheckResponse,
    ProctoringConfig,
    ProctoringEventResult,
    SessionDetailResponse,
    SessionListPage,
    SessionState,
    StartSessionResponse,
)
from app.modules.session.state_machine import advance_on_pre_check_load, transition
from app.modules.tenant_settings import get_tenant_settings


log = structlog.get_logger("session.service")

OTP_RATE_LIMIT_SECONDS = 60
OTP_LIFETIME_SECONDS = 600  # 10 minutes
OTP_MAX_ATTEMPTS = 3


def _compute_audio_processing_hints() -> AudioProcessingHints:
    """Browser-side audio constraints for the candidate session.

    Server-side NC (ai-coustics or Krisp) is always on per the
    audio-pipeline architecture (no self-hosted fallback).
    Browser noiseSuppression is therefore always OFF (let the ML
    model see raw audio); EC and AGC stay ON (load-bearing for
    full-duplex).
    """
    return AudioProcessingHints(
        noise_suppression=False,
        echo_cancellation=True,
        auto_gain_control=True,
    )


async def _build_proctoring_config(
    db: AsyncSession, tenant_id: UUID
) -> ProctoringConfig:
    settings_ = await get_tenant_settings(db, tenant_id)
    return ProctoringConfig(
        enabled=settings_.proctoring_enabled,
        soft_violation_limit=settings_.proctoring_soft_violation_limit,
        fullscreen_grace_seconds=settings_.proctoring_fullscreen_grace_seconds,
    )


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

    proctoring = await _build_proctoring_config(db, sess.tenant_id)

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
        otp_issued_at=sess.otp_issued_at,
        proctoring_enabled=proctoring.enabled,
        proctoring_outcome=sess.proctoring_outcome,
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
) -> StartSessionResponse:
    """Atomic LiveKit-provisioning start.

    Order-of-operations (Phase 3 simplified — no engine JWT):
        1. Load + state-gate + OTP-gate the session.
        2. Mint LiveKit candidate JWT (sync, no DB).
        3. Dispatch agent to LiveKit. On failure → AgentDispatchFailedError;
           no DB rollback needed since no engine token row was ever written.
           Candidate token unconsumed; candidate can retry.
        4. Atomically consume candidate_session_tokens.used_at. On rowcount=0
           (concurrent /start consumed it first), best-effort cancel_room
           and raise TokenAlreadyUsedError.
        5. Transition session → active, stamp livekit_room_name +
           started_at, audit 'session.token_used'.

    Raises:
        SessionNotFoundError    — session_id not found.
        IllegalStartStateError  — state != 'consented' AND token not yet used.
        TokenAlreadyUsedError   — state != 'consented' AND token already used,
                                  OR atomic consume races and loses.
        OtpRequiredError        — otp_required=True but otp_verified_at is None.
        AgentDispatchFailedError — LiveKit dispatch raised. Token preserved.
    """
    sess = await _load_session_or_404(db, session_id)

    # Replay disambiguation — preserved from the prior implementation.
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
            await db.commit()
            raise TokenAlreadyUsedError()
        raise IllegalStartStateError()

    if sess.otp_required and sess.otp_verified_at is None:
        raise OtpRequiredError()

    # Load candidate (for AccessToken display name) and stage (for TTL math).
    assignment = (await db.execute(
        select(CandidateJobAssignment).where(
            CandidateJobAssignment.id == sess.assignment_id
        )
    )).scalar_one()
    candidate = (await db.execute(
        select(Candidate).where(Candidate.id == assignment.candidate_id)
    )).scalar_one()
    stage = (await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == sess.stage_id)
    )).scalar_one()

    correlation_id = str(uuid.uuid4())
    room_name = f"session-{sess.id}"
    duration_minutes = stage.duration_minutes or 30

    candidate_lk_token = mint_candidate_lk_token(
        room_name=room_name,
        identity=f"candidate-{candidate.id}",
        name=candidate.name or "",
        ttl_minutes=duration_minutes + 10,
    )
    # Pre-create the room with our short empty_timeout instead of letting
    # agent_dispatch auto-create it with LiveKit's 5-minute default. When
    # recording is enabled, attach auto-egress so LiveKit starts the
    # recording when the first participant joins (no empty-room race).
    #
    # Recording is BEST-EFFORT: if the egress config is rejected, we retry
    # room creation WITHOUT it so the interview still proceeds (candidate
    # availability outranks recording completeness). A non-egress room
    # failure (e.g. LiveKit outage) still raises → AgentDispatchFailedError,
    # token preserved, candidate can retry.
    recording_requested = False
    recording_key: str | None = None
    try:
        egress_cfg = None
        if settings.recording_enabled:
            try:
                egress_cfg, recording_key = build_room_egress(
                    tenant_id=sess.tenant_id, session_id=sess.id
                )
            except Exception:
                log.warning(
                    "session.recording_config_failed",
                    session_id=str(sess.id),
                    exc_info=True,
                )
                egress_cfg = None
        try:
            await create_room(room_name=room_name, egress=egress_cfg)
            recording_requested = egress_cfg is not None
        except Exception:
            if egress_cfg is None:
                raise
            # Egress config may be the culprit — retry plain so the interview
            # still works; recording is stamped 'failed' below.
            log.warning(
                "session.recording_room_create_failed",
                session_id=str(sess.id),
                exc_info=True,
            )
            await create_room(room_name=room_name)
            recording_requested = False
        await dispatch_agent(
            room_name=room_name,
            session_id=sess.id,
            tenant_id=sess.tenant_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        # Transaction will roll back on raise. Candidate token unconsumed.
        raise AgentDispatchFailedError(detail=str(exc)) from exc

    # Atomic single-use consume — load-bearing invariant.
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
    if result.scalar_one_or_none() is None:
        # Race lost — another /start consumed the token between our state
        # gate and this UPDATE. Best-effort room cleanup.
        with contextlib.suppress(Exception):
            await cancel_room(room_name)
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
        await db.commit()
        raise TokenAlreadyUsedError()

    # Transition → active.
    sess.state = transition(SessionState.CONSENTED, SessionState.ACTIVE).value
    sess.started_at = datetime.now(UTC)
    sess.state_changed_at = datetime.now(UTC)
    sess.livekit_room_name = room_name
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

    # Stamp recording state decided during room provisioning above. The egress
    # itself is started by LiveKit when the first participant joins (auto
    # egress); the egress id is filled in later by pull-based reconcile on the
    # report-page read. Consent is timestamped before /start, so recording is
    # post-consent (AIVIA-compliant).
    if settings.recording_enabled:
        sess.recording_status = "recording" if recording_requested else "failed"
        sess.recording_s3_key = recording_key or recording_object_key(
            tenant_id=sess.tenant_id, session_id=sess.id
        )
        if recording_requested:
            sess.recording_started_at = datetime.now(UTC)
        await db.flush()

    proctoring = await _build_proctoring_config(db, sess.tenant_id)

    return StartSessionResponse(
        livekit_url=settings.livekit_public_url or settings.livekit_url,
        livekit_token=candidate_lk_token,
        room_name=room_name,
        session_id=sess.id,
        audio_processing_hints=_compute_audio_processing_hints(),
        proctoring=proctoring,
    )


async def rejoin_session(
    db: AsyncSession,
    *,
    session_id: UUID,
) -> StartSessionResponse:
    """Mint a fresh LiveKit access token for a candidate rejoining an active session.

    Differences from start_session:
      * No engine dispatch — engine is already in the room.
      * No candidate-token state machine consume — that happened on /start.
      * No state transition — session stays 'active'.
      * Idempotent on repeat calls within the JWT lifetime.

    Raises:
        SessionNotRejoinableError: session.state != 'active'.

    Rate limit (enforced at router): 5/hour per token, 3/min per IP.
    """
    session = await _load_session_or_404(db, session_id)

    if session.state != SessionState.ACTIVE.value:
        raise SessionNotRejoinableError(current_state=session.state)

    # Load candidate to derive the same identity scheme used in start_session.
    # start_session uses: identity=f"candidate-{candidate.id}"
    # We must use the identical scheme so the rejoining participant matches the
    # existing LiveKit identity — engine state re-attaches and
    # DUPLICATE_IDENTITY fires correctly on multi-tab.
    assignment = (await db.execute(
        select(CandidateJobAssignment).where(
            CandidateJobAssignment.id == session.assignment_id
        )
    )).scalar_one()
    stage = (await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == session.stage_id)
    )).scalar_one()
    candidate = (await db.execute(
        select(Candidate).where(Candidate.id == assignment.candidate_id)
    )).scalar_one()

    duration_minutes = stage.duration_minutes or 30

    new_lk_token = mint_candidate_lk_token(
        room_name=session.livekit_room_name,
        identity=f"candidate-{candidate.id}",
        name=candidate.name or "",
        ttl_minutes=duration_minutes + 10,
    )

    await log_event(
        db,
        tenant_id=session.tenant_id,
        actor_id=None,      # candidate-driven; no Supabase user
        actor_email=None,
        action="session.candidate_rejoined",
        resource="session",
        resource_id=session.id,
        payload={},
    )

    proctoring = await _build_proctoring_config(db, session.tenant_id)

    return StartSessionResponse(
        livekit_url=settings.livekit_public_url or settings.livekit_url,
        livekit_token=new_lk_token,
        room_name=session.livekit_room_name,
        session_id=session.id,
        audio_processing_hints=_compute_audio_processing_hints(),
        proctoring=proctoring,
    )


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


async def transition_to_error(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    error_code: ErrorCode,
    correlation_id: str,
    reason: Literal["engine_entrypoint", "reaper"],
) -> bool:
    """Atomic state -> 'error' transition. Returns True if this call won.

    Gated on state IN ('consented', 'active') — the only states where the
    engine could be running. Sessions in 'created' or 'pre_check' were
    never dispatched to LiveKit so there is no engine to error-out; they
    should be cancelled through the normal scheduler path instead.
    Sessions already in 'completed', 'cancelled', or 'error' are terminal
    and must not be clobbered. The boolean return lets the reaper
    distinguish 'I just claimed this stuck row' from 'someone else
    transitioned it first.'

    Caller MUST be on a bypass-RLS session. Audit row is written through
    log_event in the same transaction; the caller flushes/commits.
    """
    now = datetime.now(UTC)
    res = await db.execute(
        update(Session)
        .where(
            Session.id == session_id,
            Session.tenant_id == tenant_id,
            Session.state.in_(["consented", "active"]),
        )
        .values(
            state="error",
            error_code=error_code,
            state_changed_at=now,
        )
    )
    if res.rowcount == 0:
        return False

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=None,
        actor_email=None,
        action="session.errored",
        resource="session",
        resource_id=session_id,
        payload={
            "error_code": error_code,
            "reason": reason,
            "correlation_id": correlation_id,
        },
    )
    return True


async def record_proctoring_event(
    db: AsyncSession,
    *,
    session_id: UUID,
    tenant_id: UUID,
    kind: str,
    occurred_at: datetime,
    correlation_id: str,
) -> ProctoringEventResult:
    """Record one proctoring violation and decide termination (authoritative).

    * Loads the session for id + tenant_id (cross-tenant → 404, same opacity
      as /state).
    * If the session is not 'active', returns an idempotent terminal success
      (a violation arriving after the session already ended is a no-op).
    * Appends {kind, severity, occurred_at} to sessions.proctoring_violations.
    * Terminal on a hard kind OR when cumulative soft count > the tenant's
      proctoring_soft_violation_limit. On termination: stamp proctoring_outcome,
      transition active → terminated, best-effort cancel_room, audit.
    """
    sess = (
        await db.execute(
            select(Session).where(Session.id == session_id, Session.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise SessionNotFoundError()

    existing = list(sess.proctoring_violations or [])

    if sess.state != SessionState.ACTIVE.value:
        soft = sum(1 for v in existing if v.get("severity") == "soft")
        return ProctoringEventResult(
            terminated=True,
            violation_count=len(existing),
            soft_violation_count=soft,
            already_terminal=True,
        )

    severity = classify_severity(kind)
    violations = existing + [
        {"kind": kind, "severity": severity, "occurred_at": occurred_at.isoformat()}
    ]
    soft_count = sum(1 for v in violations if v.get("severity") == "soft")

    tenant_settings = await get_tenant_settings(db, tenant_id)
    terminal, outcome = decide_termination(
        kind=kind,
        soft_count_including_new=soft_count,
        soft_limit=tenant_settings.proctoring_soft_violation_limit,
    )

    # Reassigning a new list marks the JSONB attribute dirty for the flush.
    sess.proctoring_violations = violations
    sess.proctoring_violation_count = len(violations)

    if terminal:
        sess.proctoring_outcome = outcome
        sess.state = transition(SessionState.ACTIVE, SessionState.TERMINATED).value
        sess.state_changed_at = datetime.now(UTC)
        await db.flush()
        if sess.livekit_room_name:
            # DeleteRoom forcibly disconnects every participant (candidate +
            # agent). It's a backstop: the candidate's own CLIENT_INITIATED
            # disconnect already auto-closes the agent session + room (LiveKit
            # default close_on_disconnect). Log the outcome so a genuine
            # failure is visible instead of silently swallowed.
            try:
                await cancel_room(sess.livekit_room_name)
                log.info(
                    "session.proctoring.room_cancelled",
                    session_id=str(sess.id),
                    room=sess.livekit_room_name,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "session.proctoring.room_cancel_failed",
                    session_id=str(sess.id),
                    room=sess.livekit_room_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            actor_email=None,
            action="session.proctoring_terminated",
            resource="session",
            resource_id=sess.id,
            payload={
                "proctoring_outcome": outcome,
                "kind": kind,
                "violation_count": len(violations),
                "correlation_id": correlation_id,
            },
        )
    else:
        await db.flush()
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            actor_email=None,
            action="session.proctoring_violation",
            resource="session",
            resource_id=sess.id,
            payload={
                "kind": kind,
                "severity": severity,
                "violation_count": len(violations),
                "correlation_id": correlation_id,
            },
        )

    return ProctoringEventResult(
        terminated=terminal,
        violation_count=len(violations),
        soft_violation_count=soft_count,
    )
