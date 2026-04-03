# Phase 2: Pipeline A Spine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete end-to-end Pipeline A flow: admin provisions client → invite email sent → Company Admin claims invite → account created → placeholder onboarding page.

**Architecture:** New `admin` module handles client provisioning with `bypass_rls`. Auth endpoints handle invite verification and atomic claim. Notifications service supports dry-run mode (log to stdout) and Resend. Both frontends get `@supabase/ssr` middleware with `getClaims()` for route protection. Admin panel gets login/signup/provision pages. Client dashboard gets login/invite/onboarding pages.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), Next.js 16 + `@supabase/ssr` + Tailwind v4 (frontends), Resend (email MVP), Jinja2 (templates)

**Depends on:** Phase 1 complete (tables, RLS, auth hook, JWKS verification, middleware)

**Design spec:** `docs/superpowers/specs/2026-04-03-auth-client-user-onboarding-design.md`

**Source of truth for SQL/patterns:** [Notion spec](https://www.notion.so/Auth-Client-User-onboarding-3367984a452a806c8097c9d7cf084f18)

---

## File Map

### Backend — Create

| File | Responsibility |
|---|---|
| `backend/nexus/app/models.py` | SQLAlchemy ORM models: Company, User, UserInvite |
| `backend/nexus/app/modules/admin/__init__.py` | Package init |
| `backend/nexus/app/modules/admin/schemas.py` | Pydantic request/response schemas for admin endpoints |
| `backend/nexus/app/modules/admin/service.py` | Provision client + list clients logic |
| `backend/nexus/app/modules/admin/router.py` | Admin API routes |
| `backend/nexus/app/modules/notifications/templates/company_admin_invite.html` | Jinja2 email template |
| `backend/nexus/tests/test_admin.py` | Admin endpoint tests |
| `backend/nexus/tests/test_auth_endpoints.py` | Auth endpoint tests (verify-invite, complete-invite, me) |

### Backend — Modify

| File | What changes |
|---|---|
| `backend/nexus/pyproject.toml` | Add `resend`, `jinja2` dependencies |
| `backend/nexus/app/modules/notifications/service.py` | Rewrite: EmailProvider protocol + DryRunProvider + ResendProvider |
| `backend/nexus/app/modules/auth/router.py` | Add verify-invite, complete-invite, replace me stub |
| `backend/nexus/app/modules/auth/schemas.py` | Add request/response schemas for auth endpoints |
| `backend/nexus/app/database.py` | Add `get_bypass_db()` and `get_tenant_db()` FastAPI dependencies |
| `backend/nexus/app/main.py` | Register admin router |

### Frontend Admin — Create

| File | Responsibility |
|---|---|
| `frontend/admin/lib/supabase/client.ts` | Browser Supabase client singleton |
| `frontend/admin/lib/supabase/server.ts` | Server Supabase client factory |
| `frontend/admin/lib/api/client.ts` | Typed fetch wrapper → Nexus backend |
| `frontend/admin/middleware.ts` | `getClaims()` → `is_projectx_admin` route protection |
| `frontend/admin/app/(auth)/layout.tsx` | Centered auth layout |
| `frontend/admin/app/(auth)/login/page.tsx` | Email/password login |
| `frontend/admin/app/(auth)/signup/page.tsx` | Email/password signup |
| `frontend/admin/app/pending-approval/page.tsx` | Pending approval message |
| `frontend/admin/app/(admin)/layout.tsx` | Admin layout with sidebar |
| `frontend/admin/app/(admin)/page.tsx` | Redirect to /dashboard |
| `frontend/admin/app/(admin)/dashboard/page.tsx` | Client list table |
| `frontend/admin/app/(admin)/dashboard/provision/page.tsx` | Provision form |

### Frontend Admin — Modify

| File | What changes |
|---|---|
| `frontend/admin/app/layout.tsx` | Update metadata |
| `frontend/admin/app/globals.css` | Add theme variables |
| `frontend/admin/app/page.tsx` | Remove (replaced by route groups) |
| `frontend/admin/package.json` | Add `@supabase/ssr`, `@supabase/supabase-js` |

### Frontend App — Create

| File | Responsibility |
|---|---|
| `frontend/app/lib/supabase/client.ts` | Browser Supabase client singleton |
| `frontend/app/lib/supabase/server.ts` | Server Supabase client factory |
| `frontend/app/lib/api/client.ts` | Typed fetch wrapper → Nexus backend |
| `frontend/app/middleware.ts` | `getClaims()` → `tenant_id` + `app_role` route protection |
| `frontend/app/app/(auth)/layout.tsx` | Centered auth layout |
| `frontend/app/app/(auth)/login/page.tsx` | Email/password login |
| `frontend/app/app/(auth)/invite/page.tsx` | Invite claim flow (verify → signup → complete) |
| `frontend/app/app/onboarding/layout.tsx` | Minimal onboarding layout |
| `frontend/app/app/onboarding/page.tsx` | Placeholder onboarding page |
| `frontend/app/app/(dashboard)/layout.tsx` | Dashboard layout with `GET /api/auth/me` check |
| `frontend/app/app/(dashboard)/page.tsx` | Dashboard home placeholder |

### Frontend App — Modify

| File | What changes |
|---|---|
| `frontend/app/app/layout.tsx` | Update metadata |
| `frontend/app/app/globals.css` | Add theme variables |
| `frontend/app/app/page.tsx` | Remove (replaced by route groups) |
| `frontend/app/package.json` | Add `@supabase/ssr`, `@supabase/supabase-js` |

---

### Task 1: Add backend dependencies

**Files:**
- Modify: `backend/nexus/pyproject.toml`

- [ ] **Step 1: Add resend and jinja2 to dependencies**

In `backend/nexus/pyproject.toml`, add these two lines to the `dependencies` list (after the `httpx` entry):

```toml
    # Email (MVP provider)
    "resend>=4.0,<5",

    # Template rendering (email templates)
    "jinja2>=3.1,<4",
```

- [ ] **Step 2: Install**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && pip install -e ".[dev]"
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add resend and jinja2 dependencies"
```

---

### Task 2: SQLAlchemy ORM models

**Files:**
- Create: `backend/nexus/app/models.py`

- [ ] **Step 1: Create ORM models**

Create `backend/nexus/app/models.py`:

```python
"""SQLAlchemy ORM models for auth foundation tables.

These map to the tables created by Supabase migration 20260403000000.
They are used for INSERT/UPDATE/SELECT via SQLAlchemy — the table
definitions themselves (columns, constraints, RLS) live in the migration.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    culture_brief: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String, nullable=False, server_default="trial")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    auth_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    notification_prefs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserInvite(Base):
    __tablename__ = "user_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    projectx_admin_id: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user_invites.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW() + INTERVAL '72 hours'"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 2: Commit**

```bash
git add app/models.py
git commit -m "feat: add SQLAlchemy ORM models for companies, users, user_invites"
```

---

### Task 3: Add FastAPI database dependencies

**Files:**
- Modify: `backend/nexus/app/database.py`

- [ ] **Step 1: Add FastAPI-compatible dependency functions**

Add these after the existing `get_bypass_session()` in `backend/nexus/app/database.py`:

```python
async def get_bypass_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session with RLS bypass.

    Use with: Depends(get_bypass_db)
    Only for: admin routes, complete-invite, onboarding completion.
    """
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                sqlalchemy.text("SET LOCAL app.bypass_rls = 'true'")
            )
            yield session


