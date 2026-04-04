import uuid as uuid_mod

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.config import settings
from app.database import get_tenant_db
from app.middleware.auth import require_roles
from app.models import User
from app.modules.notifications.service import render_template, send_email
from app.modules.settings.schemas import (
    ResendInviteResponse,
    TeamInviteRequest,
    TeamInviteResponse,
    TeamMember,
)
from app.modules.settings.service import (
    create_team_invite,
    deactivate_team_user,
    list_team_members,
    resend_team_invite,
    revoke_team_invite,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/settings/team", tags=["settings"])


async def _send_team_invite_email(email: str, role: str, company_name: str, raw_token: str) -> None:
    """Send team invite email. Called via BackgroundTasks after transaction commits."""
    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"

    html = render_template(
        "team_invite.html",
        company_name=company_name,
        role=role,
        invite_url=invite_url,
        expires_hours=72,
    )
    await send_email(
        to=email,
        subject=f"You've been invited to join {company_name} on ProjectX",
        html=html,
    )

    if settings.notifications_dry_run:
        logger.info("settings.invite_url_dry_run", invite_url=invite_url)


@router.post(
    "/invite",
    response_model=TeamInviteResponse,
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def invite_endpoint(
    data: TeamInviteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
) -> TeamInviteResponse:
    """Invite a team member to the company."""
    token_payload = request.state.token_payload
    tenant_id = uuid_mod.UUID(token_payload.tenant_id)

    # Get the inviting user's record for invited_by FK and permission checks.
    # We can't use token_payload.sub directly — that's auth_user_id, not users.id.
    result = await db.execute(
        select(User).where(User.auth_user_id == token_payload.sub)
    )
    admin_user = result.scalar_one_or_none()
    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")
    if not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admin nodes can invite users")

    try:
        invite, raw_token, client_name = await create_team_invite(
            db=db,
            tenant_id=tenant_id,
            email=data.email,
            role=data.role,
            invited_by=admin_user.id,
            inviting_user_permissions=admin_user.permissions or [],
            is_admin=data.is_admin,
            permissions=data.permissions,
            org_unit_id=uuid_mod.UUID(data.org_unit_id) if data.org_unit_id else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Email sent AFTER transaction commits via BackgroundTasks.
    # If email fails, invite persists and can be resent.
    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"
    background_tasks.add_task(_send_team_invite_email, data.email, data.role, client_name, raw_token)

    return TeamInviteResponse(
        invite_id=str(invite.id),
        email=data.email,
        role=data.role,
        invite_url=invite_url if settings.notifications_dry_run else "",
    )


@router.get(
    "/members",
    response_model=list[TeamMember],
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def list_members_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> list[TeamMember]:
    """List team members. Super Admin sees all; Admin sees own org unit + descendants."""
    token_payload = request.state.token_payload
    tenant_id = uuid_mod.UUID(token_payload.tenant_id)
    caller_org = uuid_mod.UUID(token_payload.org_unit_id) if token_payload.org_unit_id else None
    members = await list_team_members(db, tenant_id, caller_org_unit_id=caller_org)
    return [TeamMember(**m) for m in members]


@router.post(
    "/resend/{invite_id}",
    response_model=ResendInviteResponse,
    dependencies=[require_roles("Company Admin")],
)
async def resend_endpoint(
    invite_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
) -> ResendInviteResponse:
    """Resend an invite (supersedes the old one, creates a new token)."""
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    try:
        new_invite, raw_token, company_name = await resend_team_invite(
            db, tenant_id, uuid_mod.UUID(invite_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"
    background_tasks.add_task(_send_team_invite_email, new_invite.email, new_invite.role, company_name, raw_token)

    return ResendInviteResponse(
        new_invite_id=str(new_invite.id),
        invite_url=invite_url if settings.notifications_dry_run else "",
    )


@router.post(
    "/revoke/{invite_id}",
    dependencies=[require_roles("Company Admin")],
)
async def revoke_endpoint(
    invite_id: str,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Revoke a pending invite."""
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    try:
        await revoke_team_invite(db, tenant_id, uuid_mod.UUID(invite_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "revoked"}


@router.post(
    "/deactivate/{user_id}",
    dependencies=[require_roles("Company Admin")],
)
async def deactivate_endpoint(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Deactivate an accepted team member."""
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    caller_auth_user_id = request.state.token_payload.sub
    try:
        await deactivate_team_user(db, tenant_id, uuid_mod.UUID(user_id), caller_auth_user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deactivated"}
