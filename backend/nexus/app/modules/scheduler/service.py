"""Scheduler module service layer.

send_invite        — dispatches a fresh interview invite (creates session + token + email)
resend_invite      — supersedes prior token, resets OTP, resends email (Task 3C.1.16)
revoke_invite      — cancels session + supersedes token (Task 3C.1.16)

Note on `send_email` shape: the notifications module exposes
`send_email(*, to, subject, html)` plus `render_template(name, **ctx)`. We
render HTML locally, then dispatch — the same pattern as
`admin/service.py::provision_client` and `settings/router.py::invite_team_member`.
"""
from __future__ import annotations

from datetime import datetime, UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateSessionToken,
    JobPipelineStage,
    JobPosting,
    Session,
)
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.notifications.service import render_template, send_email
from app.modules.org_units.service import find_company_profile_in_ancestry
from app.modules.scheduler.authz import (
    assert_assignment_active,
    assert_stage_is_ai_interview,
)
from app.modules.scheduler.errors import SessionAlreadyStartedError
from app.modules.scheduler.schemas import InviteCreateRequest, InviteResponse
from app.modules.session import service as session_service
from app.modules.session.schemas import SessionState
from app.modules.session.state_machine import transition


async def send_invite(
    db: AsyncSession,
    request: InviteCreateRequest,
    user: UserContext,
) -> InviteResponse:
    """Dispatch a new interview invite.

    Resolution order:
      1. Load assignment (404 if missing) — FK RLS handles tenant scope.
      2. Guard assignment.status == 'active' (422 ASSIGNMENT_NOT_ACTIVE).
      3. Load current stage; guard stage_type == 'ai_interview'.
      4. Resolve otp_required: request-body override > stage default.
      5. Create session row + mint token.
      6. Dispatch invite email via notifications module.
      7. Audit: session.invite_sent.
    """
    assignment = (await db.execute(
        select(CandidateJobAssignment)
        .where(CandidateJobAssignment.id == request.assignment_id)
    )).scalar_one_or_none()
    if assignment is None:
        from app.modules.candidates.errors import CandidateNotFoundError
        raise CandidateNotFoundError()  # reused 404 — assignment missing ≡ candidate-scope miss

    assert_assignment_active(assignment)

    stage = (await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == assignment.current_stage_id)
    )).scalar_one()
    assert_stage_is_ai_interview(stage)

    otp_required = (
        request.otp_required if request.otp_required is not None
        else stage.otp_required_default
    )

    sess = await session_service.create_session(
        db, assignment=assignment, stage=stage, otp_required=otp_required, user=user,
    )
    token_str, token_row = await session_service.mint_token(
        db, session=sess, candidate_id=assignment.candidate_id,
    )

    candidate = (await db.execute(
        select(Candidate).where(Candidate.id == assignment.candidate_id)
    )).scalar_one()
    job = (await db.execute(
        select(JobPosting).where(JobPosting.id == assignment.job_posting_id)
    )).scalar_one()
    company_profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    company_name = (company_profile or {}).get("name") or "the hiring team"

    html = render_template(
        "interview_invite.html",
        candidate_name=candidate.name or "there",
        company_name=company_name,
        job_title=job.title,
        stage_name=stage.name,
        duration_minutes=stage.duration_minutes,
        invite_url=f"{settings.frontend_base_url}/interview/{token_str}",
        expires_at_pretty=f"in {settings.candidate_jwt_ttl_hours} hours",
    )
    await send_email(
        to=candidate.email or "",
        subject=f"Interview invitation — {job.title}",
        html=html,
    )

    await log_event(
        db,
        tenant_id=sess.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="session.invite_sent",
        resource="session",
        resource_id=sess.id,
        payload={
            "assignment_id": str(assignment.id),
            "stage_id": str(stage.id),
            "otp_required": otp_required,
            "token_jti": str(token_row.jti),
            "recipient_email": candidate.email,
        },
    )

    return InviteResponse(session_id=sess.id, token_expires_at=token_row.expires_at)


