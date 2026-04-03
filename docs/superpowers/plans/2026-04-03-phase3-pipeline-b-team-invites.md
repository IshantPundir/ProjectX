# Phase 3: Pipeline B — Team Invites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Company Admins to invite team members (Recruiter, Hiring Manager, Interviewer, Observer) from the client dashboard's Settings → Team page, reusing the existing invite infrastructure from Phase 2.

**Architecture:** New `settings` backend module with 5 endpoints, all protected by `require_roles("Company Admin")` and scoped to the caller's tenant via `get_tenant_db`. Invite creation reuses the same token generation, email dispatch, and claim flow from Phase 2. Frontend adds a Team management page with invite form + member list + resend/revoke/deactivate actions.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), Next.js 16 + Tailwind v4 (frontend)

**Depends on:** Phase 2 complete (Pipeline A working end-to-end)

**Design spec:** `docs/superpowers/specs/2026-04-03-auth-client-user-onboarding-design.md`

---

## File Map

### Backend — Create

| File | Responsibility |
|---|---|
| `backend/nexus/app/modules/settings/__init__.py` | Package init |
| `backend/nexus/app/modules/settings/schemas.py` | Request/response schemas for team endpoints |
| `backend/nexus/app/modules/settings/service.py` | Team invite, list, resend, revoke, deactivate logic |
| `backend/nexus/app/modules/settings/router.py` | Team management API routes |
| `backend/nexus/app/modules/notifications/templates/team_invite.html` | Jinja2 email template for team member invites |
| `backend/nexus/tests/test_settings.py` | Settings endpoint tests |

### Backend — Modify

| File | What changes |
|---|---|
| `backend/nexus/app/main.py` | Register settings router |

### Frontend — Create

| File | Responsibility |
|---|---|
| `frontend/app/app/(dashboard)/settings/team/page.tsx` | Team management page: invite form + member list + actions |

### Frontend — Modify

| File | What changes |
|---|---|
| `frontend/app/app/(dashboard)/layout.tsx` | Add Settings → Team link in sidebar |

---

### Task 1: Email template for team invites

**Files:**
- Create: `backend/nexus/app/modules/notifications/templates/team_invite.html`

- [ ] **Step 1: Create the template**

Create `backend/nexus/app/modules/notifications/templates/team_invite.html`:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 40px 20px; color: #1a1a1a;">
  <h2 style="margin-bottom: 8px;">You've been invited to join {{ company_name }} on ProjectX</h2>
  <p style="color: #666; margin-bottom: 24px;">You've been added as <strong>{{ role }}</strong>. Click the link below to create your account.</p>
  <a href="{{ invite_url }}" style="display: inline-block; background: #16a34a; color: #fff; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: 500;">Set Up Your Account</a>
  <p style="color: #999; font-size: 14px; margin-top: 24px;">This invite expires in {{ expires_hours }} hours.</p>
  <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
  <p style="color: #999; font-size: 12px;">ProjectX — AI Video Interview Platform</p>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/notifications/templates/team_invite.html
git commit -m "feat: add team member invite email template"
```

---

### Task 2: Settings module — schemas

**Files:**
- Create: `backend/nexus/app/modules/settings/__init__.py`
- Create: `backend/nexus/app/modules/settings/schemas.py`

- [ ] **Step 1: Create the package**

```bash
mkdir -p backend/nexus/app/modules/settings
touch backend/nexus/app/modules/settings/__init__.py
```

- [ ] **Step 2: Create schemas**

Create `backend/nexus/app/modules/settings/schemas.py`:

```python
from pydantic import BaseModel


class TeamInviteRequest(BaseModel):
    email: str
    role: str  # "Recruiter", "Hiring Manager", "Interviewer", "Observer"


class TeamInviteResponse(BaseModel):
    invite_id: str
    email: str
    role: str
    invite_url: str  # Only present in dry-run mode


