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
from datetime import datetime, UTC
from uuid import UUID

from sqlalchemy import select
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
    InvalidSessionStateError,
    SessionNotFoundError,
)
from app.modules.session.schemas import PreCheckResponse, SessionState
from app.modules.session.state_machine import advance_on_pre_check_load, transition


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
    row = CandidateSessionToken(
        jti=jti,
        tenant_id=session.tenant_id,
        session_id=session.id,
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
