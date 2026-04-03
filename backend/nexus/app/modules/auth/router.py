"""Auth endpoints — invite verification, invite claim, and user profile."""

import hashlib
import uuid as uuid_mod

import sqlalchemy
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.models import Company, User, UserInvite
from app.modules.auth.schemas import (
    CompleteInviteRequest,
    CompleteInviteResponse,
    MeResponse,
    VerifyInviteResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/verify-invite", response_model=VerifyInviteResponse)
async def verify_invite(
    token: str = Query(..., description="Raw invite token from email URL"),
    db: AsyncSession = Depends(get_bypass_db),
) -> VerifyInviteResponse:
    """Verify an invite token and return invite details.

    Public endpoint — no auth required. Used by the /invite page to
    validate the token before showing the signup form.

    Uses bypass_rls because this is a cross-tenant read — the RLS
    tenant_isolation policy requires app.current_tenant to be set,
    but this endpoint has no tenant context (it's public). The bypass
    only grants SELECT access; this endpoint performs no writes.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    result = await db.execute(
        select(UserInvite, Company)
        .join(Company, UserInvite.tenant_id == Company.id)
        .where(
            UserInvite.token_hash == token_hash,
            UserInvite.status == "pending",
            UserInvite.expires_at > sqlalchemy.func.now(),
        )
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired invite")

    invite, company = row
    return VerifyInviteResponse(
        email=invite.email,
        role=invite.role,
        company_name=company.name,
    )


@router.post("/complete-invite", response_model=CompleteInviteResponse)
async def complete_invite(
    data: CompleteInviteRequest,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> CompleteInviteResponse:
    """Claim an invite token and create the user row.

    Called after supabase.auth.signUp() succeeds. The JWT from signup
    contains tenant_id and app_role injected by the auth hook.

    CRITICAL: Both the UPDATE (invite → accepted) and INSERT (user row)
    happen inside the same transaction. If INSERT fails, the invite
    reverts to 'pending' — no orphaned consumed invites.
    """
    token_payload = request.state.token_payload
    oauth_email = token_payload.email
    auth_user_id = uuid_mod.UUID(token_payload.sub)  # str → UUID for ORM

    token_hash = hashlib.sha256(data.raw_token.encode()).hexdigest()

    # Atomic single-use claim: UPDATE-WHERE-RETURNING
    # Uses raw SQL for the atomic UPDATE, then ORM for the INSERT.
    # Both are inside the same session.begin() transaction — if INSERT
    # fails, the UPDATE rolls back and the invite stays 'pending'.
    result = await db.execute(
        sqlalchemy.text("""
            UPDATE public.user_invites
               SET status = 'accepted', accepted_at = NOW()
             WHERE token_hash  = :token_hash
               AND status      = 'pending'
               AND expires_at  > NOW()
            RETURNING id, tenant_id, email, role
        """),
        {"token_hash": token_hash},
    )
    claimed_row = result.first()

    if not claimed_row:
        raise HTTPException(status_code=401, detail="Invalid or expired invite")

    # Email match enforcement
    if claimed_row.email != oauth_email:
        raise HTTPException(status_code=401, detail="Email mismatch — invite was for a different address")

    # Create the user row (tenant_id from raw SQL is str — convert to UUID)
    user = User(
        auth_user_id=auth_user_id,
        tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
        email=oauth_email,
        role=claimed_row.role,
    )
    db.add(user)
    await db.flush()

    # Company Admin → /onboarding, everyone else → /
    redirect_to = "/onboarding" if claimed_row.role == "Company Admin" else "/"

    logger.info(
        "auth.invite_completed",
        user_id=str(user.id),
        tenant_id=str(claimed_row.tenant_id),
        role=claimed_row.role,
    )

    return CompleteInviteResponse(
        redirect_to=redirect_to,
        user_id=str(user.id),
        tenant_id=str(claimed_row.tenant_id),
        role=claimed_row.role,
    )


@router.get("/me", response_model=MeResponse)
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> MeResponse:
    """Return the current user's profile and company info.

    Called by the dashboard layout to check onboarding_complete.
    Uses bypass_rls because the user may not have a tenant_id set
    in the RLS context yet (e.g., during onboarding).
    """
    token_payload = request.state.token_payload
    auth_user_id = token_payload.sub

    result = await db.execute(
        select(User, Company)
        .join(Company, User.tenant_id == Company.id)
        .where(User.auth_user_id == auth_user_id, User.is_active == True)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user, company = row

    return MeResponse(
        user_id=str(user.id),
        auth_user_id=str(user.auth_user_id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        tenant_id=str(user.tenant_id),
        company_name=company.name,
        onboarding_complete=company.onboarding_complete,
    )