class TeamMember(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    source: str  # "user" or "invite"
    status: str  # "active", "inactive" for users; "pending", "expired", "revoked" for invites
    created_at: str


class ResendInviteResponse(BaseModel):
    new_invite_id: str
    invite_url: str  # Only present in dry-run mode
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/settings/
git commit -m "feat: add settings module schemas for team management"
```

---

### Task 3: Settings module — service

**Files:**
- Create: `backend/nexus/app/modules/settings/service.py`

- [ ] **Step 1: Create the service**

Create `backend/nexus/app/modules/settings/service.py`:

```python
"""Team management service — invite, list, resend, revoke, deactivate."""

import hashlib
import secrets
import uuid as uuid_mod

import structlog
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Company, User, UserInvite
from app.modules.notifications.service import render_template, send_email

logger = structlog.get_logger()


async def invite_team_member(
    *,
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    email: str,
    role: str,
    invited_by: uuid_mod.UUID,  # the Company Admin's users.id
) -> tuple[UserInvite, str]:
    """Create an invite for a team member and send the email.

    Returns (invite, invite_url_or_empty).
    """
    # Validate role — Company Admin cannot be invited via this flow
    allowed_roles = {"Recruiter", "Hiring Manager", "Interviewer", "Observer"}
    if role not in allowed_roles:
        raise ValueError(f"Invalid role: {role}. Must be one of: {allowed_roles}")

    # Generate invite token
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

    # Get company name for the email
    result = await db.execute(select(Company).where(Company.id == tenant_id))
    company = result.scalar_one()

    # Send invite email
    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"

    html = render_template(
        "team_invite.html",
        company_name=company.name,
        role=role,
        invite_url=invite_url,
        expires_hours=72,
    )
    await send_email(
        to=email,
        subject=f"You've been invited to join {company.name} on ProjectX",
        html=html,
    )

    logger.info(
        "settings.team_member_invited",
        tenant_id=str(tenant_id),
        email=email,
        role=role,
    )

    if settings.notifications_dry_run:
        logger.info("settings.invite_url_dry_run", invite_url=invite_url)

    return invite, invite_url if settings.notifications_dry_run else ""


async def list_team_members(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
) -> list[dict]:
    """List all team members (accepted users) and pending invites for this tenant."""
    members: list[dict] = []

    # Active users
    result = await db.execute(
        select(User)
        .where(User.tenant_id == tenant_id)
        .order_by(User.created_at.asc())
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

    # Pending invites (not yet accepted)
    result = await db.execute(
        select(UserInvite)
        .where(
            UserInvite.tenant_id == tenant_id,
            UserInvite.status.in_(["pending", "expired"]),
        )
        .order_by(UserInvite.created_at.desc())
    )
    for invite in result.scalars().all():
        members.append({
            "id": str(invite.id),
            "email": invite.email,
            "full_name": None,
            "role": invite.role,
            "is_active": False,
            "source": "invite",
            "status": invite.status,
            "created_at": invite.created_at.isoformat(),
        })

    return members


async def resend_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
) -> tuple[UserInvite, str]:
    """Supersede an existing invite and create a new one with a fresh token."""
    # Find the existing invite
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

    # Generate new token
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

    # Supersede the old invite
    existing.status = "superseded"
    existing.superseded_by = new_invite.id

    # Get company name and send email
    company_result = await db.execute(select(Company).where(Company.id == tenant_id))
    company = company_result.scalar_one()

    base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
    invite_url = f"{base_url}/invite?token={raw_token}"

    html = render_template(
        "team_invite.html",
        company_name=company.name,
        role=new_invite.role,
        invite_url=invite_url,
        expires_hours=72,
    )
    await send_email(
        to=new_invite.email,
        subject=f"You've been invited to join {company.name} on ProjectX",
        html=html,
    )

    logger.info("settings.invite_resent", invite_id=str(new_invite.id), email=new_invite.email)

    if settings.notifications_dry_run:
        logger.info("settings.invite_url_dry_run", invite_url=invite_url)

    return new_invite, invite_url if settings.notifications_dry_run else ""


async def revoke_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
) -> None:
    """Revoke a pending invite. Only works on pending invites."""
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


