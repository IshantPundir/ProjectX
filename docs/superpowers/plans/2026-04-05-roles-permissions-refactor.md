# Roles & Permissions Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the authorization model so roles live on org-unit assignments (not users), permissions derive from a `roles` table, and super admin is stored on `clients`.

**Architecture:** Clean-slate Supabase migration replacing 9 files with 1. Backend models, auth middleware, JWT hook, and all API modules rewritten to use `UserContext` loaded via a single JOIN query per request. Frontend updated to consume new response shapes.

**Tech Stack:** FastAPI, SQLAlchemy async, Supabase (Postgres + Auth), Next.js 14 App Router, TypeScript, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-04-05-roles-permissions-refactor-design.md`

---

## File Structure

### Files to delete
- `backend/supabase/migrations/20260403000000_create_auth_tables.sql`
- `backend/supabase/migrations/20260403000001_auth_hook.sql`
- `backend/supabase/migrations/20260403000002_auth_hook_rls_policy.sql`
- `backend/supabase/migrations/20260404000000_rename_clients_add_org_units.sql`
- `backend/supabase/migrations/20260404000001_auth_hook_v2.sql`
- `backend/supabase/migrations/20260404000002_super_admin_unique.sql`
- `backend/supabase/migrations/20260404000003_user_org_assignments.sql`
- `backend/supabase/migrations/20260404000004_nullable_role.sql`
- `backend/supabase/migrations/20260404000005_role_on_assignment.sql`

### Files to create
- `backend/supabase/migrations/20260405000000_initial_schema.sql` — single clean migration
- `backend/nexus/app/modules/auth/context.py` — `UserContext`, `get_current_user_roles` dependency
- `backend/nexus/app/modules/roles/router.py` — `GET /api/roles` endpoint
- `backend/nexus/app/modules/roles/schemas.py` — role response schema
- `backend/nexus/tests/test_user_context.py` — tests for UserContext
- `backend/nexus/tests/test_roles.py` — tests for roles endpoint

### Files to rewrite
- `backend/nexus/app/models.py` — new schema (Client, User, OrganizationalUnit, Role, UserRoleAssignment, UserInvite)
- `backend/nexus/app/modules/auth/schemas.py` — TokenPayload simplified, new MeResponse
- `backend/nexus/app/modules/auth/permissions.py` — remove old validation, keep ALL_PERMISSIONS constant
- `backend/nexus/app/middleware/auth.py` — remove `require_roles`, remove stale state fields
- `backend/nexus/app/modules/auth/router.py` — rewrite all 4 endpoints
- `backend/nexus/app/modules/admin/service.py` — remove role/permissions from invite creation
- `backend/nexus/app/modules/admin/schemas.py` — remove invite_status from list (references old role)
- `backend/nexus/app/modules/settings/router.py` — use UserContext, super admin checks
- `backend/nexus/app/modules/settings/service.py` — rewrite list/invite/deactivate
- `backend/nexus/app/modules/settings/schemas.py` — new TeamMember shape with assignments
- `backend/nexus/app/modules/org_units/router.py` — rewrite for role assignment endpoints
- `backend/nexus/app/modules/org_units/service.py` — rewrite for new authorization model
- `backend/nexus/app/modules/org_units/schemas.py` — new member response with roles array
- `backend/nexus/app/main.py` — register roles router
- `backend/nexus/tests/test_permissions.py` — update for new permissions module
- `backend/nexus/tests/test_auth_endpoints.py` — update for new response shapes
- `backend/nexus/tests/test_settings.py` — update for new auth model
- `backend/nexus/tests/test_org_units.py` — update for new auth model

### Frontend files to rewrite
- `frontend/app/app/(auth)/invite/page.tsx` — remove role display
- `frontend/app/app/(dashboard)/layout.tsx` — use `is_super_admin` instead of `role`
- `frontend/app/app/(dashboard)/profile/page.tsx` — show assignments grouped by unit
- `frontend/app/app/(dashboard)/settings/team/page.tsx` — new response shape, super admin gating
- `frontend/app/app/(dashboard)/settings/org-units/page.tsx` — role management UI

---

## Task 1: Clean-Slate Supabase Migration

**Files:**
- Delete: all 9 files in `backend/supabase/migrations/`
- Create: `backend/supabase/migrations/20260405000000_initial_schema.sql`

- [ ] **Step 1: Delete old migrations**

```bash
rm backend/supabase/migrations/20260403000000_create_auth_tables.sql
rm backend/supabase/migrations/20260403000001_auth_hook.sql
rm backend/supabase/migrations/20260403000002_auth_hook_rls_policy.sql
rm backend/supabase/migrations/20260404000000_rename_clients_add_org_units.sql
rm backend/supabase/migrations/20260404000001_auth_hook_v2.sql
rm backend/supabase/migrations/20260404000002_super_admin_unique.sql
rm backend/supabase/migrations/20260404000003_user_org_assignments.sql
rm backend/supabase/migrations/20260404000004_nullable_role.sql
rm backend/supabase/migrations/20260404000005_role_on_assignment.sql
```

- [ ] **Step 2: Write the single initial migration**

Create `backend/supabase/migrations/20260405000000_initial_schema.sql`:

```sql
-- =============================================================
-- ProjectX Initial Schema (clean slate)
-- Tables: clients, users, organizational_units, roles,
--         user_role_assignments, user_invites
-- Includes: RLS policies, auth hook, system role seeds
-- =============================================================

