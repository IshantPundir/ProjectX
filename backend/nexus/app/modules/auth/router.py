"""Auth endpoints — invite verification, invite claim, and user profile."""

import hashlib
import uuid as uuid_mod

import sqlalchemy
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.models import Client, OrganizationalUnit, Role, User, UserInvite, UserRoleAssignment
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import (
    CompleteInviteRequest,
    CompleteInviteResponse,
    MeResponse,
    RoleAssignmentResponse,
    VerifyInviteResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/verify-invite", response_model=VerifyInviteResponse)
async def verify_invite(
    token: str = Query(..., description="Raw invite token from email URL"),
    db: AsyncSession = Depends(get_bypass_db),
) -> VerifyInviteResponse:
    """Verify an invite token. Public endpoint — no auth required."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    result = await db.execute(
        select(UserInvite, Client)
        .join(Client, UserInvite.tenant_id == Client.id)
        .where(
            UserInvite.token_hash == token_hash,
            UserInvite.status == "pending",
            UserInvite.expires_at > sqlalchemy.func.now(),
        )
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired invite")

    invite, client = row
    return VerifyInviteResponse(email=invite.email, client_name=client.name)


@router.post("/complete-invite", response_model=CompleteInviteResponse)
async def complete_invite(
    data: CompleteInviteRequest,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> CompleteInviteResponse:
    """Claim an invite token and create the user row.

    If invited by projectx_admin → sets clients.super_admin_id.
    No role assignment — roles are assigned later via org units.
    """
    token_payload = request.state.token_payload
    oauth_email = token_payload.email
    auth_user_id = uuid_mod.UUID(token_payload.sub)

    token_hash = hashlib.sha256(data.raw_token.encode()).hexdigest()

    # Atomic single-use claim
    result = await db.execute(
        sqlalchemy.text("""
            UPDATE public.user_invites
               SET status = 'accepted', accepted_at = NOW()
             WHERE token_hash  = :token_hash
               AND status      = 'pending'
               AND expires_at  > NOW()
            RETURNING id, tenant_id, email, invited_by, projectx_admin_id
        """),
        {"token_hash": token_hash},
    )
    claimed_row = result.first()

    if not claimed_row:
        raise HTTPException(status_code=401, detail="Invalid or expired invite")

    if claimed_row.email != oauth_email:
        raise HTTPException(
            status_code=401, detail="Email mismatch — invite was for a different address"
        )

    # Create user (identity only)
    user = User(
        auth_user_id=auth_user_id,
        tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
        email=oauth_email,
    )
    db.add(user)
    await db.flush()

    # If invited by projectx admin → this is the super admin
    is_super_admin = claimed_row.projectx_admin_id is not None
    root_unit_id = ""
    if is_super_admin:
        await db.execute(
            sqlalchemy.text(
                "UPDATE public.clients SET super_admin_id = :user_id WHERE id = :tenant_id"
            ),
            {"user_id": str(user.id), "tenant_id": str(claimed_row.tenant_id)},
        )

        # Auto-create root company unit with placeholder profile
        from app.modules.org_units.service import create_org_unit as _create_root_unit

        root_unit = await _create_root_unit(
            db=db,
            client_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
            name="Company",
            unit_type="company",
            parent_unit_id=None,
            created_by=user.id,
            actor_email=oauth_email,
            workspace_mode="enterprise",
            company_profile={
                "display_name": "",
                "industry": "",
                "company_size": "",
                "culture_summary": "",
                "hiring_bar": "",
                "brand_voice": "professional",
                "what_good_looks_like": "",
            },
        )
        root_unit_id = str(root_unit.id)

    redirect_to = "/onboarding" if is_super_admin else "/"

    logger.info(
        "auth.invite_completed",
        user_id=str(user.id),
        tenant_id=str(claimed_row.tenant_id),
        is_super_admin=is_super_admin,
    )

    # TODO: refactor complete_invite logic into auth/service.py so audit call moves to service layer
    await log_event(
        db,
        tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
        actor_id=user.id,
        actor_email=oauth_email,
        action=audit_actions.USER_INVITE_CLAIMED,
        resource="user",
        resource_id=user.id,
        payload={"email": oauth_email, "is_super_admin": is_super_admin},
        ip_address=request.client.host if request.client else None,
    )

    return CompleteInviteResponse(
        redirect_to=redirect_to,
        user_id=str(user.id),
        tenant_id=str(claimed_row.tenant_id),
        root_unit_id=root_unit_id,
    )


@router.get("/me", response_model=MeResponse)
async def get_current_user(
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_bypass_db),
) -> MeResponse:
    """Return the current user's profile, assignments, and company info."""
    user = ctx.user

    # Get client name
    result = await db.execute(select(Client).where(Client.id == user.tenant_id))
    client = result.scalar_one()

    # Check if tenant has any org units (separate query — not per-row)
    org_exists_result = await db.execute(
        select(func.count())
        .select_from(OrganizationalUnit)
        .where(OrganizationalUnit.client_id == user.tenant_id)
    )
    has_org_units = (org_exists_result.scalar() or 0) > 0

    return MeResponse(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        tenant_id=str(user.tenant_id),
        client_name=client.name,
        is_super_admin=ctx.is_super_admin,
        onboarding_complete=client.onboarding_complete,
        has_org_units=has_org_units,
        workspace_mode=ctx.workspace_mode,
        assignments=[
            RoleAssignmentResponse(
                org_unit_id=str(a.org_unit_id),
                org_unit_name=a.org_unit_name,
                role_name=a.role_name,
                permissions=a.permissions,
            )
            for a in ctx.assignments
        ],
    )


@router.post("/onboarding/complete")
async def complete_onboarding(
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_bypass_db),
) -> dict[str, str]:
    """Mark onboarding as complete. Super admin only.

    Validates: caller is super admin AND at least one org unit exists.
    """
    if not ctx.is_super_admin:
        raise HTTPException(status_code=403, detail="Only the super admin can complete onboarding")

    # Check at least one org unit exists
    org_count = await db.execute(
        select(func.count())
        .select_from(OrganizationalUnit)
        .where(OrganizationalUnit.client_id == ctx.user.tenant_id)
    )
    if (org_count.scalar() or 0) == 0:
        raise HTTPException(
            status_code=400,
            detail="Create at least one organizational unit before completing onboarding",
        )

    result = await db.execute(select(Client).where(Client.id == ctx.user.tenant_id))
    client = result.scalar_one()
    client.onboarding_complete = True

    await log_event(
        db,
        tenant_id=ctx.user.tenant_id,
        actor_id=ctx.user.id,
        actor_email=ctx.user.email,
        action=audit_actions.CLIENT_ONBOARDING_COMPLETED,
        resource="client",
        resource_id=ctx.user.tenant_id,
        payload={},
        ip_address=request.client.host if request.client else None,
    )

    return {"status": "completed"}