async def resend_invite(
    db: AsyncSession,
    *,
    session_id: UUID,
    user: UserContext,
) -> InviteResponse:
    """Supersede live token + reset OTP state + dispatch a new email.

    Rejected when session.state ∈ {active, completed, cancelled, error}.
    Preserves consent_recorded_at (AIVIA record stays with the session,
    not the token). Leaves session.state unchanged.
    """
    sess = (await db.execute(
        select(Session).where(Session.id == session_id)
    )).scalar_one_or_none()
    if sess is None:
        from app.modules.session.errors import SessionNotFoundError
        raise SessionNotFoundError()
    if sess.state in {"active", "completed", "cancelled", "error"}:
        raise SessionAlreadyStartedError()

    # Find live token (unused + not superseded)
    prior = (await db.execute(
        select(CandidateSessionToken)
        .where(
            CandidateSessionToken.session_id == session_id,
            CandidateSessionToken.used_at.is_(None),
            CandidateSessionToken.superseded_at.is_(None),
        )
        .order_by(CandidateSessionToken.issued_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    # Mint new token
    assignment = (await db.execute(
        select(CandidateJobAssignment)
        .where(CandidateJobAssignment.id == sess.assignment_id)
    )).scalar_one()
    token_str, new_token = await session_service.mint_token(
        db, session=sess, candidate_id=assignment.candidate_id,
    )

    # Supersede prior
    if prior is not None:
        await session_service.supersede_token(db, prior=prior, successor=new_token)

    # Reset OTP state; preserve consent_recorded_at
    sess.otp_hash = None
    sess.otp_issued_at = None
    sess.otp_attempts = 0
    sess.otp_verified_at = None
    await db.flush()

    # Resend email
    candidate = (await db.execute(
        select(Candidate).where(Candidate.id == assignment.candidate_id)
    )).scalar_one()
    stage = (await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == sess.stage_id)
    )).scalar_one()
    job = (await db.execute(
        select(JobPosting).where(JobPosting.id == assignment.job_posting_id)
    )).scalar_one()
    company_profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    company_name = (company_profile or {}).get("name") or "the hiring team"

    html = render_template(
        "interview_invite.html",
        candidate_name=candidate.name or "there",
        company_name=company_name,
        job_title=job.title,
        stage_name=stage.name,
        duration_minutes=stage.duration_minutes,
        invite_url=f"{settings.frontend_base_url}/interview/{token_str}",
        expires_at_pretty=f"in {settings.candidate_jwt_ttl_hours} hours",
    )
    await send_email(
        to=candidate.email or "",
        subject=f"Interview invitation (resent) — {job.title}",
        html=html,
    )

    await log_event(
        db,
        tenant_id=sess.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="session.invite_resent",
        resource="session",
        resource_id=sess.id,
        payload={
            "prior_token_jti": str(prior.jti) if prior else None,
            "new_token_jti": str(new_token.jti),
        },
    )
    return InviteResponse(session_id=sess.id, token_expires_at=new_token.expires_at)


async def revoke_invite(
    db: AsyncSession,
    *,
    session_id: UUID,
    user: UserContext,
) -> None:
    """Mark session state → cancelled + supersede live token.

    Idempotent from already-terminal states (completed/cancelled/error): no-op.
    """
    sess = (await db.execute(
        select(Session).where(Session.id == session_id)
    )).scalar_one_or_none()
    if sess is None:
        from app.modules.session.errors import SessionNotFoundError
        raise SessionNotFoundError()

    # Find + supersede live token (no successor — this is a revoke, not a replacement)
    prior = (await db.execute(
        select(CandidateSessionToken)
        .where(
            CandidateSessionToken.session_id == session_id,
            CandidateSessionToken.used_at.is_(None),
            CandidateSessionToken.superseded_at.is_(None),
        )
    )).scalar_one_or_none()
    if prior is not None:
        prior.superseded_at = datetime.now(UTC)
        await db.flush()

    # Transition to cancelled (allowed from created/pre_check/consented)
    current_state = SessionState(sess.state)
    try:
        new_state = transition(current_state, SessionState.CANCELLED).value
    except Exception:
        # Already terminal (completed/cancelled/error) — idempotent no-op.
        return
    sess.state = new_state
    sess.state_changed_at = datetime.now(UTC)
    await db.flush()

    await log_event(
        db,
        tenant_id=sess.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="session.invite_revoked",
        resource="session",
        resource_id=sess.id,
        payload={"revoked_token_jti": str(prior.jti) if prior else None},
    )