-- ─── 1. clients ──────────────────────────────────────────────
CREATE TABLE public.clients (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    domain      TEXT DEFAULT '',
    industry    TEXT DEFAULT '',
    size        TEXT DEFAULT '',
    logo_url    TEXT,
    plan        TEXT NOT NULL DEFAULT 'trial'
                    CHECK (plan IN ('trial', 'pro', 'enterprise')),
    onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
    super_admin_id UUID,                     -- FK added after users table
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_read" ON public.clients
    FOR SELECT USING (id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.clients
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 2. users ────────────────────────────────────────────────
CREATE TABLE public.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_user_id    UUID NOT NULL UNIQUE,
    tenant_id       UUID NOT NULL REFERENCES public.clients(id),
    email           TEXT NOT NULL,
    full_name       TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX users_tenant_id_idx ON public.users (tenant_id);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.users
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.users
    USING (current_setting('app.bypass_rls', true) = 'true');

-- Now add the deferred FK from clients → users
ALTER TABLE public.clients
    ADD CONSTRAINT clients_super_admin_id_fk
    FOREIGN KEY (super_admin_id) REFERENCES public.users(id)
    DEFERRABLE INITIALLY DEFERRED;

-- ─── 3. organizational_units ─────────────────────────────────
CREATE TABLE public.organizational_units (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES public.clients(id),
    parent_unit_id  UUID REFERENCES public.organizational_units(id),
    name            TEXT NOT NULL,
    unit_type       TEXT NOT NULL
                        CHECK (unit_type IN ('client_account', 'department', 'team', 'branch', 'region')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX org_units_client_id_idx ON public.organizational_units (client_id);
CREATE INDEX org_units_parent_unit_id_idx ON public.organizational_units (parent_unit_id);

ALTER TABLE public.organizational_units ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.organizational_units
    FOR SELECT USING (client_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.organizational_units
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 4. roles ────────────────────────────────────────────────
CREATE TABLE public.roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES public.clients(id),
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_system   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT roles_unique_name UNIQUE NULLS NOT DISTINCT (tenant_id, name)
);

CREATE INDEX roles_is_system_idx ON public.roles (is_system);
CREATE INDEX roles_tenant_id_idx ON public.roles (tenant_id);

ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "roles_visibility" ON public.roles
    FOR SELECT USING (
        tenant_id IS NULL
        OR tenant_id = current_setting('app.current_tenant', true)::UUID
    );
CREATE POLICY "service_bypass" ON public.roles
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 5. user_role_assignments ────────────────────────────────
CREATE TABLE public.user_role_assignments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id),
    org_unit_id UUID NOT NULL REFERENCES public.organizational_units(id),
    role_id     UUID NOT NULL REFERENCES public.roles(id),
    tenant_id   UUID NOT NULL REFERENCES public.clients(id),
    assigned_by UUID REFERENCES public.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT unique_user_unit_role UNIQUE (user_id, org_unit_id, role_id)
);

CREATE INDEX ura_user_id_idx ON public.user_role_assignments (user_id);
CREATE INDEX ura_org_unit_id_idx ON public.user_role_assignments (org_unit_id);
CREATE INDEX ura_tenant_id_idx ON public.user_role_assignments (tenant_id);

ALTER TABLE public.user_role_assignments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.user_role_assignments
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.user_role_assignments
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 6. user_invites ────────────────────────────────────────
CREATE TABLE public.user_invites (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES public.clients(id),
    invited_by          UUID REFERENCES public.users(id),
    projectx_admin_id   TEXT,
    email               TEXT NOT NULL,
    token_hash          TEXT NOT NULL UNIQUE,
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'accepted', 'superseded', 'expired', 'revoked')),
    superseded_by       UUID REFERENCES public.user_invites(id),
    expires_at          TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '72 hours',
    accepted_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT invite_origin_xor CHECK (
        (invited_by IS NOT NULL AND projectx_admin_id IS NULL) OR
        (invited_by IS NULL AND projectx_admin_id IS NOT NULL)
    )
);

CREATE INDEX invites_tenant_id_idx ON public.user_invites (tenant_id);
CREATE INDEX invites_email_status_idx ON public.user_invites (email, status);

ALTER TABLE public.user_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.user_invites
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.user_invites
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 7. Seed system roles ───────────────────────────────────
INSERT INTO public.roles (tenant_id, name, description, permissions, is_system) VALUES
(NULL, 'Admin', 'Full control of an organizational unit',
 '["users.invite_admins","users.invite_users","users.deactivate","org_units.create","org_units.manage","jobs.create","jobs.manage","candidates.view","candidates.evaluate","candidates.advance","interviews.schedule","interviews.conduct","reports.view","reports.export","settings.client","settings.integrations"]'::jsonb,
 TRUE),
(NULL, 'Recruiter', 'Manages job pipelines and candidate flow',
 '["jobs.create","jobs.manage","candidates.view","candidates.advance","interviews.schedule","reports.view"]'::jsonb,
 TRUE),
(NULL, 'Hiring Manager', 'Reviews and evaluates candidates',
 '["candidates.view","candidates.evaluate","candidates.advance","reports.view","reports.export"]'::jsonb,
 TRUE),
(NULL, 'Interviewer', 'Conducts live interviews',
 '["interviews.conduct","candidates.view","candidates.evaluate"]'::jsonb,
 TRUE),
(NULL, 'Observer', 'Read-only access to candidates and reports',
 '["candidates.view","reports.view"]'::jsonb,
 TRUE);

