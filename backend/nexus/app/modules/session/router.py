"""Session module HTTP surface.

Two concerns on one file:
  - /api/candidate-session/{token}/*  — candidate-facing (5 endpoints)
  - /api/sessions/*                    — recruiter-side reads (2 endpoints)

Candidate endpoints rely on `AuthMiddleware` having already extracted the
candidate token payload onto `request.state.candidate_token_payload` (and
rejected with 401 if the JWT signature, expiry, or DB-side JTI lookup
fails). Recruiter endpoints authenticate via the normal Supabase Bearer
flow through the `get_current_user_roles` dependency.

Routers exported:
  * `candidate_session_router` — canonical candidate-facing router.
  * `session_router`            — canonical recruiter-read router.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import Candidate, CandidateJobAssignment, Session as SessionRow
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.notifications.service import render_template, send_email
from app.modules.session import service as session_service
from app.modules.session.schemas import (
    ConsentRequest,
    PreCheckResponse,
    SessionDetailResponse,
    SessionListPage,
    StartSessionResponse,
    VerifyOtpRequest,
)

candidate_session_router = APIRouter(
    prefix="/api/candidate-session/{token}", tags=["candidate-session"]
)
session_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _candidate_session_id(request: Request) -> UUID:
    """Extract session_id from the candidate JWT payload the middleware attached.

    AuthMiddleware already verified signature + expiry + DB JTI presence;
    if the payload is missing here the middleware ordering is broken.
    """
    payload = request.state.candidate_token_payload
    return payload.session_id


# --- Candidate-facing endpoints ---------------------------------------------


@candidate_session_router.get("/pre-check", response_model=PreCheckResponse)
async def get_pre_check_endpoint(
    request: Request,
    token: str,  # consumed by middleware — declared so FastAPI routes correctly
    db: AsyncSession = Depends(get_tenant_db),
) -> PreCheckResponse:
    """Load session context for the pre-check wizard step.

    Monotonically advances `created → pre_check` on first load; idempotent
    on subsequent loads (any later state is preserved).
    """
    return await session_service.get_pre_check_context(
        db, session_id=_candidate_session_id(request)
    )


@candidate_session_router.post("/consent", status_code=status.HTTP_204_NO_CONTENT)
async def post_consent_endpoint(
    request: Request,
    token: str,
    body: ConsentRequest,
    db: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """Record AIVIA-compliant consent event with timestamp + UA + IP."""
    ip = request.client.host if request.client else None
    await session_service.record_consent(
        db,
        session_id=_candidate_session_id(request),
        user_agent=body.user_agent,
        ip_address=ip,
    )
    return Response(status_code=204)


@candidate_session_router.post(
    "/request-otp", status_code=status.HTTP_204_NO_CONTENT
)
async def post_request_otp_endpoint(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """Generate a fresh OTP + email it to the candidate.

    The service layer handles rate limiting (60s) and audit logging. We
    resolve the recipient email here via session → assignment → candidate
    because notifications need the `to:` address — the service is email-
    dispatch-agnostic by design.
    """
    session_id = _candidate_session_id(request)
    code = await session_service.request_otp(db, session_id=session_id)

    sess = (await db.execute(
        select(SessionRow).where(SessionRow.id == session_id)
    )).scalar_one()
    assignment = (await db.execute(
        select(CandidateJobAssignment)
        .where(CandidateJobAssignment.id == sess.assignment_id)
    )).scalar_one()
    candidate = (await db.execute(
        select(Candidate).where(Candidate.id == assignment.candidate_id)
    )).scalar_one()

    # Render HTML via the notifications module's template loader — same
    # pattern as scheduler/service.py for interview_invite.
    html = render_template("otp_code.html", otp_code=code)
    await send_email(
        to=candidate.email or "",
        subject="Your interview access code",
        html=html,
    )
    return Response(status_code=204)


@candidate_session_router.post(
    "/verify-otp", status_code=status.HTTP_204_NO_CONTENT
)
async def post_verify_otp_endpoint(
    request: Request,
    token: str,
    body: VerifyOtpRequest,
    db: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """Verify the candidate-supplied OTP code.

    Errors map to 422/429 via main.py global handlers (Task 3C.1.19):
      * InvalidOtpError           → 422 INVALID_OTP (with attempts_remaining)
      * OtpExpiredError           → 422 OTP_EXPIRED
      * OtpMaxAttemptsReachedError → 422 OTP_MAX_ATTEMPTS_REACHED
    """
    await session_service.verify_otp(
        db, session_id=_candidate_session_id(request), code=body.code,
    )
    return Response(status_code=204)


@candidate_session_router.post(
    "/start",
    status_code=status.HTTP_200_OK,
    response_model=StartSessionResponse,
)
async def post_start_endpoint(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_tenant_db),
) -> StartSessionResponse:
    """Provision LiveKit room, dispatch agent, atomically consume the
    single-use token, transition session → active.

    Order-of-operations gives the candidate a usable retry window when
    LiveKit dispatch fails (token NOT consumed → 502 AGENT_DISPATCH_FAILED).
    Replay or race on the consume yields 409 TOKEN_ALREADY_USED.
    """
    payload = request.state.candidate_token_payload
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return await session_service.start_session(
        db,
        session_id=payload.session_id,
        jti=payload.jti,
        ip_address=ip,
        user_agent=ua,
    )


# --- Recruiter-side read endpoints ------------------------------------------


@session_router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session_endpoint(
    session_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> SessionDetailResponse:
    """Return a single session's detail view.

    Gated on `jobs.view`. TODO(Phase-3C): swap to ancestry-walking authz
    that resolves the session → assignment → job → org_unit chain and
    verifies view access in any ancestor unit (mirrors the pattern
    `jd.authz.require_job_access` uses). For now the tenant-wide
    permission check is sufficient since the DB layer still enforces
    tenant isolation via RLS.
    """
    if not user.is_super_admin and "jobs.view" not in user.all_permissions():
        raise HTTPException(status_code=403, detail="jobs.view required")
    return await session_service.get_session(db, session_id=session_id)


@session_router.get("", response_model=SessionListPage)
async def list_sessions_endpoint(
    assignment_id: UUID | None = None,
    state: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> SessionListPage:
    """List sessions visible to the caller's tenant, optionally filtered.

    See `get_session_endpoint` TODO — same ancestry-walk authz applies here
    once the session chain is modelled.
    """
    if not user.is_super_admin and "jobs.view" not in user.all_permissions():
        raise HTTPException(status_code=403, detail="jobs.view required")
    return await session_service.list_sessions(
        db,
        tenant_id=user.user.tenant_id,
        filters={"assignment_id": assignment_id, "state": state},
        offset=offset,
        limit=limit,
    )
