"""Team management service — DB operations only.

Email dispatch is handled by the router via BackgroundTasks,
ensuring emails are sent AFTER the transaction commits.
"""

import hashlib
import secrets
import uuid as uuid_mod

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, OrganizationalUnit, Role, User, UserInvite, UserRoleAssignment
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event

logger = structlog.get_logger()


async def create_team_invite(
    *,
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    email: str,
    invited_by: uuid_mod.UUID,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> tuple[UserInvite, str, str]:
    """Create a simple invite — email only. No role info."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    invite = UserInvite(
        tenant_id=tenant_id,
        email=email,
        token_hash=token_hash,
        invited_by=invited_by,
    )
    db.add(invite)
    await db.flush()

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=invited_by,
        actor_email=actor_email,
        action=audit_actions.USER_INVITED,
        resource="user_invite",
        resource_id=invite.id,
        payload={"invited_email": email},
        ip_address=ip_address,
    )

    result = await db.execute(select(Client).where(Client.id == tenant_id))
    client = result.scalar_one()

    logger.info("settings.team_member_invited", tenant_id=str(tenant_id), email=email)

    return invite, raw_token, client.name


async def list_team_members(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    super_admin_id: uuid_mod.UUID | None,
) -> list[dict]:
    """List active users + pending invites with role assignments."""
    members: list[dict] = []

    # Active users
    result = await db.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.is_active == True)
        .order_by(User.created_at.asc())
    )
    users = result.scalars().all()

    # Batch-load all assignments for these users
    user_ids = [u.id for u in users]
    assignments_by_user: dict[uuid_mod.UUID, list[dict]] = {uid: [] for uid in user_ids}

    if user_ids:
        assignment_result = await db.execute(
            select(UserRoleAssignment, Role, OrganizationalUnit)
            .join(Role, UserRoleAssignment.role_id == Role.id)
            .join(OrganizationalUnit, UserRoleAssignment.org_unit_id == OrganizationalUnit.id)
            .where(UserRoleAssignment.user_id.in_(user_ids))
        )
        for ura, role, ou in assignment_result.all():
            assignments_by_user[ura.user_id].append(
                {
                    "org_unit_id": str(ura.org_unit_id),
                    "org_unit_name": ou.name,
                    "role_name": role.name,
                }
            )

    for user in users:
        members.append(
            {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "is_active": user.is_active,
                "is_super_admin": super_admin_id is not None and user.id == super_admin_id,
                "source": "user",
                "status": "active",
                "assignments": assignments_by_user.get(user.id, []),
                "created_at": user.created_at.isoformat(),
            }
        )

    # Pending invites
    invite_result = await db.execute(
        select(UserInvite)
        .where(UserInvite.tenant_id == tenant_id, UserInvite.status == "pending")
        .order_by(UserInvite.created_at.desc())
    )
    for invite in invite_result.scalars().all():
        members.append(
            {
                "id": str(invite.id),
                "email": invite.email,
                "full_name": None,
                "is_active": False,
                "is_super_admin": False,
                "source": "invite",
                "status": "pending",
                "assignments": [],
                "created_at": invite.created_at.isoformat(),
            }
        )

    return members


async def resend_team_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
    *,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> tuple[UserInvite, str, str]:
    """Supersede an existing invite and create a new one."""
    result = await db.execute(
        select(UserInvite).where(
            UserInvite.id == invite_id,
            UserInvite.tenant_id == tenant_id,
            UserInvite.status == "pending",
        )
    )
    existing = result.scalar_one_or_none()
    if not existing:
        raise ValueError("Invite not found or already used")

    raw_token = secrets.token_urlsafe(32)
    new_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    new_invite = UserInvite(
        tenant_id=existing.tenant_id,
        email=existing.email,
        invited_by=existing.invited_by,
        projectx_admin_id=existing.projectx_admin_id,
        token_hash=new_hash,
    )
    db.add(new_invite)
    await db.flush()

    existing.status = "superseded"
    existing.superseded_by = new_invite.id

    company_result = await db.execute(select(Client).where(Client.id == tenant_id))
    company = company_result.scalar_one()

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.USER_INVITE_RESENT,
        resource="user_invite",
        resource_id=new_invite.id,
        payload={"invited_email": existing.email, "superseded_invite_id": str(invite_id)},
        ip_address=ip_address,
    )

    logger.info("settings.invite_resent", invite_id=str(new_invite.id), email=new_invite.email)

    return new_invite, raw_token, company.name


async def revoke_team_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
    *,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Revoke a pending invite."""
    result = await db.execute(
        select(UserInvite).where(
            UserInvite.id == invite_id,
            UserInvite.tenant_id == tenant_id,
            UserInvite.status == "pending",
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise ValueError("Invite not found or not pending")

    invite.status = "revoked"

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.USER_INVITE_REVOKED,
        resource="user_invite",
        resource_id=invite_id,
        payload={"invited_email": invite.email},
        ip_address=ip_address,
    )

    logger.info("settings.invite_revoked", invite_id=str(invite_id))


async def deactivate_team_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    caller_auth_user_id: str,
    *,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> str:
    """Deactivate a user. Returns auth_user_id for background Supabase cleanup."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.is_active == True,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError("User not found or already inactive")

    if str(user.auth_user_id) == caller_auth_user_id:
        raise ValueError("Cannot deactivate your own account")

    user.is_active = False

    # Mark accepted invites as revoked
    invite_result = await db.execute(
        select(UserInvite).where(
            UserInvite.tenant_id == tenant_id,
            UserInvite.email == user.email,
            UserInvite.status == "accepted",
        )
    )
    for invite in invite_result.scalars().all():
        invite.status = "revoked"

    # Remove all role assignments
    role_result = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.tenant_id == tenant_id,
        )
    )
    removed_assignments = role_result.scalars().all()
    for assignment in removed_assignments:
        await db.delete(assignment)
    if removed_assignments:
        logger.info(
            "settings.role_assignments_removed_on_deactivation",
            user_id=str(user_id),
            count=len(removed_assignments),
        )

    # Nullify deletable_by references
    units_updated = await nullify_deletable_by_for_user(db, tenant_id, user_id)
    if units_updated > 0:
        logger.info(
            "settings.deletable_by_nullified_on_deactivation",
            user_id=str(user_id),
            units_updated=units_updated,
        )

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.USER_DEACTIVATED,
        resource="user",
        resource_id=user_id,
        payload={"deactivated_email": user.email, "auth_user_id": str(user.auth_user_id)},
        ip_address=ip_address,
    )

    logger.info("settings.user_deactivated", user_id=str(user_id), email=user.email)

    return str(user.auth_user_id)


async def _delete_auth_user(auth_user_id: str) -> None:
    """Delete a user from Supabase Auth via the Admin API."""
    import httpx

    from app.config import settings

    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.warning(
            "settings.auth_delete_skipped", reason="supabase_url or service_role_key not configured"
        )
        return

    url = f"{settings.supabase_url}/auth/v1/admin/users/{auth_user_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            url,
            headers={
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            },
        )
    if resp.status_code not in (200, 204):
        logger.error(
            "settings.auth_delete_failed", auth_user_id=auth_user_id, status=resp.status_code
        )
    else:
        logger.info("settings.auth_user_deleted", auth_user_id=auth_user_id)