-- ─── 8. Auth hook ───────────────────────────────────────────
-- Injects tenant_id and is_projectx_admin into JWT claims.
-- No role/permission data in JWT — loaded per-request from DB.
CREATE OR REPLACE FUNCTION public.projectx_custom_access_token_hook(event JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path = public
STABLE
AS $$
DECLARE
    claims      JSONB;
    user_meta   JSONB;
    v_tenant_id UUID;
    v_email     TEXT;
    auth_method TEXT;
BEGIN
    claims    := event->'claims';
    user_meta := event->'user_metadata';

    -- 1. ProjectX admin early return
    IF (claims->'app_metadata'->>'is_projectx_admin')::BOOLEAN IS TRUE THEN
        claims := jsonb_set(claims, '{tenant_id}', '""');
        claims := jsonb_set(claims, '{is_projectx_admin}', 'true');
        event  := jsonb_set(event, '{claims}', claims);
        RETURN event;
    END IF;

    -- 2. Look up existing user
    SELECT u.tenant_id INTO v_tenant_id
    FROM public.users u
    WHERE u.auth_user_id = (claims->>'sub')::UUID
      AND u.is_active = TRUE
    LIMIT 1;

    IF v_tenant_id IS NOT NULL THEN
        claims := jsonb_set(claims, '{tenant_id}', to_jsonb(v_tenant_id::TEXT));
        claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
        event  := jsonb_set(event, '{claims}', claims);
        RETURN event;
    END IF;

    -- 3. Skip invite lookup on token refresh (avoid latency)
    auth_method := event->'authentication_method'->>'method';
    IF auth_method = 'token_refresh' THEN
        claims := jsonb_set(claims, '{tenant_id}', '""');
        claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
        event  := jsonb_set(event, '{claims}', claims);
        RETURN event;
    END IF;

    -- 4. Look up pending invite by email
    v_email := claims->>'email';
    IF v_email IS NOT NULL AND v_email != '' THEN
        SELECT i.tenant_id INTO v_tenant_id
        FROM public.user_invites i
        WHERE i.email = v_email
          AND i.status = 'pending'
          AND i.expires_at > NOW()
        ORDER BY i.created_at DESC
        LIMIT 1;

        IF v_tenant_id IS NOT NULL THEN
            claims := jsonb_set(claims, '{tenant_id}', to_jsonb(v_tenant_id::TEXT));
            claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
            event  := jsonb_set(event, '{claims}', claims);
            RETURN event;
        END IF;
    END IF;

    -- 5. No match — safe defaults
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    event  := jsonb_set(event, '{claims}', claims);
    RETURN event;

EXCEPTION WHEN OTHERS THEN
    -- Never block login
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    event  := jsonb_set(event, '{claims}', claims);
    RETURN event;
END;
$$;

-- Auth hook grants
GRANT USAGE ON SCHEMA public TO supabase_auth_admin;
GRANT EXECUTE ON FUNCTION public.projectx_custom_access_token_hook TO supabase_auth_admin;
GRANT SELECT ON public.users TO supabase_auth_admin;
GRANT SELECT ON public.user_invites TO supabase_auth_admin;

-- RLS policies for supabase_auth_admin (not a superuser, RLS applies)
CREATE POLICY "auth_hook_users_read" ON public.users
    FOR SELECT TO supabase_auth_admin USING (TRUE);
CREATE POLICY "auth_hook_invites_read" ON public.user_invites
    FOR SELECT TO supabase_auth_admin USING (TRUE);
```

- [ ] **Step 3: Reset local Supabase**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
```

Expected: clean database with 6 tables, 5 seeded system roles, auth hook installed.

- [ ] **Step 4: Verify migration**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset 2>&1 | tail -5
```

Expected: no errors. Verify tables exist:

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset && echo "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;" | supabase db execute
```

Expected output includes: `clients`, `organizational_units`, `roles`, `user_invites`, `user_role_assignments`, `users`.

- [ ] **Step 5: Commit**

```bash
git add -A backend/supabase/migrations/
git commit -m "db: clean-slate migration with roles table and simplified auth hook"
```

---

## Task 2: Rewrite SQLAlchemy Models

**Files:**
- Rewrite: `backend/nexus/app/models.py`

- [ ] **Step 1: Rewrite models.py**

Replace the entire file with:

```python
"""SQLAlchemy ORM models.

Tables: clients, users, organizational_units, roles,
        user_role_assignments, user_invites
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Client(Base):
    """Tenant root."""
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, server_default="''")
    industry: Mapped[str] = mapped_column(Text, server_default="''")
    size: Mapped[str] = mapped_column(Text, server_default="''")
    logo_url: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String, nullable=False, server_default="trial")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    super_admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", deferrable=True, initially="DEFERRED"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    """Dashboard user — identity only. Roles live on user_role_assignments."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    auth_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationalUnit(Base):
    __tablename__ = "organizational_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Role(Base):
    """Role definition — system or tenant-custom."""
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="roles_unique_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default="''")
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class UserRoleAssignment(Base):
    """Junction: user assigned to org unit with a specific role."""
    __tablename__ = "user_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "org_unit_id", "role_id", name="unique_user_unit_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class UserInvite(Base):
    """Invite to join a tenant — no role info, just email + token."""
    __tablename__ = "user_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    projectx_admin_id: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user_invites.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW() + INTERVAL '72 hours'"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 2: Verify import works**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -c "from app.models import Client, User, OrganizationalUnit, Role, UserRoleAssignment, UserInvite; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/models.py
git commit -m "models: rewrite for roles/permissions refactor — identity-only users, roles table, role assignments"
```

---

## Task 3: Simplify Auth Schemas and Token Payload

**Files:**
- Rewrite: `backend/nexus/app/modules/auth/schemas.py`

- [ ] **Step 1: Rewrite schemas.py**

Replace the entire file with:

```python
"""Auth schemas — JWT payload, invite/me responses."""

from pydantic import BaseModel


class TokenPayload(BaseModel):
    """Decoded JWT payload from Supabase Auth (ES256 via JWKS).

    Thin JWT: only sub, tenant_id, is_projectx_admin.
    Role/permission data is loaded per-request from DB.
    """
    sub: str                         # Supabase Auth user UUID
    tenant_id: str = ""              # company UUID (empty for admins and pre-onboarding)
    email: str = ""
    role: str = "authenticated"      # Postgres role — always "authenticated", NOT for RBAC
    is_projectx_admin: bool = False  # True only for ProjectX internal team
    exp: int = 0


class CandidateTokenPayload(BaseModel):
    """Decoded JWT for single-use candidate session tokens (HS256)."""
    sub: str = ""
    session_id: str = ""
    tenant_id: str = ""
    exp: int = 0
    iat: int = 0


class VerifyInviteResponse(BaseModel):
    email: str
    client_name: str


class CompleteInviteRequest(BaseModel):
    raw_token: str


class CompleteInviteResponse(BaseModel):
    redirect_to: str  # "/onboarding" or "/"
    user_id: str
    tenant_id: str


class RoleAssignmentResponse(BaseModel):
    org_unit_id: str
    org_unit_name: str
    role_name: str
    permissions: list[str]


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    tenant_id: str
    client_name: str
    is_super_admin: bool
    onboarding_complete: bool
    has_org_units: bool
    assignments: list[RoleAssignmentResponse]
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/auth/schemas.py
git commit -m "schemas: thin TokenPayload, new MeResponse with assignments"
```

---

## Task 4: Simplify Auth Service (JWT Verification)

**Files:**
- Modify: `backend/nexus/app/modules/auth/service.py`

- [ ] **Step 1: Update verify_access_token to match new TokenPayload**

Replace `verify_access_token` function (keep the rest of the file unchanged):

```python
def verify_access_token(token: str) -> TokenPayload | None:
    """Verify a dashboard user JWT via JWKS (ES256).

    Returns None if the token is invalid, expired, or malformed.
    """
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            options={"verify_exp": True, "verify_aud": False},
        )
        return TokenPayload(
            sub=payload["sub"],
            tenant_id=payload.get("tenant_id", ""),
            email=payload.get("email", ""),
            role=payload.get("role", "authenticated"),
            is_projectx_admin=payload.get("is_projectx_admin", False),
            exp=payload.get("exp", 0),
        )
    except jwt.ExpiredSignatureError:
        logger.warning("auth.token_expired")
        return None
    except Exception as exc:
        logger.warning("auth.token_invalid", error=str(exc))
        return None
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/auth/service.py
git commit -m "auth: remove app_role/is_admin/org_unit_id from JWT verification"
```

---

## Task 5: Simplify Auth Middleware

**Files:**
- Rewrite: `backend/nexus/app/middleware/auth.py`

- [ ] **Step 1: Rewrite auth.py**

Replace the entire file with:

```python
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.modules.auth.service import verify_access_token

logger = structlog.get_logger()

# Routes that skip authentication entirely
_PUBLIC_PATHS: set[str] = {
    "/health",
    "/docs",
    "/openapi.json",
}

# Path prefixes that use candidate JWT (not dashboard auth)
_CANDIDATE_PREFIXES: tuple[str, ...] = (
    "/api/candidate-session/",
)

# Path prefixes that skip auth entirely (public endpoints)
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/auth/verify-invite",  # Public — invite token verification
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Provider-agnostic JWT verification.

    Extracts the Bearer token, verifies it, and attaches
    sub, tenant_id, is_projectx_admin to request.state.
    No role data in JWT — loaded per-request via UserContext.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        if path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        if path.startswith(_CANDIDATE_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid authorization header"})

        token = auth_header.removeprefix("Bearer ").strip()

        payload = verify_access_token(token)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        # Thin state — only identity + tenant
        request.state.token_payload = payload
        request.state.user_id = payload.sub
        request.state.tenant_id = payload.tenant_id
        request.state.is_projectx_admin = payload.is_projectx_admin

        return await call_next(request)
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/middleware/auth.py
git commit -m "middleware: remove role/is_admin/org_unit_id from request.state, drop require_roles"
```

---

## Task 6: Create UserContext Dependency

**Files:**
- Create: `backend/nexus/app/modules/auth/context.py`
- Rewrite: `backend/nexus/app/modules/auth/permissions.py`

- [ ] **Step 1: Rewrite permissions.py**

Replace the entire file with:

```python
"""Permission constants.

Permissions are derived from roles — never stored per-user.
This module defines the canonical permission set for validation.
"""

ALL_PERMISSIONS: frozenset[str] = frozenset({
    "users.invite_admins",
    "users.invite_users",
    "users.deactivate",
    "org_units.create",
    "org_units.manage",
    "jobs.create",
    "jobs.manage",
    "candidates.view",
    "candidates.evaluate",
    "candidates.advance",
    "interviews.schedule",
    "interviews.conduct",
    "reports.view",
    "reports.export",
    "settings.client",
    "settings.integrations",
})
```

- [ ] **Step 2: Create context.py**

Create `backend/nexus/app/modules/auth/context.py`:

```python
"""UserContext — per-request authorization context.

Loaded via a single JOIN query across user_role_assignments, roles,
and organizational_units. Super admin check compares against
clients.super_admin_id.
"""

import uuid as uuid_mod
from dataclasses import dataclass, field

import structlog
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.models import Client, OrganizationalUnit, Role, User, UserRoleAssignment

logger = structlog.get_logger()


@dataclass
class RoleAssignment:
    org_unit_id: uuid_mod.UUID
    org_unit_name: str
    role_id: uuid_mod.UUID
    role_name: str
    permissions: list[str]


@dataclass
class UserContext:
    user: User
    is_super_admin: bool
    assignments: list[RoleAssignment] = field(default_factory=list)

    def has_role_in_unit(self, org_unit_id: uuid_mod.UUID, role_name: str) -> bool:
        return any(
            a.org_unit_id == org_unit_id and a.role_name == role_name
            for a in self.assignments
        )

    def has_permission_in_unit(self, org_unit_id: uuid_mod.UUID, permission: str) -> bool:
        for a in self.assignments:
            if a.org_unit_id == org_unit_id and permission in a.permissions:
                return True
        return False

    def permissions_in_unit(self, org_unit_id: uuid_mod.UUID) -> set[str]:
        perms: set[str] = set()
        for a in self.assignments:
            if a.org_unit_id == org_unit_id:
                perms.update(a.permissions)
        return perms

    def all_permissions(self) -> set[str]:
        perms: set[str] = set()
        for a in self.assignments:
            perms.update(a.permissions)
        return perms


async def get_current_user_roles(
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> UserContext:
    """FastAPI dependency — loads UserContext for the authenticated user.

    Single JOIN query for assignments. Raises 401/404 as appropriate.
    """
    token_payload = getattr(request.state, "token_payload", None)
    if token_payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth_user_id = token_payload.sub

    # Load user + client in one query
    result = await db.execute(
        select(User, Client)
        .join(Client, User.tenant_id == Client.id)
        .where(User.auth_user_id == auth_user_id, User.is_active == True)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user, client = row
    is_super_admin = client.super_admin_id is not None and client.super_admin_id == user.id

    # Single JOIN: user_role_assignments + roles + organizational_units
    assignments_result = await db.execute(
        select(UserRoleAssignment, Role, OrganizationalUnit)
        .join(Role, UserRoleAssignment.role_id == Role.id)
        .join(OrganizationalUnit, UserRoleAssignment.org_unit_id == OrganizationalUnit.id)
        .where(UserRoleAssignment.user_id == user.id)
    )

    assignments = [
        RoleAssignment(
            org_unit_id=ura.org_unit_id,
            org_unit_name=ou.name,
            role_id=role.id,
            role_name=role.name,
            permissions=role.permissions or [],
        )
        for ura, role, ou in assignments_result.all()
    ]

    return UserContext(user=user, is_super_admin=is_super_admin, assignments=assignments)


def require_super_admin():
    """FastAPI dependency factory — rejects non-super-admins."""
    async def _check(ctx: UserContext = Depends(get_current_user_roles)) -> UserContext:
        if not ctx.is_super_admin:
            raise HTTPException(status_code=403, detail="Super admin required")
        return ctx
    return Depends(_check)
```

- [ ] **Step 3: Verify imports**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -c "from app.modules.auth.context import UserContext, get_current_user_roles, require_super_admin; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/auth/context.py backend/nexus/app/modules/auth/permissions.py
git commit -m "auth: add UserContext dependency with single-JOIN query, simplify permissions"
```

---

## Task 7: Rewrite Auth Router

**Files:**
- Rewrite: `backend/nexus/app/modules/auth/router.py`

- [ ] **Step 1: Rewrite router.py**

Replace the entire file with:

```python
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
        raise HTTPException(status_code=401, detail="Email mismatch — invite was for a different address")

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
    if is_super_admin:
        await db.execute(
            sqlalchemy.text(
                "UPDATE public.clients SET super_admin_id = :user_id WHERE id = :tenant_id"
            ),
            {"user_id": str(user.id), "tenant_id": str(claimed_row.tenant_id)},
        )

    redirect_to = "/onboarding" if is_super_admin else "/"

    logger.info(
        "auth.invite_completed",
        user_id=str(user.id),
        tenant_id=str(claimed_row.tenant_id),
        is_super_admin=is_super_admin,
    )

    return CompleteInviteResponse(
        redirect_to=redirect_to,
        user_id=str(user.id),
        tenant_id=str(claimed_row.tenant_id),
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
        select(func.count()).select_from(OrganizationalUnit).where(
            OrganizationalUnit.client_id == user.tenant_id
        )
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
        select(func.count()).select_from(OrganizationalUnit).where(
            OrganizationalUnit.client_id == ctx.user.tenant_id
        )
    )
    if (org_count.scalar() or 0) == 0:
        raise HTTPException(status_code=400, detail="Create at least one organizational unit before completing onboarding")

    result = await db.execute(select(Client).where(Client.id == ctx.user.tenant_id))
    client = result.scalar_one()
    client.onboarding_complete = True

    return {"status": "completed"}
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/auth/router.py
git commit -m "auth: rewrite endpoints for new roles model — simplified invite, assignments in /me"
```

---

## Task 8: Rewrite Admin Module

**Files:**
- Modify: `backend/nexus/app/modules/admin/service.py`
- Modify: `backend/nexus/app/modules/admin/schemas.py`

- [ ] **Step 1: Update admin service — remove role/permissions from invite**

In `backend/nexus/app/modules/admin/service.py`, replace the `provision_client` function's invite creation block. Change lines 43-52 from:

```python
    invite = UserInvite(
        tenant_id=client.id,
        email=admin_email,
        role="Company Admin",
        token_hash=token_hash,
        projectx_admin_id=admin_identity,
        is_admin=True,
        permissions=SUPER_ADMIN_PERMISSIONS,
        org_unit_id=None,
    )
```

to:

```python
    invite = UserInvite(
        tenant_id=client.id,
        email=admin_email,
        token_hash=token_hash,
        projectx_admin_id=admin_identity,
    )
```

Also remove the import of `SUPER_ADMIN_PERMISSIONS` from the top of the file (line 13):

```python
from app.modules.auth.permissions import SUPER_ADMIN_PERMISSIONS
```

- [ ] **Step 2: Update list_clients to remove role-based invite lookup**

In `backend/nexus/app/modules/admin/service.py`, replace the `list_clients` function (lines 85-131) with:

```python
async def list_clients(db: AsyncSession) -> list[dict]:
    """List all companies with their latest invite status.

    Single query with LEFT JOIN — no N+1.
    """
    from sqlalchemy import func

    # Subquery: latest invite per company (any invite, not role-filtered)
    latest_invite = (
        select(
            UserInvite.tenant_id,
            func.max(UserInvite.created_at).label("max_created"),
        )
        .where(UserInvite.projectx_admin_id.isnot(None))
        .group_by(UserInvite.tenant_id)
        .subquery()
    )

    result = await db.execute(
        select(Client, UserInvite)
        .outerjoin(
            latest_invite,
            Client.id == latest_invite.c.tenant_id,
        )
        .outerjoin(
            UserInvite,
            (UserInvite.tenant_id == latest_invite.c.tenant_id)
            & (UserInvite.created_at == latest_invite.c.max_created)
            & (UserInvite.projectx_admin_id.isnot(None)),
        )
        .order_by(Client.created_at.desc())
    )
    rows = result.all()

    return [
        {
            "client_id": str(company.id),
            "client_name": company.name,
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

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/admin/service.py backend/nexus/app/modules/admin/schemas.py
git commit -m "admin: remove role/permissions from invite provisioning"
```

---

## Task 9: Rewrite Settings/Team Module

**Files:**
- Rewrite: `backend/nexus/app/modules/settings/schemas.py`
- Rewrite: `backend/nexus/app/modules/settings/service.py`
- Rewrite: `backend/nexus/app/modules/settings/router.py`

- [ ] **Step 1: Rewrite settings schemas**

Replace `backend/nexus/app/modules/settings/schemas.py`:

```python
from pydantic import BaseModel


class TeamInviteRequest(BaseModel):
    email: str


class TeamInviteResponse(BaseModel):
    invite_id: str
    email: str
    invite_url: str  # Only present in dry-run mode; empty in production


class TeamMemberAssignment(BaseModel):
    org_unit_id: str
    org_unit_name: str
    role_name: str


class TeamMember(BaseModel):
    id: str
    email: str
    full_name: str | None
    is_active: bool
    is_super_admin: bool
    source: str  # "user" or "invite"
    status: str  # "active", "inactive" for users; "pending" for invites
    assignments: list[TeamMemberAssignment]
    created_at: str


class ResendInviteResponse(BaseModel):
    new_invite_id: str
    invite_url: str  # Only present in dry-run mode
```

- [ ] **Step 2: Rewrite settings service**

Replace `backend/nexus/app/modules/settings/service.py`:

```python
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

logger = structlog.get_logger()


async def create_team_invite(
    *,
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    email: str,
    invited_by: uuid_mod.UUID,
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
        select(User).where(
            User.tenant_id == tenant_id, User.is_active == True
        ).order_by(User.created_at.asc())
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
            assignments_by_user[ura.user_id].append({
                "org_unit_id": str(ura.org_unit_id),
                "org_unit_name": ou.name,
                "role_name": role.name,
            })

    for user in users:
        members.append({
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_super_admin": super_admin_id is not None and user.id == super_admin_id,
            "source": "user",
            "status": "active",
            "assignments": assignments_by_user.get(user.id, []),
            "created_at": user.created_at.isoformat(),
        })

    # Pending invites
    invite_result = await db.execute(
        select(UserInvite)
        .where(UserInvite.tenant_id == tenant_id, UserInvite.status == "pending")
        .order_by(UserInvite.created_at.desc())
    )
    for invite in invite_result.scalars().all():
        members.append({
            "id": str(invite.id),
            "email": invite.email,
            "full_name": None,
            "is_active": False,
            "is_super_admin": False,
            "source": "invite",
            "status": "pending",
            "assignments": [],
            "created_at": invite.created_at.isoformat(),
        })

    return members


async def resend_team_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
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
    """Deactivate a user and delete their Supabase Auth account."""
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
```

- [ ] **Step 3: Rewrite settings router**

Replace `backend/nexus/app/modules/settings/router.py`:

```python
import uuid as uuid_mod

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.config import settings
from app.database import get_tenant_db
from app.models import Client, User
from app.modules.auth.context import UserContext, get_current_user_roles, require_super_admin
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


@router.post(
    "/deactivate/{user_id}",
    dependencies=[require_super_admin()],
)
async def deactivate_endpoint(
    user_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Deactivate a user. Super admin only."""
    try:
        await deactivate_team_user(
            db, ctx.user.tenant_id, uuid_mod.UUID(user_id), ctx.user.auth_user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deactivated"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/settings/
git commit -m "settings: rewrite for UserContext auth, super admin gating, assignments in team list"
```

---

## Task 10: Rewrite Org Units Module

**Files:**
- Rewrite: `backend/nexus/app/modules/org_units/schemas.py`
- Rewrite: `backend/nexus/app/modules/org_units/service.py`
- Rewrite: `backend/nexus/app/modules/org_units/router.py`

- [ ] **Step 1: Rewrite org units schemas**

Replace `backend/nexus/app/modules/org_units/schemas.py`:

```python
from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    created_at: str


class AssignRoleRequest(BaseModel):
    user_id: str
    role_id: str


class MemberRole(BaseModel):
    role_id: str
    role_name: str
    assigned_at: str


class OrgUnitMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    roles: list[MemberRole]
```

- [ ] **Step 2: Rewrite org units service**

Replace `backend/nexus/app/modules/org_units/service.py`:

```python
"""Org unit CRUD and role assignment service."""

import uuid as uuid_mod
from collections import defaultdict

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit, Role, User, UserRoleAssignment

logger = structlog.get_logger()

VALID_UNIT_TYPES = {"client_account", "department", "team", "branch", "region"}


async def create_org_unit(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
) -> OrganizationalUnit:
    if unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")

    unit = OrganizationalUnit(
        client_id=client_id,
        name=name,
        unit_type=unit_type,
        parent_unit_id=parent_unit_id,
    )
    db.add(unit)
    await db.flush()

    logger.info("org_units.created", unit_id=str(unit.id), name=name)
    return unit


async def list_org_units(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    is_super_admin: bool,
) -> list[dict]:
    """List org units. Super admin sees all; others see assigned units only."""
    if is_super_admin:
        query = select(OrganizationalUnit).where(
            OrganizationalUnit.client_id == client_id
        )
    else:
        # Only units the user has assignments in
        assigned_unit_ids = (
            select(UserRoleAssignment.org_unit_id)
            .where(UserRoleAssignment.user_id == user_id)
            .distinct()
            .scalar_subquery()
        )
        query = select(OrganizationalUnit).where(
            OrganizationalUnit.client_id == client_id,
            OrganizationalUnit.id.in_(assigned_unit_ids),
        )

    result = await db.execute(query.order_by(OrganizationalUnit.created_at.asc()))
    units = result.scalars().all()

    # Batch member counts
    unit_ids = [u.id for u in units]
    counts: dict[uuid_mod.UUID, int] = {}
    if unit_ids:
        count_result = await db.execute(
            select(
                UserRoleAssignment.org_unit_id,
                func.count(func.distinct(UserRoleAssignment.user_id)),
            )
            .where(UserRoleAssignment.org_unit_id.in_(unit_ids))
            .group_by(UserRoleAssignment.org_unit_id)
        )
        counts = {row[0]: row[1] for row in count_result.all()}

    return [
        {
            "id": str(u.id),
            "client_id": str(u.client_id),
            "parent_unit_id": str(u.parent_unit_id) if u.parent_unit_id else None,
            "name": u.name,
            "unit_type": u.unit_type,
            "member_count": counts.get(u.id, 0),
            "created_at": u.created_at.isoformat(),
        }
        for u in units
    ]


async def update_org_unit(
    db: AsyncSession,
    unit: OrganizationalUnit,
    name: str | None,
    unit_type: str | None,
) -> OrganizationalUnit:
    if unit_type is not None and unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")
    if name is not None:
        unit.name = name
    if unit_type is not None:
        unit.unit_type = unit_type
    return unit


async def list_unit_members(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
) -> list[dict]:
    """List members of an org unit grouped by user with their roles."""
    result = await db.execute(
        select(UserRoleAssignment, User, Role)
        .join(User, UserRoleAssignment.user_id == User.id)
        .join(Role, UserRoleAssignment.role_id == Role.id)
        .where(UserRoleAssignment.org_unit_id == org_unit_id)
        .order_by(User.email.asc(), Role.name.asc())
    )

    # Group by user
    members_map: dict[uuid_mod.UUID, dict] = {}
    for ura, user, role in result.all():
        if user.id not in members_map:
            members_map[user.id] = {
                "user_id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "roles": [],
            }
        members_map[user.id]["roles"].append({
            "role_id": str(role.id),
            "role_name": role.name,
            "assigned_at": ura.created_at.isoformat(),
        })

    return list(members_map.values())


async def assign_role(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    role_id: uuid_mod.UUID,
    tenant_id: uuid_mod.UUID,
    assigned_by: uuid_mod.UUID,
) -> UserRoleAssignment:
    """Assign a role to a user in an org unit."""
    # Verify user exists and is active
    user_result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    if not user_result.scalar_one_or_none():
        raise ValueError("User not found or inactive")

    # Verify role exists
    role_result = await db.execute(select(Role).where(Role.id == role_id))
    if not role_result.scalar_one_or_none():
        raise ValueError("Role not found")

    assignment = UserRoleAssignment(
        user_id=user_id,
        org_unit_id=org_unit_id,
        role_id=role_id,
        tenant_id=tenant_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)

    try:
        await db.flush()
    except Exception:
        raise ValueError("User already has this role in this unit")

    logger.info("org_units.role_assigned", user_id=str(user_id), org_unit_id=str(org_unit_id), role_id=str(role_id))
    return assignment


async def remove_user_from_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
) -> int:
    """Remove ALL role assignments for a user in an org unit. Returns count removed."""
    result = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.org_unit_id == org_unit_id,
            UserRoleAssignment.user_id == user_id,
        )
    )
    assignments = result.scalars().all()
    if not assignments:
        raise ValueError("No assignments found for this user in this unit")

    for a in assignments:
        await db.delete(a)

    logger.info("org_units.user_removed", user_id=str(user_id), org_unit_id=str(org_unit_id), count=len(assignments))
    return len(assignments)


async def remove_role_from_user(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    role_id: uuid_mod.UUID,
) -> None:
    """Remove a specific role assignment."""
    result = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.org_unit_id == org_unit_id,
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.role_id == role_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise ValueError("Assignment not found")

    await db.delete(assignment)
    logger.info("org_units.role_removed", user_id=str(user_id), org_unit_id=str(org_unit_id), role_id=str(role_id))
```

- [ ] **Step 3: Rewrite org units router**

Replace `backend/nexus/app/modules/org_units/router.py`:

```python
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import OrganizationalUnit
from app.modules.auth.context import UserContext, get_current_user_roles, require_super_admin
from app.modules.org_units.schemas import (
    AssignRoleRequest,
    CreateOrgUnitRequest,
    OrgUnitMember,
    OrgUnitResponse,
    UpdateOrgUnitRequest,
)
from app.modules.org_units.service import (
    assign_role,
    create_org_unit,
    list_org_units,
    list_unit_members,
    remove_role_from_user,
    remove_user_from_unit,
    update_org_unit,
)

router = APIRouter(prefix="/api/org-units", tags=["org-units"])


def _require_unit_admin(ctx: UserContext, org_unit_id: uuid_mod.UUID) -> None:
    """Check super admin OR Admin role in the specific unit."""
    if ctx.is_super_admin:
        return
    if ctx.has_role_in_unit(org_unit_id, "Admin"):
        return
    raise HTTPException(status_code=403, detail="Requires super admin or Admin role in this unit")


@router.post("", response_model=OrgUnitResponse, dependencies=[require_super_admin()])
async def create_unit(
    data: CreateOrgUnitRequest,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    """Create an org unit. Super admin only."""
    parent_id = uuid_mod.UUID(data.parent_unit_id) if data.parent_unit_id else None
    try:
        unit = await create_org_unit(db, ctx.user.tenant_id, data.name, data.unit_type, parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=0,
        created_at=unit.created_at.isoformat(),
    )


@router.get("", response_model=list[OrgUnitResponse])
async def list_units(
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[OrgUnitResponse]:
    """List org units. Super admin: all. Others: assigned units only. Empty list for unassigned users."""
    units = await list_org_units(db, ctx.user.tenant_id, ctx.user.id, ctx.is_super_admin)
    return [OrgUnitResponse(**u) for u in units]


@router.put("/{unit_id}", response_model=OrgUnitResponse)
async def update_unit(
    unit_id: str,
    data: UpdateOrgUnitRequest,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    """Update an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == uid))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Org unit not found")

    try:
        unit = await update_org_unit(db, unit, data.name, data.unit_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=0,
        created_at=unit.created_at.isoformat(),
    )


@router.get("/{unit_id}/members", response_model=list[OrgUnitMember])
async def get_members(
    unit_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[OrgUnitMember]:
    """List members of an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    members = await list_unit_members(db, uid)
    return [OrgUnitMember(**m) for m in members]


@router.post("/{unit_id}/members")
async def assign_member_role(
    unit_id: str,
    data: AssignRoleRequest,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Assign a role to a user in an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    try:
        await assign_role(
            db,
            org_unit_id=uid,
            user_id=uuid_mod.UUID(data.user_id),
            role_id=uuid_mod.UUID(data.role_id),
            tenant_id=ctx.user.tenant_id,
            assigned_by=ctx.user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "assigned"}


@router.delete("/{unit_id}/members/{user_id}")
async def remove_member(
    unit_id: str,
    user_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Remove all roles for a user in an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    try:
        count = await remove_user_from_unit(db, uid, uuid_mod.UUID(user_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "removed", "count": str(count)}


@router.delete("/{unit_id}/members/{user_id}/roles/{role_id}")
async def remove_member_role(
    unit_id: str,
    user_id: str,
    role_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Remove a specific role from a user in an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    try:
        await remove_role_from_user(db, uid, uuid_mod.UUID(user_id), uuid_mod.UUID(role_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "removed"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/org_units/
git commit -m "org-units: rewrite with role assignment endpoints and UserContext auth"
```

---

## Task 11: Add Roles Module and Register Router

**Files:**
- Create: `backend/nexus/app/modules/roles/router.py`
- Create: `backend/nexus/app/modules/roles/schemas.py`
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Create roles schemas**

Create `backend/nexus/app/modules/roles/__init__.py` (empty file) and `backend/nexus/app/modules/roles/schemas.py`:

```python
from pydantic import BaseModel


class RoleResponse(BaseModel):
    id: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
```

- [ ] **Step 2: Create roles router**

Create `backend/nexus/app/modules/roles/router.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import Role
from app.modules.auth.context import get_current_user_roles
from app.modules.roles.schemas import RoleResponse

router = APIRouter(prefix="/api/roles", tags=["roles"])


@router.get("", response_model=list[RoleResponse])
async def list_roles(
    db: AsyncSession = Depends(get_tenant_db),
) -> list[RoleResponse]:
    """List available roles — system + tenant custom (future)."""
    result = await db.execute(
        select(Role).order_by(Role.is_system.desc(), Role.name.asc())
    )
    return [
        RoleResponse(
            id=str(r.id),
            name=r.name,
            description=r.description or "",
            permissions=r.permissions or [],
            is_system=r.is_system,
        )
        for r in result.scalars().all()
    ]
```

- [ ] **Step 3: Register roles router in main.py**

In `backend/nexus/app/main.py`, add after line 73 (`from app.modules.org_units.router import router as org_units_router`):

```python
    from app.modules.roles.router import router as roles_router
```

And add after line 87 (`application.include_router(org_units_router)`):

```python
    application.include_router(roles_router)
```

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/roles/ backend/nexus/app/main.py
git commit -m "feat: add read-only /api/roles endpoint, register in app"
```

---

## Task 12: Update Backend Tests

**Files:**
- Rewrite: `backend/nexus/tests/test_permissions.py`
- Rewrite: `backend/nexus/tests/test_auth_endpoints.py`
- Rewrite: `backend/nexus/tests/test_settings.py`
- Rewrite: `backend/nexus/tests/test_org_units.py`
- Create: `backend/nexus/tests/test_user_context.py`

- [ ] **Step 1: Rewrite test_permissions.py**

Replace `backend/nexus/tests/test_permissions.py`:

```python
"""Tests for permission constants."""

from app.modules.auth.permissions import ALL_PERMISSIONS


def test_all_permissions_is_frozenset():
    assert isinstance(ALL_PERMISSIONS, frozenset)


def test_all_permissions_count():
    assert len(ALL_PERMISSIONS) == 16


def test_known_permissions_present():
    assert "jobs.create" in ALL_PERMISSIONS
    assert "candidates.view" in ALL_PERMISSIONS
    assert "interviews.conduct" in ALL_PERMISSIONS
    assert "reports.export" in ALL_PERMISSIONS
    assert "settings.client" in ALL_PERMISSIONS
```

- [ ] **Step 2: Rewrite test_auth_endpoints.py**

Replace `backend/nexus/tests/test_auth_endpoints.py`:

```python
"""Tests for auth endpoints — error paths that don't require a live database."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_verify_invite_missing_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/verify-invite")
    assert resp.status_code == 422  # missing required query param


@pytest.mark.asyncio
async def test_complete_invite_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/complete-invite", json={"raw_token": "abc"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_onboarding_complete_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/onboarding/complete")
    assert resp.status_code == 401
```

- [ ] **Step 3: Rewrite test_settings.py**

Replace `backend/nexus/tests/test_settings.py`:

```python
"""Tests for settings/team endpoints — auth guard tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_invite_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/settings/team/invite", json={"email": "a@b.com"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_members_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/settings/team/members")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_deactivate_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/settings/team/deactivate/00000000-0000-0000-0000-000000000001")
    assert resp.status_code == 401
```

- [ ] **Step 4: Rewrite test_org_units.py**

Replace `backend/nexus/tests/test_org_units.py`:

```python
"""Tests for org units endpoints — auth guard tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_create_org_unit_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/org-units", json={"name": "Test", "unit_type": "team"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_org_units_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/org-units")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_roles_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/roles")
    assert resp.status_code == 401
```

- [ ] **Step 5: Create test_user_context.py**

Create `backend/nexus/tests/test_user_context.py`:

```python
"""Tests for UserContext helper methods."""

import uuid

from app.modules.auth.context import RoleAssignment, UserContext


def _make_ctx(assignments: list[RoleAssignment], is_super_admin: bool = False) -> UserContext:
    """Create a UserContext with a mock user for testing."""
    from unittest.mock import MagicMock
    user = MagicMock()
    user.id = uuid.uuid4()
    return UserContext(user=user, is_super_admin=is_super_admin, assignments=assignments)


def test_has_role_in_unit_true():
    unit_id = uuid.uuid4()
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Admin", permissions=[]),
    ])
    assert ctx.has_role_in_unit(unit_id, "Admin") is True