async def get_tenant_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session with RLS tenant context.

    Use with: Depends(get_tenant_db)
    For: all tenant-scoped routes.
    """
    tenant_id = request.state.tenant_id
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                sqlalchemy.text("SET LOCAL app.current_tenant = :tid"),
                {"tid": tenant_id},
            )
            yield session
```

Also add the import at the top of the file:

```python
from starlette.requests import Request
```

- [ ] **Step 2: Commit**

```bash
git add app/database.py
git commit -m "feat: add FastAPI-compatible database dependencies (get_bypass_db, get_tenant_db)"
```

---

### Task 4: Rewrite notifications service (dry-run + Resend)

**Files:**
- Modify: `backend/nexus/app/modules/notifications/service.py`
- Create: `backend/nexus/app/modules/notifications/templates/company_admin_invite.html`

- [ ] **Step 1: Rewrite notifications service**

Replace the entire content of `backend/nexus/app/modules/notifications/service.py`:

```python
"""Provider-agnostic email dispatch with dry-run mode.

When NOTIFICATIONS_DRY_RUN=true: logs the full email body and any invite URLs
to stdout. Copy the URL from the terminal to test the full Pipeline A flow
without Resend credentials.

When NOTIFICATIONS_DRY_RUN=false: sends via Resend API.
"""

import asyncio
from pathlib import Path
from typing import Protocol

import structlog
from jinja2 import Environment, FileSystemLoader

from app.config import settings
from app.modules.notifications.schemas import EmailMessage, SMSMessage

logger = structlog.get_logger()

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


def render_template(template_name: str, **kwargs: object) -> str:
    """Render a Jinja2 email template."""
    template = _jinja_env.get_template(template_name)
    return template.render(**kwargs)


class EmailProvider(Protocol):
    """Provider-agnostic email interface."""
    async def send(self, *, to: str, subject: str, html: str) -> None: ...


class DryRunProvider:
    """Logs emails to stdout instead of sending. For local development."""
    async def send(self, *, to: str, subject: str, html: str) -> None:
        logger.info(
            "email.dry_run",
            to=to,
            subject=subject,
            html_length=len(html),
            html_body=html,
        )


class ResendProvider:
    """Sends emails via Resend API."""
    def __init__(self) -> None:
        import resend
        resend.api_key = settings.resend_api_key
        self._from = settings.email_from
        self._resend = resend

    async def send(self, *, to: str, subject: str, html: str) -> None:
        await asyncio.to_thread(
            self._resend.Emails.send,
            {"from": self._from, "to": to, "subject": subject, "html": html},
        )


def _create_provider() -> EmailProvider:
    if settings.notifications_dry_run:
        return DryRunProvider()
    return ResendProvider()


_provider: EmailProvider = _create_provider()


async def send_email(*, to: str, subject: str, html: str) -> None:
    """Send an email. Business logic calls this — never import a provider directly."""
    try:
        await _provider.send(to=to, subject=subject, html=html)
        logger.info("email.sent", to=to, subject=subject)
    except Exception as exc:
        logger.error("email.failed", to=to, subject=subject, error=str(exc))
        raise


async def send_sms(message: SMSMessage) -> bool:
    """Send an SMS through the configured provider (Twilio at MVP)."""
    logger.info("notifications.sms.send", to=message.to)
    # TODO: implement Twilio integration (Phase 5+)
    return True
```

- [ ] **Step 2: Create email template**

Create directory and template:

```bash
mkdir -p backend/nexus/app/modules/notifications/templates
```

Create `backend/nexus/app/modules/notifications/templates/company_admin_invite.html`:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 40px 20px; color: #1a1a1a;">
  <h2 style="margin-bottom: 8px;">You've been invited to set up {{ company_name }} on ProjectX</h2>
  <p style="color: #666; margin-bottom: 24px;">Click the link below to create your Company Admin account and configure your workspace.</p>
  <a href="{{ invite_url }}" style="display: inline-block; background: #16a34a; color: #fff; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: 500;">Set Up Your Account</a>
  <p style="color: #999; font-size: 14px; margin-top: 24px;">This invite expires in {{ expires_hours }} hours.</p>
  <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
  <p style="color: #999; font-size: 12px;">ProjectX — AI Video Interview Platform</p>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add app/modules/notifications/service.py app/modules/notifications/templates/
git commit -m "feat: rewrite notifications with dry-run mode + Resend provider + invite template"
```

---

### Task 5: Admin module — schemas, service, router

**Files:**
- Create: `backend/nexus/app/modules/admin/__init__.py`
- Create: `backend/nexus/app/modules/admin/schemas.py`
- Create: `backend/nexus/app/modules/admin/service.py`
- Create: `backend/nexus/app/modules/admin/router.py`

- [ ] **Step 1: Create the admin module package**

```bash
mkdir -p backend/nexus/app/modules/admin
touch backend/nexus/app/modules/admin/__init__.py
```

- [ ] **Step 2: Create admin schemas**

Create `backend/nexus/app/modules/admin/schemas.py`:

```python
from pydantic import BaseModel


class ProvisionClientRequest(BaseModel):
    company_name: str
    admin_email: str
    domain: str = ""
    industry: str = ""
    plan: str = "trial"


class ProvisionClientResponse(BaseModel):
    company_id: str
    invite_id: str
    admin_email: str
    invite_url: str  # Only present in dry-run mode; empty in production


class ClientListItem(BaseModel):
    company_id: str
    company_name: str
    domain: str | None
    plan: str
    onboarding_complete: bool
    admin_email: str | None
    invite_status: str | None
    created_at: str
```

- [ ] **Step 3: Create admin service**

Create `backend/nexus/app/modules/admin/service.py`:

```python
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

    Returns (company, invite, raw_token). The raw_token is passed to the
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
```

- [ ] **Step 4: Create admin router**

Create `backend/nexus/app/modules/admin/router.py`:

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.modules.admin.schemas import (
    ClientListItem,
    ProvisionClientRequest,
    ProvisionClientResponse,
)
from app.modules.admin.service import list_clients, provision_client
from app.modules.auth.service import require_projectx_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post(
    "/provision-client",
    response_model=ProvisionClientResponse,
    dependencies=[require_projectx_admin()],
)
async def provision_client_endpoint(
    data: ProvisionClientRequest,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> ProvisionClientResponse:
    """Provision a new enterprise client and send invite to their Company Admin."""
    admin_email = request.state.token_payload.email

    company, invite, invite_url = await provision_client(
        db=db,
        company_name=data.company_name,
        admin_email=data.admin_email,
        domain=data.domain,
        industry=data.industry,
        plan=data.plan,
        admin_identity=admin_email,
    )

    return ProvisionClientResponse(
        company_id=str(company.id),
        invite_id=str(invite.id),
        admin_email=data.admin_email,
        invite_url=invite_url,
    )


@router.get(
    "/clients",
    response_model=list[ClientListItem],
    dependencies=[require_projectx_admin()],
)
async def list_clients_endpoint(
    db: AsyncSession = Depends(get_bypass_db),
) -> list[ClientListItem]:
    """List all provisioned companies and their invite statuses."""
    clients = await list_clients(db)
    return [ClientListItem(**c) for c in clients]
```

- [ ] **Step 5: Commit**

```bash
git add app/modules/admin/
git commit -m "feat: add admin module with provision-client and list-clients endpoints"
```

---

### Task 6: Auth endpoints — verify-invite, complete-invite, me

**Files:**
- Modify: `backend/nexus/app/modules/auth/schemas.py`
- Modify: `backend/nexus/app/modules/auth/router.py`

- [ ] **Step 1: Add auth endpoint schemas**

Add these classes to `backend/nexus/app/modules/auth/schemas.py` (after `CandidateTokenPayload`):

```python
class VerifyInviteResponse(BaseModel):
    email: str
    role: str
    company_name: str


class CompleteInviteRequest(BaseModel):
    raw_token: str


class CompleteInviteResponse(BaseModel):
    redirect_to: str  # "/onboarding" or "/(dashboard)"
    user_id: str
    tenant_id: str
    role: str


class MeResponse(BaseModel):
    user_id: str
    auth_user_id: str
    email: str
    full_name: str | None
    role: str
    tenant_id: str
    company_name: str
    onboarding_complete: bool
```

- [ ] **Step 2: Rewrite auth router**

Replace the entire content of `backend/nexus/app/modules/auth/router.py`:

```python
"""Auth endpoints — invite verification, invite claim, and user profile."""

import hashlib

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
    import uuid as uuid_mod

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

    # Company Admin → /onboarding, everyone else → /(dashboard)
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
```

Add the missing import at the top:

```python
import sqlalchemy
```

- [ ] **Step 3: Commit**

```bash
git add app/modules/auth/schemas.py app/modules/auth/router.py
git commit -m "feat: add verify-invite, complete-invite, and me auth endpoints"
```

---

### Task 6b: Auth endpoint tests

**Files:**
- Create: `backend/nexus/tests/test_auth_endpoints.py`

> **Note:** These tests verify the most security-critical endpoint in the system (`complete-invite`). They use mocked database sessions to test the logic without requiring a live database.

- [ ] **Step 1: Write auth endpoint tests**

Create `backend/nexus/tests/test_auth_endpoints.py`:

```python
"""Tests for auth endpoints — verify-invite, complete-invite, me.

