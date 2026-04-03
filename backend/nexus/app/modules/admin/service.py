"""Admin service — client provisioning and management."""

import hashlib
import secrets

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Company, User, UserInvite
from app.modules.notifications.service import render_template, send_email

logger = structlog.get_logger()


async def provision_client(
    *,
    db: AsyncSession,
    company_name: str,
    admin_email: str,
    domain: str = "",
    industry: str = "",
    plan: str = "trial",
    admin_identity: str,  # email of the ProjectX admin performing this action
) -> tuple[Company, UserInvite, str]:
    """Create a company + invite for the Company Admin.

    Returns (company, invite, raw_token_or_url). The raw_token is passed to the
    email sender and then discarded — never stored.
    """
    # Create company
    company = Company(name=company_name, domain=domain or None, industry=industry or None, plan=plan)
    db.add(company)
    await db.flush()  # get company.id

    # Generate invite token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Create invite
    invite = UserInvite(
        tenant_id=company.id,
        email=admin_email,
        role="Company Admin",
        token_hash=token_hash,
        projectx_admin_id=admin_identity,
    )
    db.add(invite)
    await db.flush()  # get invite.id

    # Send invite email
    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"

    html = render_template(
        "company_admin_invite.html",
        company_name=company_name,
        invite_url=invite_url,
        expires_hours=72,
    )
    await send_email(
        to=admin_email,
        subject=f"You've been invited to set up {company_name} on ProjectX",
        html=html,
    )

    logger.info(
        "admin.client_provisioned",
        company_id=str(company.id),
        admin_email=admin_email,
    )

    # Log the invite URL explicitly in dry-run mode so it's easy to copy from terminal
    if settings.notifications_dry_run:
        logger.info("admin.invite_url_dry_run", invite_url=invite_url)

    return company, invite, invite_url if settings.notifications_dry_run else ""


async def list_clients(db: AsyncSession) -> list[dict]:
    """List all companies with their latest Company Admin invite status.

    Single query with LEFT JOIN — no N+1.
    """
    from sqlalchemy import func

    # Subquery: latest Company Admin invite per company
    latest_invite = (
        select(
            UserInvite.tenant_id,
            func.max(UserInvite.created_at).label("max_created"),
        )
        .where(UserInvite.role == "Company Admin")
        .group_by(UserInvite.tenant_id)
        .subquery()
    )

    result = await db.execute(
        select(Company, UserInvite)
        .outerjoin(
            latest_invite,
            Company.id == latest_invite.c.tenant_id,
        )
        .outerjoin(
            UserInvite,
            (UserInvite.tenant_id == latest_invite.c.tenant_id)
            & (UserInvite.created_at == latest_invite.c.max_created)
            & (UserInvite.role == "Company Admin"),
        )
        .order_by(Company.created_at.desc())
    )
    rows = result.all()

    return [
        {
            "company_id": str(company.id),
            "company_name": company.name,
            "domain": company.domain,
            "plan": company.plan,
            "onboarding_complete": company.onboarding_complete,
            "admin_email": invite.email if invite else None,
            "invite_status": invite.status if invite else None,
            "created_at": company.created_at.isoformat(),
        }
        for company, invite in rows
    ]