async def deactivate_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
) -> None:
    """Deactivate an accepted user. Sets is_active = false."""
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

    # Prevent deactivating yourself (Company Admin)
    if user.role == "Company Admin":
        raise ValueError("Cannot deactivate the Company Admin")

    user.is_active = False
    logger.info("settings.user_deactivated", user_id=str(user_id), email=user.email)
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/settings/service.py
git commit -m "feat: add team management service (invite, list, resend, revoke, deactivate)"
```

---

### Task 4: Settings module — router

**Files:**
- Create: `backend/nexus/app/modules/settings/router.py`

- [ ] **Step 1: Create the router**

Create `backend/nexus/app/modules/settings/router.py`:

```python
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.middleware.auth import require_roles
from app.modules.settings.schemas import (
    ResendInviteResponse,
    TeamInviteRequest,
    TeamInviteResponse,
    TeamMember,
)
from app.modules.settings.service import (
    deactivate_user,
    invite_team_member,
    list_team_members,
    resend_invite,
    revoke_invite,
)

router = APIRouter(prefix="/api/settings/team", tags=["settings"])


@router.post(
    "/invite",
    response_model=TeamInviteResponse,
    dependencies=[require_roles("Company Admin")],
)
async def invite_endpoint(
    data: TeamInviteRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> TeamInviteResponse:
    """Invite a team member to the company."""
    token_payload = request.state.token_payload
    tenant_id = uuid_mod.UUID(token_payload.tenant_id)

    # Get the Company Admin's users.id for the invited_by FK
    from sqlalchemy import select
    from app.models import User
    result = await db.execute(
        select(User).where(User.auth_user_id == token_payload.sub)
    )
    admin_user = result.scalar_one_or_none()
    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")

    try:
        invite, invite_url = await invite_team_member(
            db=db,
            tenant_id=tenant_id,
            email=data.email,
            role=data.role,
            invited_by=admin_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return TeamInviteResponse(
        invite_id=str(invite.id),
        email=data.email,
        role=data.role,
        invite_url=invite_url,
    )


@router.get(
    "/members",
    response_model=list[TeamMember],
    dependencies=[require_roles("Company Admin")],
)
async def list_members_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> list[TeamMember]:
    """List all team members and pending invites."""
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    members = await list_team_members(db, tenant_id)
    return [TeamMember(**m) for m in members]


@router.post(
    "/resend/{invite_id}",
    response_model=ResendInviteResponse,
    dependencies=[require_roles("Company Admin")],
)
async def resend_endpoint(
    invite_id: str,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> ResendInviteResponse:
    """Resend an invite (supersedes the old one, creates a new token)."""
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    try:
        new_invite, invite_url = await resend_invite(
            db, tenant_id, uuid_mod.UUID(invite_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ResendInviteResponse(
        new_invite_id=str(new_invite.id),
        invite_url=invite_url,
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
        await revoke_invite(db, tenant_id, uuid_mod.UUID(invite_id))
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
    try:
        await deactivate_user(db, tenant_id, uuid_mod.UUID(user_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deactivated"}
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/settings/router.py
git commit -m "feat: add settings router with 5 team management endpoints"
```

---

### Task 5: Register settings router in main.py

**Files:**
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Add settings router**

In `backend/nexus/app/main.py`, add after the admin router import (around line 72):

```python
    from app.modules.settings.router import router as settings_router
```

And add after `application.include_router(admin_router)` (around line 83):

```python
    application.include_router(settings_router)
```

- [ ] **Step 2: Run tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/main.py
git commit -m "feat: register settings router in main.py"
```

---

### Task 6: Backend tests for settings endpoints

**Files:**
- Create: `backend/nexus/tests/test_settings.py`

- [ ] **Step 1: Write tests**

Create `backend/nexus/tests/test_settings.py`:

```python
"""Tests for settings/team endpoints — error paths that don't need a live DB."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


class TestTeamInvite:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/settings/team/invite",
                json={"email": "test@test.com", "role": "Recruiter"},
            )
        assert resp.status_code == 401


class TestTeamMembers:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/team/members")
        assert resp.status_code == 401


class TestRevokeInvite:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/settings/team/revoke/some-id")
        assert resp.status_code == 401


class TestDeactivateUser:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/settings/team/deactivate/some-id")
        assert resp.status_code == 401
```

- [ ] **Step 2: Run all tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests pass (previous 9 + 4 new = 13 total).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_settings.py
git commit -m "test: add settings endpoint auth tests"
```

---

### Task 7: Frontend — Team management page

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/team/page.tsx`
- Modify: `frontend/app/app/(dashboard)/layout.tsx`

- [ ] **Step 1: Add Settings link to dashboard sidebar**

In `frontend/app/app/(dashboard)/layout.tsx`, replace the `<nav>` section (lines 50-57):

```typescript
        <nav className="flex-1 space-y-1">
          <a
            href="/"
            className="block text-sm text-zinc-700 hover:text-zinc-900 py-1.5"
          >
            Dashboard
          </a>
          <a
            href="/settings/team"
            className="block text-sm text-zinc-700 hover:text-zinc-900 py-1.5"
          >
            Team
          </a>
        </nav>
```

- [ ] **Step 2: Create the team management page**

Create `frontend/app/app/(dashboard)/settings/team/page.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface TeamMember {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  source: "user" | "invite";
  status: string;
  created_at: string;
}

export default function TeamPage() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Invite form state
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("Recruiter");
  const [inviteLoading, setInviteLoading] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState("");

  async function getToken() {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      window.location.href = "/login";
      return null;
    }
    return session.access_token;
  }

  async function loadMembers() {
    try {
      const token = await getToken();
      if (!token) return;
      const data = await apiFetch<TeamMember[]>("/api/settings/team/members", { token });
      setMembers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load team");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadMembers(); }, []);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setInviteLoading(true);
    setError("");
    setInviteSuccess("");

    try {
      const token = await getToken();
      if (!token) return;
      const result = await apiFetch<{ invite_url: string }>("/api/settings/team/invite", {
        method: "POST",
        token,
        body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
      });

      setInviteEmail("");
      setInviteSuccess(result.invite_url
        ? `Invite sent! URL: ${result.invite_url}`
        : "Invite sent!");
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send invite");
    } finally {
      setInviteLoading(false);
    }
  }

  async function handleResend(inviteId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/resend/" + inviteId, { method: "POST", token });
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend");
    }
  }

  async function handleRevoke(inviteId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/revoke/" + inviteId, { method: "POST", token });
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke");
    }
  }

  async function handleDeactivate(userId: string) {
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch("/api/settings/team/deactivate/" + userId, { method: "POST", token });
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to deactivate");
    }
  }

  const statusColor: Record<string, string> = {
    active: "bg-green-50 text-green-700",
    inactive: "bg-zinc-100 text-zinc-500",
    pending: "bg-amber-50 text-amber-700",
    expired: "bg-zinc-100 text-zinc-500",
    revoked: "bg-red-50 text-red-600",
  };

  const users = members.filter((m) => m.source === "user");
  const invites = members.filter((m) => m.source === "invite");

  return (
    <>
      <h1 className="text-lg font-semibold text-zinc-900 mb-6">Team Management</h1>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">{error}</p>
      )}
      {inviteSuccess && (
        <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg p-3 mb-4">
          {inviteSuccess}
        </div>
      )}

      {/* Invite form */}
      <form onSubmit={handleInvite} className="bg-white border border-zinc-200 rounded-lg p-5 mb-6">
        <h2 className="text-sm font-medium text-zinc-900 mb-3">Invite Team Member</h2>
        <div className="flex gap-3 items-end">
          <div className="flex-1">
            <label className="block text-xs font-medium text-zinc-600 mb-1">Email</label>
            <input
              type="email"
              required
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
              placeholder="colleague@company.com"
            />
          </div>
          <div className="w-48">
            <label className="block text-xs font-medium text-zinc-600 mb-1">Role</label>
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
            >
              <option value="Recruiter">Recruiter</option>
              <option value="Hiring Manager">Hiring Manager</option>
              <option value="Interviewer">Interviewer</option>
              <option value="Observer">Observer</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={inviteLoading}
            className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
          >
            {inviteLoading ? "Sending..." : "Send Invite"}
          </button>
        </div>
      </form>

      {loading ? (
        <p className="text-sm text-zinc-500">Loading team...</p>
      ) : (
        <>
          {/* Active members */}
          <h2 className="text-sm font-medium text-zinc-900 mb-3">Members ({users.length})</h2>
          {users.length === 0 ? (
            <p className="text-sm text-zinc-500 mb-6">No team members yet.</p>
          ) : (
            <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden mb-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-zinc-50 border-b border-zinc-200">
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Email</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Name</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Role</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((m) => (
                    <tr key={m.id} className="border-b border-zinc-100 last:border-0">
                      <td className="px-4 py-2.5 text-zinc-900">{m.email}</td>
                      <td className="px-4 py-2.5 text-zinc-600">{m.full_name || "—"}</td>
                      <td className="px-4 py-2.5 text-zinc-600">{m.role}</td>
                      <td className="px-4 py-2.5">
                        <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[m.status] || ""}`}>
                          {m.status}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        {m.role !== "Company Admin" && m.is_active && (
                          <button
                            onClick={() => handleDeactivate(m.id)}
                            className="text-xs text-red-600 hover:underline"
                          >
                            Deactivate
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pending invites */}
          {invites.length > 0 && (
            <>
              <h2 className="text-sm font-medium text-zinc-900 mb-3">Pending Invites ({invites.length})</h2>
              <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-zinc-50 border-b border-zinc-200">
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Email</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Role</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {invites.map((m) => (
                      <tr key={m.id} className="border-b border-zinc-100 last:border-0">
                        <td className="px-4 py-2.5 text-zinc-900">{m.email}</td>
                        <td className="px-4 py-2.5 text-zinc-600">{m.role}</td>
                        <td className="px-4 py-2.5">
                          <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[m.status] || ""}`}>
                            {m.status}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 space-x-3">
                          {m.status === "pending" && (
                            <>
                              <button
                                onClick={() => handleResend(m.id)}
                                className="text-xs text-blue-600 hover:underline"
                              >
                                Resend
                              </button>
                              <button
                                onClick={() => handleRevoke(m.id)}
                                className="text-xs text-red-600 hover:underline"
                              >
                                Revoke
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/app/\(dashboard\)/settings/team/page.tsx frontend/app/app/\(dashboard\)/layout.tsx
git commit -m "feat: add team management page with invite form and member list"
```

---

### Task 8: End-to-end Pipeline B verification

- [ ] **Step 1: Run all backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All 13 tests pass.

- [ ] **Step 2: Verify frontend builds**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app && npm run build 2>&1 | tail -15
```

Expected: Build succeeds with `/settings/team` route listed.

- [ ] **Step 3: Full Pipeline B test**

1. Log into client dashboard as Company Admin (`ishant@binqle.com`)
2. Go to Settings → Team (sidebar link)
3. Invite a Recruiter: enter email + select "Recruiter" → Send Invite
4. Copy invite URL from backend terminal (dry-run mode)
5. Open invite URL in incognito → set password → claim invite
6. Sign in as the Recruiter → should see the dashboard (no onboarding redirect)
7. Back as Company Admin → Team page should show:
   - Company Admin in Members (no Deactivate button)
   - Recruiter in Members (with Deactivate button)
8. Test Resend: invite another user, then click Resend → old invite superseded
9. Test Revoke: invite another user, then click Revoke → invite shows as revoked
10. Test Deactivate: click Deactivate on the Recruiter → status shows inactive

---

## Phase 3 Acceptance Criteria

- [ ] `POST /api/settings/team/invite` creates invite + sends email, scoped to tenant
- [ ] `GET /api/settings/team/members` returns both users and pending invites
- [ ] `POST /api/settings/team/resend/{id}` supersedes old invite, creates new one
- [ ] `POST /api/settings/team/revoke/{id}` sets status to 'revoked' (pending only)
- [ ] `POST /api/settings/team/deactivate/{id}` sets `is_active = false` (not Company Admin)
- [ ] All 5 endpoints require `Company Admin` role (403 for other roles)
- [ ] Team member invite uses existing `complete-invite` endpoint to claim
- [ ] Invite URL from team invite works in the `/invite` page (same flow as Pipeline A)
- [ ] Recruiter lands on `/(dashboard)` after claiming (not `/onboarding`)
- [ ] Company Admin cannot deactivate themselves
- [ ] RLS scopes all queries to the caller's tenant

---

## What's Next

After Phase 3 is verified, proceed to **Phase 4: Onboarding Wizard** — replaces the Phase 2 placeholder with a multi-step wizard for Company Admin.
