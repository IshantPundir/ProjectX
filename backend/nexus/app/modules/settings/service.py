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

from app.modules.audit import actions as audit_actions, log_event
from app.modules.ats import is_ats_source
from app.modules.auth import AuthProviderError, User, UserInvite, UserRoleAssignment, get_auth_provider
from app.modules.org_units import Client, OrganizationalUnit, nullify_deletable_by_for_user
from app.modules.roles import Role

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
    """List all team members (native + ATS-imported) + pending invites.

    Under the unified-storage model (spec 2026-05-14), ATS-imported users
    live in the `users` table tagged with `source LIKE 'ats_%'` and
    `auth_user_id IS NULL`. The legacy `ats_user_mappings` table is gone.

    Response shape per row matches the spec's TeamMember interface:
      - source: 'native' | 'ats_<vendor>'  (provenance from users.source)
      - external_id: ATS opaque id, NULL for pure-native rows
      - external_source_metadata: vendor blob (role, business_unit_id, …)
      - is_active: directly from users.is_active
      - has_auth_account: users.auth_user_id IS NOT NULL
      - invite_state: 'none' | 'pending' | 'accepted' | 'revoked'
                      derived from a LOWER(email) match against the
                      tenant's user_invites rows

    Rows are ordered: rows with auth accounts first (created_at asc), then
    ATS-imported-only rows (created_at desc — most recent imports surface
    so the recruiter sees what just arrived).
    """
    members: list[dict] = []

    # 1. Load every User row for the tenant (active + inactive).
    result = await db.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.deleted_at.is_(None))
        .order_by(User.created_at.asc())
    )
    users = list(result.scalars().all())
    user_ids = [u.id for u in users]

    # 2. Batch-load role assignments for users with auth accounts (rows
    #    without an auth account can't have assignments — they need to
    #    accept an invite first).
    assignments_by_user: dict[uuid_mod.UUID, list[dict]] = {
        uid: [] for uid in user_ids
    }
    if user_ids:
        assignment_result = await db.execute(
            select(UserRoleAssignment, Role, OrganizationalUnit)
            .join(Role, UserRoleAssignment.role_id == Role.id)
            .join(
                OrganizationalUnit,
                UserRoleAssignment.org_unit_id == OrganizationalUnit.id,
            )
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

    # 3. Load every invite for the tenant — we use this both for the
    #    per-user invite_state JOIN and to surface invites that have no
    #    matching User row yet (pre-accept).
    invite_result = await db.execute(
        select(UserInvite)
        .where(UserInvite.tenant_id == tenant_id)
        .order_by(UserInvite.created_at.desc())
    )
    invites = list(invite_result.scalars().all())

    # Latest invite per lower(email). Used both to compute invite_state on
    # each User row and to discover invites without a matching User row.
    latest_invite_by_email: dict[str, UserInvite] = {}
    for inv in invites:
        key = inv.email.lower()
        if key not in latest_invite_by_email:
            latest_invite_by_email[key] = inv

    def _invite_state_for(email: str) -> str:
        inv = latest_invite_by_email.get(email.lower())
        if inv is None:
            return "none"
        # Status enum: 'pending' | 'accepted' | 'revoked' | 'superseded'
        # 'superseded' rolls up to whatever the successor row says.
        return inv.status if inv.status in ("pending", "accepted", "revoked") else "none"

    # 4. Project User rows to TeamMember dicts.
    for user in users:
        members.append(
            {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "source": user.source,
                "external_id": user.external_id,
                "external_source_metadata": user.external_source_metadata,
                "is_active": user.is_active,
                "has_auth_account": user.auth_user_id is not None,
                "invite_state": _invite_state_for(user.email),
                "is_super_admin": (
                    super_admin_id is not None and user.id == super_admin_id
                ),
                "assignments": assignments_by_user.get(user.id, []),
                "created_at": user.created_at.isoformat(),
                # Backwards-compat label for callers that haven't migrated.
                # Phase D removes this when the frontend stops reading it.
                "status": _legacy_status(user),
            }
        )

    # 5. Add invites that don't yet have a matching User row (pre-accept
    #    pending invites — under the new model, the User row is created
    #    or promoted on invite_accept, so a pending invite without a
    #    matching email means the recruiter sent the invite to someone
    #    we don't know yet).
    known_emails = {u.email.lower() for u in users}
    for inv in invites:
        if inv.status != "pending":
            continue
        if inv.email.lower() in known_emails:
            continue
        members.append(
            {
                "id": str(inv.id),
                "email": inv.email,
                "full_name": None,
                "source": "native",
                "external_id": None,
                "external_source_metadata": None,
                "is_active": False,
                "has_auth_account": False,
                "invite_state": "pending",
                "is_super_admin": False,
                "assignments": [],
                "created_at": inv.created_at.isoformat(),
                "status": "pending",
            }
        )

    return members


def _legacy_status(user: User) -> str:
    """Map a User row to the legacy `status` enum that older callers used.

    'active'        — has auth account and is_active
    'inactive'      — has auth account but deactivated
    'ats_unlinked'  — ATS-imported, no auth account yet
    'pending'       — fallback (shouldn't normally hit)
    """
    if user.auth_user_id is not None:
        return "active" if user.is_active else "inactive"
    if is_ats_source(user.source):
        return "ats_unlinked"
    return "pending"


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
    """Delete a user from the configured auth provider.

    Kept as a thin shim so existing BackgroundTask call sites in
    settings/router.py stay one-line. New code should call
    `get_auth_provider().delete_user(...)` directly.
    """
    provider = get_auth_provider()
    try:
        await provider.delete_user(auth_user_id)
    except AuthProviderError as err:
        logger.error(
            "settings.auth_delete_failed",
            auth_user_id=auth_user_id,
            error=str(err),
        )
