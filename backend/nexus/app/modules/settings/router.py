import uuid as uuid_mod

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_tenant_db
from app.models import Client
from app.modules.auth.context import UserContext, get_current_user_roles, require_super_admin
from app.modules.notifications.service import render_template, send_email
from app.modules.settings.schemas import (
    ResendInviteResponse,
    TeamInviteRequest,
    TeamInviteResponse,
    TeamMember,
)
from app.modules.settings.service import (
    _delete_auth_user,
    create_team_invite,
    deactivate_team_user,
    list_team_members,
    resend_team_invite,
    revoke_team_invite,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/settings/team", tags=["settings"])


async def _send_team_invite_email(email: str, company_name: str, raw_token: str) -> None:
    """Send team invite email. Called via BackgroundTasks after transaction commits."""
    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"

    html = render_template(
        "team_invite.html",
        company_name=company_name,
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
    dependencies=[require_super_admin()],
)
async def invite_endpoint(
    data: TeamInviteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> TeamInviteResponse:
    """Invite a team member — email only. Super admin only."""
    tenant_id = ctx.user.tenant_id

    try:
        invite, raw_token, client_name = await create_team_invite(
            db=db,
            tenant_id=tenant_id,
            email=data.email,
            invited_by=ctx.user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"
    background_tasks.add_task(_send_team_invite_email, data.email, client_name, raw_token)

    return TeamInviteResponse(
        invite_id=str(invite.id),
        email=data.email,
        invite_url=invite_url if settings.notifications_dry_run else "",
    )


@router.get(
    "/members",
    response_model=list[TeamMember],
)
async def list_members_endpoint(
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[TeamMember]:
    """List team members. Visible to all authenticated users."""
    # Get super_admin_id for badge display
    client_result = await db.execute(select(Client).where(Client.id == ctx.user.tenant_id))
    client = client_result.scalar_one()

    members = await list_team_members(db, ctx.user.tenant_id, super_admin_id=client.super_admin_id)
    return [TeamMember(**m) for m in members]


@router.post(
    "/resend/{invite_id}",
    response_model=ResendInviteResponse,
    dependencies=[require_super_admin()],
)
async def resend_endpoint(
    invite_id: str,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResendInviteResponse:
    """Resend an invite. Super admin only."""
    try:
        new_invite, raw_token, company_name = await resend_team_invite(
            db, ctx.user.tenant_id, uuid_mod.UUID(invite_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"
    background_tasks.add_task(_send_team_invite_email, new_invite.email, company_name, raw_token)

    return ResendInviteResponse(
        new_invite_id=str(new_invite.id),
        invite_url=invite_url if settings.notifications_dry_run else "",
    )


@router.post(
    "/revoke/{invite_id}",
    dependencies=[require_super_admin()],
)
async def revoke_endpoint(
    invite_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Revoke a pending invite. Super admin only."""
    try:
        await revoke_team_invite(db, ctx.user.tenant_id, uuid_mod.UUID(invite_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "revoked"}


async def _background_delete_auth_user(auth_user_id: str) -> None:
    """Background task wrapper — logs errors, never re-raises."""
    try:
        await _delete_auth_user(auth_user_id)
    except Exception as exc:
        logger.error(
            "settings.supabase_deletion_failed",
            auth_user_id=auth_user_id,
            error=str(exc),
        )


@router.post(
    "/deactivate/{user_id}",
    dependencies=[require_super_admin()],
)
async def deactivate_endpoint(
    user_id: str,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Deactivate a user. Super admin only.

    DB deactivation happens in-transaction. Supabase auth account
    deletion is scheduled as a background task after commit.
    """
    try:
        auth_user_id = await deactivate_team_user(
            db, ctx.user.tenant_id, uuid_mod.UUID(user_id), str(ctx.user.auth_user_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(_background_delete_auth_user, auth_user_id)

    return {"status": "deactivated"}
