"""Team management service — DB operations only.

Email dispatch is handled by the router via BackgroundTasks,
ensuring emails are sent AFTER the transaction commits.
If email fails, the invite persists and can be resent.
"""

import hashlib
import secrets
import uuid as uuid_mod

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, User, UserInvite
from app.modules.auth.permissions import require_permission, validate_permissions

logger = structlog.get_logger()


async def create_team_invite(
    *,
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    email: str,
    invited_by: uuid_mod.UUID,
) -> tuple[UserInvite, str, str]:
    """Create a simple invite — email only.

    Role, permissions, org_unit, and is_admin are all left as defaults.
    The user can be assigned roles and org units later via the org units page.
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    invite = UserInvite(
        tenant_id=tenant_id,
        email=email,
        role=None,  # no role — assigned later when placed into an org unit
        token_hash=token_hash,
        invited_by=invited_by,
    )
    db.add(invite)
    await db.flush()

    result = await db.execute(select(Client).where(Client.id == tenant_id))
    client = result.scalar_one()

    logger.info("settings.team_member_invited", tenant_id=str(tenant_id), email=email)

    return invite, raw_token, client.name


async def _get_visible_org_unit_ids(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    caller_org_unit_id: uuid_mod.UUID,
) -> set[uuid_mod.UUID]:
    """Return the caller's org unit ID + all descendant IDs."""
    from app.models import OrganizationalUnit

    result = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.client_id == tenant_id)
    )
    all_units = list(result.scalars().all())

    children_map: dict[uuid_mod.UUID | None, list] = {}
    for u in all_units:
        children_map.setdefault(u.parent_unit_id, []).append(u)

    ids: set[uuid_mod.UUID] = {caller_org_unit_id}
    queue = [caller_org_unit_id]
    while queue:
        parent_id = queue.pop()
        for child in children_map.get(parent_id, []):
            ids.add(child.id)
            queue.append(child.id)

    return ids


async def list_team_members(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    caller_org_unit_id: uuid_mod.UUID | None = None,
) -> list[dict]:
    """List active users + pending invites.

    Super Admin (org_unit_id=NULL): sees all members in tenant.
    Admin (org_unit_id=<uuid>): sees only members in own org unit + descendants.
    """
    members: list[dict] = []

    # Determine which org_unit_ids this caller can see
    visible_org_ids: set[uuid_mod.UUID] | None = None
    if caller_org_unit_id is not None:
        visible_org_ids = await _get_visible_org_unit_ids(db, tenant_id, caller_org_unit_id)

    # Active users only — deactivated users are hidden from the list
    query = select(User).where(User.tenant_id == tenant_id, User.is_active == True).order_by(User.created_at.asc())
    if visible_org_ids is not None:
        query = query.where(User.org_unit_id.in_(visible_org_ids))
    result = await db.execute(query)

    for user in result.scalars().all():
        members.append({
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "permissions": user.permissions or [],
            "source": "user",
            "status": "active" if user.is_active else "inactive",
            "created_at": user.created_at.isoformat(),
        })

    # Pending invites
    invite_query = (
        select(UserInvite)
        .where(UserInvite.tenant_id == tenant_id, UserInvite.status == "pending")
        .order_by(UserInvite.created_at.desc())
    )
    if visible_org_ids is not None:
        invite_query = invite_query.where(UserInvite.org_unit_id.in_(visible_org_ids))
    result = await db.execute(invite_query)

    for invite in result.scalars().all():
        members.append({
            "id": str(invite.id),
            "email": invite.email,
            "full_name": None,
            "role": invite.role,
            "is_active": False,
            "is_admin": invite.is_admin,
            "permissions": invite.permissions or [],
            "source": "invite",
            "status": "pending",
            "created_at": invite.created_at.isoformat(),
        })

    return members


async def resend_team_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
) -> tuple[UserInvite, str, str]:
    """Supersede an existing invite and create a new one. Returns (new_invite, raw_token, company_name).

    Does NOT send email — caller handles dispatch after commit.
    """
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
        role=existing.role,
        invited_by=existing.invited_by,
        projectx_admin_id=existing.projectx_admin_id,
        token_hash=new_hash,
    )
    db.add(new_invite)
    await db.flush()

    existing.status = "superseded"
    existing.superseded_by = new_invite.id

    # Get company name (RLS scoped via get_tenant_db)
    company_result = await db.execute(select(Client).where(Client.id == tenant_id))
    company = company_result.scalar_one()

    logger.info("settings.invite_resent", invite_id=str(new_invite.id), email=new_invite.email)

    return new_invite, raw_token, company.name


async def revoke_team_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
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
    logger.info("settings.invite_revoked", invite_id=str(invite_id))


async def deactivate_team_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    caller_auth_user_id: str,
) -> None:
    """Deactivate an accepted user and delete their Supabase Auth account.

    Deleting the auth.users row allows the user to signUp() fresh if
    re-invited later (new password, clean slate). Without this, signUp()
    fails with "User already registered" and the user is stuck.
    """
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

    # Guard: prevent the caller from deactivating themselves
    if str(user.auth_user_id) == caller_auth_user_id:
        raise ValueError("Cannot deactivate your own account")

    user.is_active = False

    # Mark the accepted invite(s) as revoked — keeps user_invites in sync with user status
    invite_result = await db.execute(
        select(UserInvite).where(
            UserInvite.tenant_id == tenant_id,
            UserInvite.email == user.email,
            UserInvite.status == "accepted",
        )
    )
    for invite in invite_result.scalars().all():
        invite.status = "revoked"

    # Delete the Supabase Auth account so the user can signUp() fresh if re-invited
    await _delete_auth_user(str(user.auth_user_id))

    logger.info("settings.user_deactivated", user_id=str(user_id), email=user.email)


async def _delete_auth_user(auth_user_id: str) -> None:
    """Delete a user from Supabase Auth via the Admin API."""
    import httpx

    from app.config import settings

    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.warning("settings.auth_delete_skipped", reason="supabase_url or service_role_key not configured")
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
        logger.error("settings.auth_delete_failed", auth_user_id=auth_user_id, status=resp.status_code)
    else:
        logger.info("settings.auth_user_deleted", auth_user_id=auth_user_id)