def test_has_role_in_unit_false():
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=uuid.uuid4(), org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=[]),
    ])
    assert ctx.has_role_in_unit(uuid.uuid4(), "Admin") is False


def test_has_permission_in_unit():
    unit_id = uuid.uuid4()
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=["jobs.create", "jobs.manage"]),
    ])
    assert ctx.has_permission_in_unit(unit_id, "jobs.create") is True
    assert ctx.has_permission_in_unit(unit_id, "interviews.conduct") is False


def test_permissions_in_unit_union():
    unit_id = uuid.uuid4()
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=["jobs.create"]),
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Interviewer", permissions=["interviews.conduct"]),
    ])
    perms = ctx.permissions_in_unit(unit_id)
    assert perms == {"jobs.create", "interviews.conduct"}


def test_all_permissions_across_units():
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=uuid.uuid4(), org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=["jobs.create"]),
        RoleAssignment(org_unit_id=uuid.uuid4(), org_unit_name="Sales", role_id=uuid.uuid4(), role_name="Observer", permissions=["candidates.view"]),
    ])
    assert ctx.all_permissions() == {"jobs.create", "candidates.view"}


def test_super_admin_flag():
    ctx = _make_ctx([], is_super_admin=True)
    assert ctx.is_super_admin is True
```

- [ ] **Step 6: Run tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/tests/
git commit -m "tests: update for roles/permissions refactor"
```