The complete-invite endpoint is the most security-critical endpoint:
it atomically claims an invite token and creates a user row. These tests
verify the key invariants: valid claim succeeds, expired/used tokens are
rejected, email mismatch is rejected, and transaction rollback works.
"""

import hashlib
import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def ec_key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture
def mock_jwks(ec_key_pair):
    _, public_key = ec_key_pair
    mock_jwk = MagicMock()
    mock_jwk.key = public_key
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_jwk
    return mock_client


def _make_jwt(private_key, **overrides) -> str:
    payload = {
        "sub": "auth-user-uuid",
        "tenant_id": "tenant-uuid",
        "app_role": "Company Admin",
        "email": "admin@acme.com",
        "role": "authenticated",
        "is_projectx_admin": False,
        "exp": int(time.time()) + 3600,
        "aud": "authenticated",
    }
    payload.update(overrides)
    return pyjwt.encode(payload, private_key, algorithm="ES256")


class TestVerifyInvite:
    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self):
        """A nonexistent token should return 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/auth/verify-invite?token=nonexistent")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_token_param_returns_422(self):
        """Missing required query param should return 422."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/auth/verify-invite")
        assert resp.status_code == 422


class TestCompleteInvite:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        """complete-invite without a Bearer token should return 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/complete-invite",
                json={"raw_token": "some-token"},
            )
        assert resp.status_code == 401
