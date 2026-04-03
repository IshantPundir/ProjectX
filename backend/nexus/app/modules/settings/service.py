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

from app.models import Company, User, UserInvite

logger = structlog.get_logger()


async def create_team_invite(
    *,
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    email: str,
    role: str,
    invited_by: uuid_mod.UUID,
) -> tuple[UserInvite, str, str]:
    """Create an invite for a team member. Returns (invite, raw_token, company_name).

    Does NOT send email — caller is responsible for email dispatch
    after the transaction commits.
    """
    allowed_roles = {"Recruiter", "Hiring Manager", "Interviewer", "Observer"}
    if role not in allowed_roles:
        raise ValueError(f"Invalid role: {role}. Must be one of: {allowed_roles}")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    invite = UserInvite(
        tenant_id=tenant_id,
        email=email,
        role=role,
        token_hash=token_hash,
        invited_by=invited_by,
    )
    db.add(invite)
    await db.flush()

    # Get company name for the email (RLS scopes this to the tenant)
    result = await db.execute(select(Company).where(Company.id == tenant_id))
    company = result.scalar_one()

    logger.info("settings.team_member_invited", tenant_id=str(tenant_id), email=email, role=role)

    return invite, raw_token, company.name


async def list_team_members(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
) -> list[dict]:
    """List active users + pending invites for this tenant."""
    members: list[dict] = []

    # Active users (RLS scoped by get_tenant_db)
    result = await db.execute(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at.asc())
    )
    for user in result.scalars().all():
        members.append({
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "source": "user",
            "status": "active" if user.is_active else "inactive",
            "created_at": user.created_at.isoformat(),
        })

    # Pending invites only (not expired/revoked/superseded — those have no actions)
    result = await db.execute(
        select(UserInvite).where(
            UserInvite.tenant_id == tenant_id,
            UserInvite.status == "pending",
        ).order_by(UserInvite.created_at.desc())
    )
    for invite in result.scalars().all():
        members.append({
            "id": str(invite.id),
            "email": invite.email,
            "full_name": None,
            "role": invite.role,
            "is_active": False,
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
    company_result = await db.execute(select(Company).where(Company.id == tenant_id))
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
    """Deactivate an accepted user. Guards against self-deactivation by caller identity."""
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
    logger.info("settings.user_deactivated", user_id=str(user_id), email=user.email)
