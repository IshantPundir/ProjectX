"""Scheduler module HTTP surface — /api/scheduler/invites/*.

Three recruiter-facing endpoints that drive the invite lifecycle:
  * POST /api/scheduler/invites                       — send a fresh invite
  * POST /api/scheduler/invites/{session_id}/resend   — supersede + resend
  * POST /api/scheduler/invites/{session_id}/revoke   — cancel session + supersede token

All three are guarded by `_require_manage`: super admin bypass, else
requires BOTH `candidates.manage` AND `jobs.manage`. The service layer
(`scheduler.service`) owns the domain invariants (stage-type guard,
assignment-active guard, state-terminal guard for resend); the router is
thin request/response plus the perm check.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.scheduler import service as scheduler_service
from app.modules.scheduler.schemas import InviteCreateRequest, InviteResponse

scheduler_router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


def _require_manage(user: UserContext) -> None:
    """Enforce invite-lifecycle manage permissions.

    Super admin bypasses. Otherwise we require BOTH `candidates.manage`
    AND `jobs.manage` — dispatching an interview invite spans both
    domains (touches a candidate via assignment + consumes a job's
    pipeline stage). Either role alone is insufficient.

    TODO(Phase-3C): swap to ancestry-walking authz once the
    session → assignment → job → org_unit chain is modelled in a dedicated
    `scheduler.authz` module (mirrors `jd.authz.require_job_access`). The
    tenant-wide permission check below is sufficient today because the DB
    layer still enforces tenant isolation via RLS.
    """
    if user.is_super_admin:
        return
    perms = user.all_permissions()
    if "candidates.manage" not in perms or "jobs.manage" not in perms:
        raise HTTPException(
            status_code=403,
            detail="Missing candidates.manage + jobs.manage",
        )


@scheduler_router.post(
    "/invites",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_invite_endpoint(
    body: InviteCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> InviteResponse:
    """Send a new interview invite for an assignment.

    Service layer raises:
      * CandidateNotFoundError         → 404 (assignment missing ≡ scope miss)
      * AssignmentNotActiveError       → 422 ASSIGNMENT_NOT_ACTIVE
      * InvalidStageTypeForInviteError → 422 INVALID_STAGE_TYPE_FOR_INVITE
    All mapped to JSON by main.py global handlers (Task 3C.1.19).
    """
    _require_manage(user)
    return await scheduler_service.send_invite(db, body, user)


@scheduler_router.post(
    "/invites/{session_id}/resend",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_resend_endpoint(
    session_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> InviteResponse:
    """Supersede the live token + reset OTP state + resend the email.

    Rejected when the session is already active/completed/cancelled/error
    (SessionAlreadyStartedError → 409 SESSION_ALREADY_STARTED).
    """
    _require_manage(user)
    return await scheduler_service.resend_invite(
        db, session_id=session_id, user=user
    )


@scheduler_router.post(
    "/invites/{session_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def post_revoke_endpoint(
    session_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Response:
    """Cancel the session + supersede the live token.

    Idempotent from already-terminal states (completed/cancelled/error):
    the service no-ops silently. Successful revoke returns 204.
    """
    _require_manage(user)
    await scheduler_service.revoke_invite(db, session_id=session_id, user=user)
    return Response(status_code=204)