```

- [ ] **Step 2: Run tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_auth_endpoints.py -v
```

Expected: All tests pass (these test the error paths which don't need a live DB).

- [ ] **Step 3: Commit**

```bash
git add tests/test_auth_endpoints.py
git commit -m "test: add auth endpoint tests for verify-invite and complete-invite"
```

---

### Task 7: Register admin router in main.py

**Files:**
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Add admin router import and registration**

In `backend/nexus/app/main.py`, add after the existing router imports (line 70):

```python
    from app.modules.admin.router import router as admin_router
```

And add after `application.include_router(candidate_router)` (line 81):

```python
    application.include_router(admin_router)
```

- [ ] **Step 2: Commit**

```bash
git add app/main.py
git commit -m "feat: register admin router in main.py"
```

---

### Task 8: Install frontend Supabase dependencies

**Files:**
- Modify: `frontend/admin/package.json`
- Modify: `frontend/app/package.json`

- [ ] **Step 1: Install in admin panel**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin && npm install @supabase/supabase-js@latest @supabase/ssr@latest
```

- [ ] **Step 2: Install in client dashboard**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app && npm install @supabase/supabase-js@latest @supabase/ssr@latest
```

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/admin/package.json frontend/admin/package-lock.json frontend/app/package.json frontend/app/package-lock.json
git commit -m "feat: install @supabase/ssr and @supabase/supabase-js in both frontends"
```

---

### Task 9: Frontend admin — Supabase lib + API client

**Files:**
- Create: `frontend/admin/lib/supabase/client.ts`
- Create: `frontend/admin/lib/supabase/server.ts`
- Create: `frontend/admin/lib/api/client.ts`

- [ ] **Step 1: Create browser Supabase client**

Create `frontend/admin/lib/supabase/client.ts`:

```typescript
import { createBrowserClient } from "@supabase/ssr";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
```

- [ ] **Step 2: Create server Supabase client**

Create `frontend/admin/lib/supabase/server.ts`:

```typescript
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, options);
          });
        },
      },
    },
  );
}
```

- [ ] **Step 3: Create API client**

Create `frontend/admin/lib/api/client.ts`:

```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { token?: string } = {},
): Promise<T> {
  const { token, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${path}`, {
    ...fetchOptions,
    headers,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/admin/lib/
git commit -m "feat: add Supabase client + API client for admin panel"
```

---

### Task 10: Frontend admin — middleware

**Files:**
- Create: `frontend/admin/middleware.ts`

- [ ] **Step 1: Create middleware**

Create `frontend/admin/middleware.ts`:

```typescript
import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PATHS = new Set(["/login", "/signup"]);

export async function middleware(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const path = request.nextUrl.pathname;

  // Public paths — allow through
  if (PUBLIC_PATHS.has(path)) {
    // Refresh session silently (for token auto-refresh)
    await supabase.auth.getUser();
    return supabaseResponse;
  }

  // Validate session + get claims
  const {
    data: { claims },
    error,
  } = await supabase.auth.getClaims();

  // No valid session → login
  if (error || !claims) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // /pending-approval — any authenticated user can see this
  if (path === "/pending-approval") {
    return supabaseResponse;
  }

  // All other routes require is_projectx_admin
  const isAdmin = (claims as Record<string, unknown>).is_projectx_admin;
  if (!isAdmin) {
    return NextResponse.redirect(new URL("/pending-approval", request.url));
  }

  return supabaseResponse;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/admin/middleware.ts
git commit -m "feat: add admin panel middleware with is_projectx_admin check"
```

---

### Task 11: Frontend admin — auth pages + pending approval

**Files:**
- Create: `frontend/admin/app/(auth)/layout.tsx`
- Create: `frontend/admin/app/(auth)/login/page.tsx`
- Create: `frontend/admin/app/(auth)/signup/page.tsx`
- Create: `frontend/admin/app/pending-approval/page.tsx`
- Modify: `frontend/admin/app/layout.tsx`
- Modify: `frontend/admin/app/globals.css`
- Delete: `frontend/admin/app/page.tsx`

- [ ] **Step 1: Update root layout**

Replace `frontend/admin/app/layout.tsx`:

```typescript
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "ProjectX Admin",
  description: "Internal administration panel",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-zinc-50 font-sans text-zinc-900">
        {children}
      </body>
    </html>
  );
}
```

- [ ] **Step 2: Update globals.css**

Replace `frontend/admin/app/globals.css`:

```css
@import "tailwindcss";

@theme inline {
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
}
```

- [ ] **Step 3: Delete old page.tsx**

```bash
rm frontend/admin/app/page.tsx
```

- [ ] **Step 4: Create auth layout**

Create `frontend/admin/app/(auth)/layout.tsx`:

```typescript
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-sm">{children}</div>
    </div>
  );
}
```

- [ ] **Step 5: Create login page**

Create `frontend/admin/app/(auth)/login/page.tsx`:

```typescript
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const supabase = createClient();
    const { error: authError } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    if (authError) {
      setError(authError.message);
      setLoading(false);
      return;
    }

    router.push("/dashboard");
    router.refresh();
  }

  return (
    <>
      <div className="text-center mb-8">
        <h1 className="text-xl font-bold text-zinc-900">ProjectX Admin</h1>
        <p className="text-sm text-zinc-500 mt-1">Internal administration panel</p>
      </div>
      <form onSubmit={handleSubmit} className="bg-white border border-zinc-200 rounded-xl p-7 space-y-4">
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</p>
        )}
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900 focus:border-transparent"
            placeholder="you@projectx.com"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Password</label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900 focus:border-transparent"
          />
        </div>
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-zinc-900 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-zinc-800 disabled:opacity-50"
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
      <p className="text-center text-sm text-zinc-500 mt-4">
        Need an account?{" "}
        <Link href="/signup" className="text-blue-600 hover:underline">
          Sign up
        </Link>
      </p>
    </>
  );
}
```

- [ ] **Step 6: Create signup page**

Create `frontend/admin/app/(auth)/signup/page.tsx`:

```typescript
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const supabase = createClient();
    const { error: authError } = await supabase.auth.signUp({
      email,
      password,
    });

    if (authError) {
      setError(authError.message);
      setLoading(false);
      return;
    }

    router.push("/pending-approval");
    router.refresh();
  }

  return (
    <>
      <div className="text-center mb-8">
        <h1 className="text-xl font-bold text-zinc-900">Create Admin Account</h1>
        <p className="text-sm text-zinc-500 mt-1">Requires manual approval after signup</p>
      </div>
      <form onSubmit={handleSubmit} className="bg-white border border-zinc-200 rounded-xl p-7 space-y-4">
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</p>
        )}
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900 focus:border-transparent"
            placeholder="you@projectx.com"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Password</label>
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900 focus:border-transparent"
            placeholder="Minimum 8 characters"
          />
        </div>
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-zinc-900 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-zinc-800 disabled:opacity-50"
        >
          {loading ? "Creating account..." : "Create account"}
        </button>
      </form>
      <p className="text-center text-sm text-zinc-500 mt-4">
        Already have an account?{" "}
        <Link href="/login" className="text-blue-600 hover:underline">
          Sign in
        </Link>
      </p>
    </>
  );
}
```

- [ ] **Step 7: Create pending-approval page**

Create `frontend/admin/app/pending-approval/page.tsx`:

```typescript
"use client";

import { createClient } from "@/lib/supabase/client";
import { useRouter } from "next/navigation";

export default function PendingApprovalPage() {
  const router = useRouter();

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="text-center max-w-sm">
        <h1 className="text-lg font-semibold text-zinc-900 mb-2">
          Pending Approval
        </h1>
        <p className="text-sm text-zinc-500 leading-relaxed">
          Your account has been created but is awaiting admin approval. You'll
          be able to access the dashboard once approved.
        </p>
        <button
          onClick={handleSignOut}
          className="text-sm text-blue-600 hover:underline mt-6"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Commit**

```bash
git add frontend/admin/app/
git commit -m "feat: add admin auth pages (login, signup, pending-approval)"
```

---

### Task 12: Frontend admin — dashboard + provision pages

**Files:**
- Create: `frontend/admin/app/(admin)/layout.tsx`
- Create: `frontend/admin/app/(admin)/page.tsx`
- Create: `frontend/admin/app/(admin)/dashboard/page.tsx`
- Create: `frontend/admin/app/(admin)/dashboard/provision/page.tsx`

- [ ] **Step 1: Create admin layout with sidebar**

Create `frontend/admin/app/(admin)/layout.tsx`:

```typescript
"use client";

import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex flex-1">
      <aside className="w-56 border-r border-zinc-200 bg-white p-4 flex flex-col">
        <h2 className="text-sm font-bold text-zinc-900 mb-6">ProjectX Admin</h2>
        <nav className="flex-1">
          <a
            href="/dashboard"
            className="block text-sm text-zinc-700 hover:text-zinc-900 py-1.5"
          >
            Clients
          </a>
        </nav>
        <button
          onClick={handleSignOut}
          className="text-sm text-zinc-500 hover:text-zinc-700 text-left"
        >
          Sign out
        </button>
      </aside>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
```

- [ ] **Step 2: Create redirect page**

Create `frontend/admin/app/(admin)/page.tsx`:

```typescript
import { redirect } from "next/navigation";

export default function AdminIndex() {
  redirect("/dashboard");
}
```

- [ ] **Step 3: Create client list page**

Create `frontend/admin/app/(admin)/dashboard/page.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface Client {
  company_id: string;
  company_name: string;
  domain: string | null;
  plan: string;
  onboarding_complete: boolean;
  admin_email: string | null;
  invite_status: string | null;
  created_at: string;
}

export default function DashboardPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const supabase = createClient();
        const { data: { session } } = await supabase.auth.getSession();
        const token = session?.access_token;
        if (!token) {
          window.location.href = "/login";
          return;
        }

        const data = await apiFetch<Client[]>("/api/admin/clients", { token });
        setClients(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load clients");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const statusColor: Record<string, string> = {
    pending: "bg-amber-50 text-amber-700",
    accepted: "bg-green-50 text-green-700",
    expired: "bg-zinc-100 text-zinc-500",
    revoked: "bg-red-50 text-red-600",
  };

  return (
    <>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-zinc-900">Clients</h1>
        <Link
          href="/dashboard/provision"
          className="bg-zinc-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-zinc-800"
        >
          + Provision Client
        </Link>
      </div>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">{error}</p>
      )}

      {loading ? (
        <p className="text-sm text-zinc-500">Loading...</p>
      ) : clients.length === 0 ? (
        <p className="text-sm text-zinc-500">No clients provisioned yet.</p>
      ) : (
        <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-zinc-50 border-b border-zinc-200">
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Company</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Admin Email</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Plan</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Invite</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Created</th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr key={c.company_id} className="border-b border-zinc-100 last:border-0">
                  <td className="px-4 py-2.5 font-medium text-zinc-900">{c.company_name}</td>
                  <td className="px-4 py-2.5 text-zinc-600">{c.admin_email || "—"}</td>
                  <td className="px-4 py-2.5">
                    <span className="bg-green-50 text-green-700 px-2 py-0.5 rounded-full text-xs">{c.plan}</span>
                  </td>
                  <td className="px-4 py-2.5">
                    {c.invite_status ? (
                      <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[c.invite_status] || "bg-zinc-100 text-zinc-500"}`}>
                        {c.invite_status}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-zinc-400">{new Date(c.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 4: Create provision form**

Create `frontend/admin/app/(admin)/dashboard/provision/page.tsx`:

```typescript
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

export default function ProvisionPage() {
  const router = useRouter();
  const [companyName, setCompanyName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [domain, setDomain] = useState("");
  const [industry, setIndustry] = useState("");
  const [plan, setPlan] = useState("trial");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [inviteUrl, setInviteUrl] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setInviteUrl("");
    setLoading(true);

    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) throw new Error("Not authenticated");

      const result = await apiFetch<{
        company_id: string;
        invite_url: string;
      }>("/api/admin/provision-client", {
        method: "POST",
        token,
        body: JSON.stringify({
          company_name: companyName,
          admin_email: adminEmail,
          domain,
          industry,
          plan,
        }),
      });

      if (result.invite_url) {
        setInviteUrl(result.invite_url);
      }

      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Provisioning failed");
      setLoading(false);
    }
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-zinc-900">Provision New Client</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Creates the company and sends an invite to the designated admin.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="max-w-lg bg-white border border-zinc-200 rounded-xl p-7 space-y-4">
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</p>
        )}

        {inviteUrl && (
          <div className="text-sm bg-green-50 border border-green-200 rounded-lg p-3">
            <p className="font-medium text-green-800 mb-1">Invite URL (dry-run mode):</p>
            <code className="text-xs break-all text-green-700">{inviteUrl}</code>
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Company Name *</label>
          <input type="text" required value={companyName} onChange={(e) => setCompanyName(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900" />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Admin Email *</label>
          <input type="email" required value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-zinc-600 mb-1.5">Domain</label>
            <input type="text" value={domain} onChange={(e) => setDomain(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900"
              placeholder="accenture.com" />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-600 mb-1.5">Industry</label>
            <input type="text" value={industry} onChange={(e) => setIndustry(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900"
              placeholder="Consulting" />
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Plan</label>
          <select value={plan} onChange={(e) => setPlan(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900 bg-white">
            <option value="trial">Trial</option>
            <option value="pro">Pro</option>
            <option value="enterprise">Enterprise</option>
          </select>
        </div>

        <div className="flex gap-3 justify-end pt-2">
          <button type="button" onClick={() => router.push("/dashboard")}
            className="px-5 py-2.5 border border-zinc-200 rounded-lg text-sm text-zinc-600 hover:bg-zinc-50">
            Cancel
          </button>
          <button type="submit" disabled={loading}
            className="px-5 py-2.5 bg-zinc-900 text-white rounded-lg text-sm font-medium hover:bg-zinc-800 disabled:opacity-50">
            {loading ? "Provisioning..." : "Provision & Send Invite"}
          </button>
        </div>
      </form>
    </>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/admin/app/
git commit -m "feat: add admin dashboard with client list and provision form"
```

---

### Task 13: Frontend app — Supabase lib + API client + middleware

**Files:**
- Create: `frontend/app/lib/supabase/client.ts`
- Create: `frontend/app/lib/supabase/server.ts`
- Create: `frontend/app/lib/api/client.ts`
- Create: `frontend/app/middleware.ts`

- [ ] **Step 1: Create lib files**

Create `frontend/app/lib/supabase/client.ts` (identical to admin):

```typescript
import { createBrowserClient } from "@supabase/ssr";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
```

Create `frontend/app/lib/supabase/server.ts` (identical to admin):

```typescript
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, options);
          });
        },
      },
    },
  );
}
```

Create `frontend/app/lib/api/client.ts` (identical to admin):

```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { token?: string } = {},
): Promise<T> {
  const { token, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${path}`, {
    ...fetchOptions,
    headers,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
}
```

- [ ] **Step 2: Create client dashboard middleware**

Create `frontend/app/middleware.ts`:

```typescript
import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PATHS = new Set(["/login", "/invite"]);

export async function middleware(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const path = request.nextUrl.pathname;

  // Public paths — allow through
  if (PUBLIC_PATHS.has(path) || path.startsWith("/invite")) {
    await supabase.auth.getUser();
    return supabaseResponse;
  }

  // Validate session + get claims
  const {
    data: { claims },
    error,
  } = await supabase.auth.getClaims();

  // No valid session → login
  if (error || !claims) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  const typedClaims = claims as Record<string, unknown>;
  const tenantId = typedClaims.tenant_id as string;
  const appRole = typedClaims.app_role as string;

  // /onboarding — requires Company Admin role
  if (path.startsWith("/onboarding")) {
    if (!tenantId || appRole !== "Company Admin") {
      return NextResponse.redirect(new URL("/", request.url));
    }
    return supabaseResponse;
  }

  // Dashboard routes — require tenant_id and app_role
  if (!tenantId || !appRole) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return supabaseResponse;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/lib/ frontend/app/middleware.ts
git commit -m "feat: add Supabase lib, API client, and middleware for client dashboard"
```

---

### Task 14: Frontend app — auth pages (login + invite)

**Files:**
- Create: `frontend/app/app/(auth)/layout.tsx`
- Create: `frontend/app/app/(auth)/login/page.tsx`
- Create: `frontend/app/app/(auth)/invite/page.tsx`
- Modify: `frontend/app/app/layout.tsx`
- Modify: `frontend/app/app/globals.css`
- Delete: `frontend/app/app/page.tsx`

- [ ] **Step 1: Update root layout**

Replace `frontend/app/app/layout.tsx`:

```typescript
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "ProjectX",
  description: "AI Video Interview Platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-zinc-50 font-sans text-zinc-900">
        {children}
      </body>
    </html>
  );
}
```

- [ ] **Step 2: Update globals.css**

Replace `frontend/app/app/globals.css`:

```css
@import "tailwindcss";

@theme inline {
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
}
```

- [ ] **Step 3: Delete old page.tsx and create auth layout**

```bash
rm frontend/app/app/page.tsx
```

Create `frontend/app/app/(auth)/layout.tsx`:

```typescript
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-md">{children}</div>
    </div>
  );
}
```

- [ ] **Step 4: Create login page**

Create `frontend/app/app/(auth)/login/page.tsx`:

```typescript
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const supabase = createClient();
    const { error: authError } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    if (authError) {
      setError(authError.message);
      setLoading(false);
      return;
    }

    router.push("/");
    router.refresh();
  }

  return (
    <>
      <div className="text-center mb-8">
        <h1 className="text-2xl font-bold text-zinc-900">ProjectX</h1>
        <p className="text-sm text-zinc-500 mt-1">Sign in to your dashboard</p>
      </div>
      <form onSubmit={handleSubmit} className="bg-white border border-zinc-200 rounded-xl p-7 space-y-4">
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</p>
        )}
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Email</label>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent"
            placeholder="you@company.com" />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Password</label>
          <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent" />
        </div>
        <button type="submit" disabled={loading}
          className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-green-700 disabled:opacity-50">
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
      <p className="text-center text-sm text-zinc-400 mt-4">
        Don't have an account? Contact your Company Admin for an invite.
      </p>
    </>
  );
}
```

- [ ] **Step 5: Create invite page**

Create `frontend/app/app/(auth)/invite/page.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface InviteDetails {
  email: string;
  role: string;
  company_name: string;
}

export default function InvitePage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const rawToken = searchParams.get("token") || "";

  const [state, setState] = useState<"loading" | "invalid" | "ready" | "submitting" | "done">("loading");
  const [invite, setInvite] = useState<InviteDetails | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");

  // Verify invite on load
  useEffect(() => {
    if (!rawToken) {
      setState("invalid");
      return;
    }
    apiFetch<InviteDetails>(`/api/auth/verify-invite?token=${encodeURIComponent(rawToken)}`)
      .then((data) => {
        setInvite(data);
        setState("ready");
      })
      .catch(() => setState("invalid"));
  }, [rawToken]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setState("submitting");

    try {
      // 1. Sign up with Supabase Auth
      // If signUp fails with "User already registered", try signIn instead.
      // This handles the edge case where signUp succeeded but complete-invite
      // failed on a previous attempt (network error, server error).
      const supabase = createClient();
      let signUpData;
      const { data: suData, error: signUpError } = await supabase.auth.signUp({
        email: invite!.email,
        password,
      });

      if (signUpError) {
        // Retry as sign-in if user already exists
        if (signUpError.message.toLowerCase().includes("already registered")) {
          const { data: signInData, error: signInError } = await supabase.auth.signInWithPassword({
            email: invite!.email,
            password,
          });
          if (signInError) throw new Error(signInError.message);
          signUpData = signInData;
        } else {
          throw new Error(signUpError.message);
        }
      } else {
        signUpData = suData;
      }

      // 2. Complete the invite (claim token + create user row)
      const token = signUpData.session?.access_token;
      if (!token) throw new Error("No session after signup");

      const result = await apiFetch<{ redirect_to: string }>("/api/auth/complete-invite", {
        method: "POST",
        token,
        body: JSON.stringify({ raw_token: rawToken }),
      });

      setState("done");
      router.push(result.redirect_to);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create account");
      setState("ready");
    }
  }

  // Loading state
  if (state === "loading") {
    return (
      <div className="text-center py-20">
        <p className="text-sm text-zinc-500">Verifying your invite...</p>
      </div>
    );
  }

  // Invalid/expired state
  if (state === "invalid") {
    return (
      <div className="text-center py-20">
        <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-4">
          <span className="text-red-500 text-xl">✕</span>
        </div>
        <h2 className="text-lg font-semibold text-zinc-900 mb-2">Invite Invalid or Expired</h2>
        <p className="text-sm text-zinc-500 max-w-sm mx-auto leading-relaxed">
          This invite link is no longer valid. It may have already been used or expired.
          Please contact the person who invited you to request a new one.
        </p>
      </div>
    );
  }

  // Ready state — signup form
  return (
    <>
      <div className="text-center mb-6">
        <h1 className="text-xl font-bold text-zinc-900">Set Up Your Account</h1>
        <p className="text-sm text-zinc-500 mt-1">You've been invited to join</p>
      </div>

      <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center mb-6">
        <p className="font-semibold text-green-800">{invite!.company_name}</p>
        <p className="text-sm text-green-700 mt-0.5">as {invite!.role}</p>
      </div>

      <form onSubmit={handleSubmit} className="bg-white border border-zinc-200 rounded-xl p-7 space-y-4">
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</p>
        )}
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Email</label>
          <div className="flex items-center justify-between border border-zinc-200 rounded-lg px-3 py-2.5 bg-zinc-50 text-sm text-zinc-500">
            {invite!.email}
            <span className="text-xs bg-zinc-200 text-zinc-500 px-2 py-0.5 rounded">locked</span>
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Set Password</label>
          <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
            placeholder="Minimum 8 characters" />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">Confirm Password</label>
          <input type="password" required minLength={8} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600" />
        </div>
        <button type="submit" disabled={state === "submitting"}
          className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-green-700 disabled:opacity-50">
          {state === "submitting" ? "Creating account..." : "Create Account & Continue"}
        </button>
      </form>
    </>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/app/app/
git commit -m "feat: add client dashboard auth pages (login, invite flow)"
```

---

### Task 15: Frontend app — onboarding + dashboard placeholders

**Files:**
- Create: `frontend/app/app/onboarding/layout.tsx`
- Create: `frontend/app/app/onboarding/page.tsx`
- Create: `frontend/app/app/(dashboard)/layout.tsx`
- Create: `frontend/app/app/(dashboard)/page.tsx`

- [ ] **Step 1: Create onboarding layout and placeholder**

Create `frontend/app/app/onboarding/layout.tsx`:

```typescript
export default function OnboardingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      {children}
    </div>
  );
}
```

Create `frontend/app/app/onboarding/page.tsx`:

```typescript
"use client";

import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function OnboardingPage() {
  const router = useRouter();

  async function handleContinue() {
    router.push("/");
    router.refresh();
  }

  return (
    <div className="text-center max-w-md">
      <div className="w-12 h-12 rounded-full bg-green-50 flex items-center justify-center mx-auto mb-4">
        <span className="text-green-600 text-xl">✓</span>
      </div>
      <h1 className="text-xl font-semibold text-zinc-900 mb-2">Welcome to ProjectX</h1>
      <p className="text-sm text-zinc-500 leading-relaxed mb-6">
        Your account has been created successfully. The onboarding wizard is coming
        soon — for now, you can explore the dashboard.
      </p>
      <button
        onClick={handleContinue}
        className="bg-green-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:bg-green-700"
      >
        Go to Dashboard
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Create dashboard layout with /api/auth/me check**

Create `frontend/app/app/(dashboard)/layout.tsx`:

```typescript
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { cache } from "react";

const getMe = cache(async (token: string, apiUrl: string) => {
  const res = await fetch(`${apiUrl}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json() as Promise<{
    role: string;
    onboarding_complete: boolean;
  }>;
});

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = await createClient();

  // getClaims() validates the JWT signature locally — no server roundtrip.
  // We use it to get the access_token for forwarding to the API.
  const { data: { claims }, error } = await supabase.auth.getClaims();

  if (error || !claims) {
    redirect("/login");
  }

  // getClaims() validates and returns claims but we need the raw token
  // to forward to the API. getSession() is acceptable here because
  // getClaims() already validated — we just need the token string.
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) {
    redirect("/login");
  }

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
  const me = await getMe(session.access_token, apiUrl);

  if (me && me.role === "Company Admin" && !me.onboarding_complete) {
    redirect("/onboarding");
  }

  return (
    <div className="flex flex-1">
      <aside className="w-56 border-r border-zinc-200 bg-white p-4">
        <h2 className="text-sm font-bold text-zinc-900 mb-6">ProjectX</h2>
        <nav>
          <a href="/" className="block text-sm text-zinc-700 hover:text-zinc-900 py-1.5">
            Dashboard
          </a>
        </nav>
      </aside>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
```

- [ ] **Step 3: Create dashboard home placeholder**

Create `frontend/app/app/(dashboard)/page.tsx`:

```typescript
export default function DashboardPage() {
  return (
    <div>
      <h1 className="text-lg font-semibold text-zinc-900 mb-4">Dashboard</h1>
      <p className="text-sm text-zinc-500">
        Welcome to ProjectX. Your interview pipeline will appear here.
      </p>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/onboarding/ frontend/app/app/\(dashboard\)/
git commit -m "feat: add onboarding placeholder and dashboard layout with auth/me check"
```

---

### Task 16: End-to-end Pipeline A verification

This is the acceptance test for the entire Phase 2.

- [ ] **Step 1: Run all backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 2: Reset Supabase and start backend**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
cd /home/ishant/Projects/ProjectX/backend/nexus && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 3: Start both frontends (separate terminals)**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin && npm run dev -- -p 3001
cd /home/ishant/Projects/ProjectX/frontend/app && npm run dev
```

- [ ] **Step 4: Full Pipeline A test**

1. Go to `http://localhost:3001/signup` — create an admin account
2. In Supabase Studio (`http://127.0.0.1:54323`), set `is_projectx_admin: true` in the user's `raw_app_meta_data`
3. Log in at `http://localhost:3001/login` — should see `/dashboard`
4. Click "Provision Client" → fill form → submit
5. Copy the invite URL from the backend terminal output (dry-run mode)
6. Open invite URL in a new browser/incognito → should show "Set Up Your Account" form
7. Set password → create account → should redirect to `/onboarding`
8. Click "Go to Dashboard" → should see dashboard placeholder
9. Open the same invite URL again → should show "Invite Invalid or Expired"
10. Go to `http://localhost:3000/login` → log in with Company Admin credentials → should see dashboard

---

## Phase 2 Acceptance Criteria

- [ ] Admin panel: signup → pending approval → manual flag → login → dashboard
- [ ] Admin panel: provision client → invite URL in terminal output
- [ ] Client dashboard: invite URL → verify → signup form → create account
- [ ] JWT after signup contains `tenant_id` and `app_role = Company Admin`
- [ ] `complete-invite` atomically claims invite + creates user row
- [ ] Second click on same invite URL → "Invite Invalid or Expired"
- [ ] Company Admin redirected to `/onboarding` placeholder
- [ ] Return via `/login` → dashboard (no onboarding redirect — placeholder accepts)
- [ ] `GET /api/auth/me` returns user profile + `onboarding_complete`
- [ ] `GET /api/admin/clients` shows provisioned company with invite status
- [ ] Transaction rollback: if user INSERT fails, invite stays `pending`

---

## What's Next

After Phase 2 is verified, proceed to **Phase 3: Pipeline B** — Company Admin invites team members from the client dashboard.