---

## Task 13: Frontend — Update Invite Page

**Files:**
- Modify: `frontend/app/app/(auth)/invite/page.tsx`

- [ ] **Step 1: Remove role display from invite page**

In `frontend/app/app/(auth)/invite/page.tsx`:

Remove the `role` field from the `InviteDetails` interface (change lines 8-12):

```typescript
interface InviteDetails {
  email: string;
  client_name: string;
}
```

Remove the role display in the green box. Replace:

```tsx
      <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center mb-6">
        <p className="font-semibold text-green-800">
          {invite!.client_name}
        </p>
        <p className="text-sm text-green-700 mt-0.5">as {invite!.role}</p>
      </div>
```

with:

```tsx
      <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center mb-6">
        <p className="font-semibold text-green-800">
          {invite!.client_name}
        </p>
      </div>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/app/\(auth\)/invite/page.tsx
git commit -m "frontend: remove role display from invite page"
```

---

## Task 14: Frontend — Update Dashboard Layout

**Files:**
- Modify: `frontend/app/app/(dashboard)/layout.tsx`

- [ ] **Step 1: Update MeData type and redirect logic**

In `frontend/app/app/(dashboard)/layout.tsx`, update the `getMe` return type (line 5-12). Replace:

```typescript
const getMe = cache(async (token: string, apiUrl: string) => {
  const res = await fetch(`${apiUrl}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json() as Promise<{
    role: string;
    is_admin: boolean;
    onboarding_complete: boolean;
    has_org_units: boolean;
  }>;
});
```

with:

```typescript
const getMe = cache(async (token: string, apiUrl: string) => {
  const res = await fetch(`${apiUrl}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json() as Promise<{
    is_super_admin: boolean;
    onboarding_complete: boolean;
    has_org_units: boolean;
  }>;
});
```

Update the redirect check. Replace:

```typescript
  if (me && me.role === "Company Admin" && !me.onboarding_complete) {
    redirect("/onboarding");
  }
```

with:

```typescript
  if (me && me.is_super_admin && !me.onboarding_complete) {
    redirect("/onboarding");
  }
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/app/\(dashboard\)/layout.tsx
git commit -m "frontend: use is_super_admin instead of role check in dashboard layout"
```

---

## Task 15: Frontend — Update Profile Page

**Files:**
- Rewrite: `frontend/app/app/(dashboard)/profile/page.tsx`

- [ ] **Step 1: Rewrite profile page for assignments**

Replace the entire file `frontend/app/app/(dashboard)/profile/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface RoleAssignment {
  org_unit_id: string;
  org_unit_name: string;
  role_name: string;
  permissions: string[];
}

interface MeData {
  user_id: string;
  email: string;
  full_name: string | null;
  tenant_id: string;
  client_name: string;
  is_super_admin: boolean;
  assignments: RoleAssignment[];
}

export default function ProfilePage() {
  const router = useRouter();
  const [me, setMe] = useState<MeData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const supabase = createClient();
        const {
          data: { session },
        } = await supabase.auth.getSession();
        if (!session?.access_token) return;
        const data = await apiFetch<MeData>("/api/auth/me", {
          token: session.access_token,
        });
        setMe(data);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  if (loading) return <p className="text-sm text-zinc-500">Loading...</p>;
  if (!me) return <p className="text-sm text-zinc-500">Not logged in.</p>;

  // Group assignments by org unit
  const byUnit: Record<string, { name: string; roles: string[] }> = {};
  for (const a of me.assignments) {
    if (!byUnit[a.org_unit_id]) {
      byUnit[a.org_unit_id] = { name: a.org_unit_name, roles: [] };
    }
    byUnit[a.org_unit_id].roles.push(a.role_name);
  }

  return (
    <>
      <h1 className="text-lg font-semibold text-zinc-900 mb-6">Profile</h1>

      <div className="bg-white border border-zinc-200 rounded-lg p-6 max-w-lg space-y-4">
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">Email</p>
          <p className="text-sm text-zinc-900">{me.email}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">Name</p>
          <p className="text-sm text-zinc-900">{me.full_name || "—"}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">Role</p>
          <div className="flex items-center gap-2">
            {me.is_super_admin && (
              <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-xs font-medium">
                Super Admin
              </span>
            )}
            {!me.is_super_admin && me.assignments.length === 0 && (
              <p className="text-sm text-zinc-400">
                No roles assigned yet. Contact your administrator.
              </p>
            )}
          </div>
        </div>
        <div>
          <p className="text-xs font-medium text-zinc-500 mb-0.5">
            Organization
          </p>
          <p className="text-sm text-zinc-900">{me.client_name}</p>
        </div>

        {Object.keys(byUnit).length > 0 && (
          <div>
            <p className="text-xs font-medium text-zinc-500 mb-1.5">
              Assignments
            </p>
            <div className="space-y-2">
              {Object.entries(byUnit).map(([unitId, { name, roles }]) => (
                <div
                  key={unitId}
                  className="bg-zinc-50 rounded-lg px-3 py-2"
                >
                  <p className="text-sm font-medium text-zinc-800">{name}</p>
                  <p className="text-xs text-zinc-500">{roles.join(", ")}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <button
        onClick={handleSignOut}
        className="mt-6 text-sm text-red-600 hover:underline"
      >
        Sign out
      </button>
    </>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/app/\(dashboard\)/profile/page.tsx
git commit -m "frontend: rewrite profile page with assignments grouped by org unit"
```

---

## Task 16: Frontend — Update Team Page

**Files:**
- Rewrite: `frontend/app/app/(dashboard)/settings/team/page.tsx`

- [ ] **Step 1: Rewrite team page with super admin gating**

Replace `frontend/app/app/(dashboard)/settings/team/page.tsx` entirely. The key changes:
- `TeamMember` interface uses `is_super_admin` and `assignments[]` instead of `role`/`is_admin`/`permissions`
- `MeData` uses `is_super_admin` instead of `role`/`is_admin`
- Invite form, deactivate, resend, revoke actions visible only when `me.is_super_admin`
- Member list shows assignments and super admin badge

This is a full page rewrite — create the file matching the new `TeamMember` schema from Task 9 Step 1. The UI pattern stays the same (list + invite form + action buttons), but data shapes change.

- [ ] **Step 2: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/team/page.tsx
git commit -m "frontend: rewrite team page for new auth model, super admin gating"
```

---

## Task 17: Frontend — Update Org Units Page

**Files:**
- Rewrite: `frontend/app/app/(dashboard)/settings/org-units/page.tsx`

- [ ] **Step 1: Rewrite org units page for role management**

Replace `frontend/app/app/(dashboard)/settings/org-units/page.tsx` entirely. The key changes:
- Member list shows `roles[]` array per member (not single role)
- "Add member" dialog: select user + select role from `GET /api/roles`
- "Remove role" action per role row
- "Remove from unit" action removes all roles
- Create unit button visible only to super admins
- Add/remove member actions visible to super admin or users with Admin role in the selected unit

This is a full page rewrite — create the file matching the new `OrgUnitMember` schema from Task 10 Step 1.

- [ ] **Step 2: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/org-units/page.tsx
git commit -m "frontend: rewrite org units page as primary role management surface"
```

---

## Task 18: Integration Verification

- [ ] **Step 1: Reset Supabase and verify schema**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
```

Expected: clean database, no errors.

- [ ] **Step 2: Start backend and verify endpoints**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && docker compose up --build -d
```

Verify health:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 3: Run all backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Start frontend and verify build**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app && npm run build
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: integration verification — all tests pass, build succeeds"
```
