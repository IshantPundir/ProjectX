# Phase 1: Database + Auth Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the three foundational database tables (companies, users, user_invites) with RLS, deploy the Supabase Auth Custom Access Token Hook, and rewrite the FastAPI auth layer from HS256 shared-secret to ES256 JWKS verification.

**Architecture:** Supabase migrations create the schema and auth hook in the same Postgres instance managed by Supabase CLI. FastAPI verifies JWTs via the JWKS public key endpoint — no shared secret. A new `get_bypass_session()` enables admin/onboarding operations that need to write across RLS boundaries.

**Tech Stack:** PostgreSQL 17 (via Supabase CLI), SQLAlchemy async + asyncpg, PyJWT with `PyJWKClient` (ES256), FastAPI middleware, pytest + pytest-asyncio

**Source of truth for all SQL:** [Notion spec](https://www.notion.so/Auth-Client-User-onboarding-3367984a452a806c8097c9d7cf084f18)

**Design spec:** `docs/superpowers/specs/2026-04-03-auth-client-user-onboarding-design.md`

---

## File Map

**Create:**
| File | Responsibility |
|---|---|
| `backend/supabase/migrations/20260403000000_create_auth_tables.sql` | 3 tables + trigger function + RLS policies |
| `backend/supabase/migrations/20260403000001_auth_hook.sql` | Auth hook function + grants to `supabase_auth_admin` |
| `scripts/supabase_hook.sql` | Production deployment copy with header comment |
| `backend/nexus/tests/test_auth_service.py` | Unit tests for JWKS verification + require_projectx_admin |
| `backend/nexus/tests/test_database.py` | Unit test for get_bypass_session |

**Modify:**
| File | Lines | What changes |
|---|---|---|
| `backend/supabase/config.toml` | 155, 267-269 | Add redirect URL, uncomment + configure auth hook |
| `backend/nexus/app/config.py` | 18-28 | Replace HS256 fields with `supabase_jwks_url`, add `notifications_dry_run` |
| `backend/nexus/app/modules/auth/schemas.py` | 14-21 | `TokenPayload`: rename `role`→`app_role`, add `is_projectx_admin` |
| `backend/nexus/app/modules/auth/service.py` | 1-42 | Replace HS256 `jwt.decode` with `PyJWKClient` ES256, add `require_projectx_admin` |
| `backend/nexus/app/middleware/auth.py` | 12-16, 56-59, 64-81 | Add public paths, read `app_role`, attach `is_projectx_admin`, update `require_roles` |
| `backend/nexus/app/database.py` | 40-44 | Add `get_bypass_session()` |
| `backend/nexus/app/main.py` | 60 | Update CORS origins to include `:3001` |
| `backend/nexus/.env` | whole file | Add `SUPABASE_JWKS_URL`, `NOTIFICATIONS_DRY_RUN`, update `DATABASE_URL` |
| `backend/nexus/.env.example` | whole file | Same as `.env` |

**Not modified (Phase 2+):**
- All frontend files
- `notifications/service.py` (Phase 2 — dry-run + Resend)
- No new FastAPI endpoints in this phase

---

### Task 1: Supabase migration — auth tables

**Files:**
- Create: `backend/supabase/migrations/20260403000000_create_auth_tables.sql`

- [ ] **Step 1: Create the migrations directory**

```bash
mkdir -p /home/ishant/Projects/ProjectX/backend/supabase/migrations
```

- [ ] **Step 2: Write the migration file**

Create `backend/supabase/migrations/20260403000000_create_auth_tables.sql` with this exact content:

```sql
-- Migration: Create auth foundation tables
-- Tables: companies, users, user_invites
-- All tables have RLS enabled with tenant isolation + service bypass policies.
-- Source of truth: Notion spec — Auth + Client + User onboarding

-- Step 1: Shared trigger function for updated_at
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

-- Step 2: companies (tenant root — no FKs)
CREATE TABLE public.companies (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT        NOT NULL,
  domain        TEXT,
  industry      TEXT,
  size          TEXT,
  culture_brief TEXT,
  logo_url      TEXT,
  plan          TEXT        NOT NULL DEFAULT 'trial'
                CHECK (plan IN ('trial', 'pro', 'enterprise')),
  onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at    TIMESTAMPTZ
);

CREATE TRIGGER companies_updated_at
  BEFORE UPDATE ON public.companies
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.companies ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_read" ON public.companies FOR SELECT
  USING (id = (SELECT current_setting('app.current_tenant', true)::UUID));

CREATE POLICY "service_write" ON public.companies FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');

-- Step 3: users (one row per client team member)
CREATE TABLE public.users (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_user_id      UUID        NOT NULL UNIQUE,
  tenant_id         UUID        NOT NULL REFERENCES public.companies(id),
  email             TEXT        NOT NULL,
  full_name         TEXT,
  role              TEXT        NOT NULL
                    CHECK (role IN (
                      'Company Admin', 'Recruiter',
                      'Hiring Manager', 'Interviewer', 'Observer'
                    )),
  is_active         BOOLEAN     NOT NULL DEFAULT TRUE,
  notification_prefs JSONB      NOT NULL DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at        TIMESTAMPTZ
);

CREATE INDEX users_tenant_id_idx    ON public.users (tenant_id);
CREATE INDEX users_auth_user_id_idx ON public.users (auth_user_id);

CREATE TRIGGER users_updated_at
  BEFORE UPDATE ON public.users
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.users
  USING (tenant_id = (SELECT current_setting('app.current_tenant', true)::UUID));

CREATE POLICY "service_bypass" ON public.users FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');

-- Step 4: user_invites (invite tracking for both pipelines)
CREATE TABLE public.user_invites (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID        NOT NULL REFERENCES public.companies(id),

  -- Origin: exactly one of these must be set (XOR enforced at DB level)
  invited_by        UUID        REFERENCES public.users(id),
  projectx_admin_id TEXT,

  CONSTRAINT invite_has_exactly_one_origin CHECK (
    (invited_by IS NOT NULL) <> (projectx_admin_id IS NOT NULL)
  ),

  email             TEXT        NOT NULL,
  role              TEXT        NOT NULL
                    CHECK (role IN (
                      'Company Admin', 'Recruiter',
                      'Hiring Manager', 'Interviewer', 'Observer'
                    )),
  token_hash        TEXT        NOT NULL UNIQUE,
  status            TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                      'pending', 'accepted', 'superseded', 'expired', 'revoked'
                    )),
  superseded_by     UUID        REFERENCES public.user_invites(id),
  expires_at        TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '72 hours',
  accepted_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX user_invites_tenant_idx ON public.user_invites (tenant_id);
CREATE INDEX user_invites_token_idx  ON public.user_invites (token_hash);
CREATE INDEX user_invites_email_idx  ON public.user_invites (email, status);

ALTER TABLE public.user_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.user_invites
  USING (tenant_id = (SELECT current_setting('app.current_tenant', true)::UUID));

CREATE POLICY "service_bypass" ON public.user_invites FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');
```

- [ ] **Step 3: Commit**

```bash
git add backend/supabase/migrations/20260403000000_create_auth_tables.sql
git commit -m "feat: add Supabase migration for auth tables (companies, users, user_invites)"
```

---

### Task 2: Supabase migration — auth hook + grants

**Files:**
- Create: `backend/supabase/migrations/20260403000001_auth_hook.sql`
- Create: `scripts/supabase_hook.sql`

- [ ] **Step 1: Write the auth hook migration**

Create `backend/supabase/migrations/20260403000001_auth_hook.sql` with this exact content:

```sql
-- Migration: Custom Access Token Hook for Supabase Auth
-- This function runs before every JWT issuance. It injects tenant_id,
-- app_role, and is_projectx_admin into the JWT claims.
--
-- CRITICAL CONSTRAINTS:
--   - 2-second hard timeout — must complete quickly
--   - READ-ONLY — never INSERT/UPDATE in this function
--   - Must preserve all required JWT claims in output
--   - EXCEPTION handler returns safe defaults — never blocks login

CREATE OR REPLACE FUNCTION public.projectx_custom_access_token_hook(event JSONB)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
  claims          JSONB;
  user_uid        UUID;
  user_email      TEXT;
  user_row        RECORD;
  invite_row      RECORD;
  is_px_admin     BOOLEAN;
  auth_method     TEXT;
BEGIN
  claims      := event -> 'claims';
  user_uid    := (event ->> 'user_id')::UUID;
  user_email  := claims ->> 'email';
  auth_method := event ->> 'authentication_method';

  -- Check for ProjectX admin flag (already in claims.app_metadata)
  is_px_admin := COALESCE(
    (claims -> 'app_metadata' ->> 'is_projectx_admin')::BOOLEAN,
    FALSE
  );

  IF is_px_admin THEN
    claims := jsonb_set(claims, '{is_projectx_admin}', 'true');
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{app_role}', '""');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 1: existing public.users row (covers both first login and token_refresh)
  SELECT id, tenant_id, role
    INTO user_row
    FROM public.users
   WHERE auth_user_id = user_uid
     AND is_active = TRUE
   LIMIT 1;

  IF FOUND THEN
    claims := jsonb_set(claims, '{tenant_id}', to_jsonb(user_row.tenant_id::TEXT));
    claims := jsonb_set(claims, '{app_role}',  to_jsonb(user_row.role));
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Skip invite lookup on token_refresh — no new invite is being claimed
  IF auth_method = 'token_refresh' THEN
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{app_role}', '""');
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 2: pending invite exists for this email
  SELECT id, tenant_id, role
    INTO invite_row
    FROM public.user_invites
   WHERE email      = user_email
     AND status     = 'pending'
     AND expires_at > NOW()
   ORDER BY created_at DESC
   LIMIT 1;

  IF FOUND THEN
    claims := jsonb_set(claims, '{tenant_id}', to_jsonb(invite_row.tenant_id::TEXT));
    claims := jsonb_set(claims, '{app_role}',  to_jsonb(invite_row.role));
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 3: no user, no invite — return empty claims
  claims := jsonb_set(claims, '{tenant_id}', '""');
  claims := jsonb_set(claims, '{app_role}',  '""');
  claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
  RETURN jsonb_build_object('claims', claims);

EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'projectx_hook error: % %', SQLERRM, SQLSTATE;
  claims := jsonb_set(claims, '{tenant_id}', '""');
  claims := jsonb_set(claims, '{app_role}', '""');
  claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
  RETURN jsonb_build_object('claims', claims);
END;
$$;

-- Grants: allow Supabase Auth service to execute the hook
GRANT EXECUTE ON FUNCTION public.projectx_custom_access_token_hook TO supabase_auth_admin;
GRANT USAGE ON SCHEMA public TO supabase_auth_admin;
GRANT SELECT ON public.users TO supabase_auth_admin;
GRANT SELECT ON public.user_invites TO supabase_auth_admin;

-- Revoke from other roles to prevent Data API exposure
REVOKE EXECUTE ON FUNCTION public.projectx_custom_access_token_hook FROM authenticated, anon, public;
```

- [ ] **Step 2: Create production deployment copy**

Create `scripts/supabase_hook.sql` — identical SQL but with a header comment:

```sql
-- ============================================================================
-- ProjectX Custom Access Token Hook — PRODUCTION DEPLOYMENT
-- ============================================================================
-- This file is a copy of backend/supabase/migrations/20260403000001_auth_hook.sql
-- for manual execution against hosted Supabase Postgres.
--
-- HOW TO DEPLOY:
--   1. Connect to hosted Supabase Postgres (Database → Connection string → psql)
--   2. Run this entire file: \i scripts/supabase_hook.sql
--   3. Register the hook in Supabase Dashboard:
--      Authentication → Hooks → Custom Access Token Hook
--      → select public.projectx_custom_access_token_hook
--
-- DO NOT run this against local Supabase — it runs automatically via migrations.
-- ============================================================================

-- Include the FULL contents of backend/supabase/migrations/20260403000001_auth_hook.sql
-- below this line — the complete CREATE OR REPLACE FUNCTION through the REVOKE statement.
-- The implementing agent must copy the entire SQL body from the migration file into this
-- file so it is self-contained. Do NOT leave this as a reference — paste the actual SQL.
```

- [ ] **Step 3: Commit**

```bash
git add backend/supabase/migrations/20260403000001_auth_hook.sql scripts/supabase_hook.sql
git commit -m "feat: add auth hook migration + production deployment copy"
```

---

### Task 3: Register hook in config.toml + add redirect URLs

**Files:**
- Modify: `backend/supabase/config.toml:155,267-269`

- [ ] **Step 1: Add admin panel redirect URL**

In `backend/supabase/config.toml`, find line 155:

```toml
additional_redirect_urls = ["https://127.0.0.1:3000"]
```

Replace with:

```toml
additional_redirect_urls = ["http://127.0.0.1:3000", "http://127.0.0.1:3000/auth/callback", "http://127.0.0.1:3001/auth/callback"]
```

- [ ] **Step 2: Uncomment and configure the auth hook**

In `backend/supabase/config.toml`, find lines 267-269:

```toml
# [auth.hook.custom_access_token]
# enabled = true
# uri = "pg-functions://<database>/<schema>/<hook_name>"
```

Replace with:

```toml
[auth.hook.custom_access_token]
enabled = true
uri = "pg-functions://postgres/public/projectx_custom_access_token_hook"
```

- [ ] **Step 3: Verify email confirmations are disabled**

Confirm `backend/supabase/config.toml` line 209 has:

```toml
enable_confirmations = false
```

This is critical — without it, `supabase.auth.signUp()` sends a confirmation email and the user has no active session until they click it, breaking the entire invite flow. It was set during Phase 0 but verify it hasn't been reverted.

- [ ] **Step 4: Commit**

```bash
git add backend/supabase/config.toml
git commit -m "feat: register auth hook in config.toml, add redirect URLs"
```

---

### Task 4: Verify migrations — run supabase db reset

- [ ] **Step 1: Reset the database**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
```

Expected: Migrations run without error. Output includes `Applied migration 20260403000000_create_auth_tables.sql` and `20260403000001_auth_hook.sql`.

- [ ] **Step 2: Verify tables exist**

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -c "\dt public.*"
```

Expected: `companies`, `users`, `user_invites` all listed.

- [ ] **Step 3: Verify RLS is enabled**

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -c "SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('companies', 'users', 'user_invites');"
```

Expected: All three show `rowsecurity = t`.

- [ ] **Step 4: Verify XOR constraint works**

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -c "
INSERT INTO public.companies (name) VALUES ('TestCo') RETURNING id;
"
```

Then test the XOR constraint (should fail — neither `invited_by` nor `projectx_admin_id` is set):

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -c "
INSERT INTO public.user_invites (tenant_id, email, role, token_hash)
VALUES ((SELECT id FROM public.companies LIMIT 1), 'test@test.com', 'Recruiter', 'abc123');
"
```

Expected: `ERROR: new row for relation "user_invites" violates check constraint "invite_has_exactly_one_origin"`

- [ ] **Step 5: Verify hook function exists**

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -c "SELECT proname FROM pg_proc WHERE proname = 'projectx_custom_access_token_hook';"
```

Expected: One row returned.

- [ ] **Step 6: Clean up test data and re-reset**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
```

---

### Task 5: Update backend config

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/.env`
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1: Update config.py**

Replace the auth and Supabase sections (lines 18-28) in `backend/nexus/app/config.py`:

```python
    # Auth — ES256 JWKS verification (no shared secret)
    supabase_jwks_url: str = ""  # e.g. http://127.0.0.1:54321/auth/v1/.well-known/jwks.json

    # Candidate JWT (separate signing key — treat as DB credential)
    candidate_jwt_secret: str = "change-me-candidate-secret"
    candidate_jwt_algorithm: str = "HS256"

    # Notifications
    notifications_dry_run: bool = True  # True = log emails to stdout, False = send via Resend
```

This replaces the old `jwt_secret`, `jwt_algorithm`, `jwt_issuer`, `supabase_url`, and `supabase_jwt_secret` fields.

Also update the database URL default (line 13) to point to Supabase's Postgres:

```python
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"
```

And update CORS (line 60) to include admin panel:

```python
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"]
```

- [ ] **Step 2: Update .env**

Replace the auth section in `backend/nexus/.env`:

```
# Database (Supabase local Postgres)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres

# Auth — JWKS endpoint for ES256 JWT verification
SUPABASE_JWKS_URL=http://127.0.0.1:54321/auth/v1/.well-known/jwks.json

# Candidate JWT (separate from dashboard tokens)
CANDIDATE_JWT_SECRET=change-me-candidate-secret

# Notifications
NOTIFICATIONS_DRY_RUN=true

# CORS
CORS_ORIGINS=["http://localhost:3000","http://localhost:3001","http://127.0.0.1:3000","http://127.0.0.1:3001"]
```

Explicitly remove these stale fields from both `.env` and `.env.example`:
- `JWT_SECRET`
- `JWT_ALGORITHM`
- `SUPABASE_URL`
- `SUPABASE_JWT_SECRET`

Leaving stale fields causes confusion when someone reads the file later.

- [ ] **Step 3: Update .env.example to match .env**

Same changes as Step 2.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/.env backend/nexus/.env.example
git commit -m "feat: update config for ES256 JWKS auth, remove HS256 fields"
```

---

### Task 6: Update TokenPayload schema

**Files:**
- Modify: `backend/nexus/app/modules/auth/schemas.py`

- [ ] **Step 1: Update the TokenPayload model**

Replace the entire `TokenPayload` class (lines 14-21) in `backend/nexus/app/modules/auth/schemas.py`:

```python
class TokenPayload(BaseModel):
    """Decoded JWT payload from Supabase Auth (ES256 via JWKS).

    Custom claims (app_role, tenant_id, is_projectx_admin) are injected
    by the projectx_custom_access_token_hook Postgres function.
    """
    sub: str                         # Supabase Auth user UUID
    tenant_id: str = ""              # company UUID (empty for admins and pre-onboarding)
    app_role: str = ""               # RBAC role: Company Admin, Recruiter, etc.
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
```

> **Note:** Both `TokenPayload` and `CandidateTokenPayload` must be in this file — `service.py` imports both.

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/auth/schemas.py
git commit -m "feat: update TokenPayload for ES256 custom claims (app_role, is_projectx_admin)"
```

---

### Task 7: Rewrite auth service for JWKS/ES256 (TDD)

**Files:**
- Create: `backend/nexus/tests/test_auth_service.py`
- Modify: `backend/nexus/app/modules/auth/service.py`

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/test_auth_service.py`:

```python
"""Tests for provider-agnostic JWT verification (ES256 via JWKS)."""

import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.modules.auth.schemas import TokenPayload


@pytest.fixture
def ec_key_pair():
    """Generate a fresh EC P-256 key pair for testing."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture
def mock_jwks_client(ec_key_pair):
    """Mock PyJWKClient that returns our test public key."""
    _, public_key = ec_key_pair
    mock_jwk = MagicMock()
    mock_jwk.key = public_key
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_jwk
    return mock_client


def _make_token(private_key, **overrides) -> str:
    """Create a JWT signed with the test private key."""
    payload = {
        "sub": "test-user-uuid",
        "tenant_id": "test-tenant-uuid",
        "app_role": "Company Admin",
        "email": "admin@acme.com",
        "role": "authenticated",
        "is_projectx_admin": False,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "aud": "authenticated",
    }
    payload.update(overrides)
    return pyjwt.encode(payload, private_key, algorithm="ES256")


class TestVerifyAccessToken:
    def test_valid_token_returns_payload(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(private_key)

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token

            result = verify_access_token(token)

        assert result is not None
        assert isinstance(result, TokenPayload)
        assert result.sub == "test-user-uuid"
        assert result.tenant_id == "test-tenant-uuid"
        assert result.app_role == "Company Admin"
        assert result.email == "admin@acme.com"
        assert result.is_projectx_admin is False

    def test_expired_token_returns_none(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(private_key, exp=int(time.time()) - 10)

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token

            result = verify_access_token(token)

        assert result is None

    def test_tampered_token_returns_none(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(private_key)
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token

            result = verify_access_token(tampered)

        assert result is None

    def test_projectx_admin_token(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(
            private_key,
            tenant_id="",
            app_role="",
            is_projectx_admin=True,
        )

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token

            result = verify_access_token(token)

        assert result is not None
        assert result.is_projectx_admin is True
        assert result.tenant_id == ""
        assert result.app_role == ""

    def test_empty_custom_claims_returns_defaults(self, ec_key_pair, mock_jwks_client):
        """Token without custom claims (new user, no invite) should return defaults."""
        private_key, _ = ec_key_pair
        token = _make_token(private_key, tenant_id="", app_role="", is_projectx_admin=False)

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token

            result = verify_access_token(token)

        assert result is not None
        assert result.tenant_id == ""
        assert result.app_role == ""
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_auth_service.py -v 2>&1 | head -30
```

Expected: FAIL — `_get_jwks_client` does not exist yet.

- [ ] **Step 3: Implement JWKS-based verify_access_token**

Replace the entire content of `backend/nexus/app/modules/auth/service.py`:

```python
"""Provider-agnostic JWT verification.

Dashboard tokens are verified via JWKS (ES256) — no shared secret required.
Candidate tokens still use HS256 with a separate signing key.
"""

import structlog
import jwt
from jwt import PyJWKClient

from app.config import settings
from app.modules.auth.schemas import CandidateTokenPayload, TokenPayload

logger = structlog.get_logger()

# Module-level singleton — PyJWKClient handles key caching internally
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Lazy-init the JWKS client (cached for process lifetime)."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(settings.supabase_jwks_url, cache_keys=True)
    return _jwks_client


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
            app_role=payload.get("app_role", ""),
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


def verify_candidate_token(token: str) -> CandidateTokenPayload | None:
    """Verify a single-use candidate session JWT (HS256)."""
    try:
        payload = jwt.decode(
            token,
            settings.candidate_jwt_secret,
            algorithms=[settings.candidate_jwt_algorithm],
            options={"verify_exp": True},
        )
        return CandidateTokenPayload(
            sub=payload.get("sub", ""),
            session_id=payload.get("session_id", ""),
            tenant_id=payload.get("tenant_id", ""),
            exp=payload.get("exp", 0),
            iat=payload.get("iat", 0),
        )
    except jwt.ExpiredSignatureError:
        logger.warning("auth.candidate_token_expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("auth.candidate_token_invalid", error=str(exc))
        return None


def require_projectx_admin():
    """FastAPI dependency factory — rejects requests without is_projectx_admin claim.

    Usage: dependencies=[require_projectx_admin()]  ← called, returns Depends()
    Same pattern as require_roles(). Do NOT wrap in Depends() at the call site.
    """
    from fastapi import Depends, HTTPException, Request

    async def _check(request: Request) -> TokenPayload:
        payload: TokenPayload | None = getattr(request.state, "token_payload", None)
        if payload is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not payload.is_projectx_admin:
            raise HTTPException(status_code=403, detail="Not a ProjectX admin")
        return payload

    return Depends(_check)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_auth_service.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_auth_service.py app/modules/auth/service.py
git commit -m "feat: rewrite auth service for ES256 JWKS verification, add require_projectx_admin"
```

---

### Task 8: Add get_bypass_session (TDD)

**Files:**
- Create: `backend/nexus/tests/test_database.py`
- Modify: `backend/nexus/app/database.py`

- [ ] **Step 1: Write failing test**

Create `backend/nexus/tests/test_database.py`:

```python
"""Tests for database session helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import get_bypass_session


@pytest.mark.asyncio
async def test_bypass_session_sets_rls_flag():
    """get_bypass_session must SET LOCAL app.bypass_rls = 'true'."""
    mock_session = AsyncMock()
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        async with get_bypass_session() as session:
            pass

    # Verify SET LOCAL was called
    mock_session.execute.assert_called_once()
    call_args = mock_session.execute.call_args
    sql_text = str(call_args[0][0])
    assert "app.bypass_rls" in sql_text
    assert "true" in sql_text.lower()
```

- [ ] **Step 2: Run test — verify it fails**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_database.py -v
```

Expected: FAIL — `get_bypass_session` does not exist yet.

- [ ] **Step 3: Add get_bypass_session to database.py**

Add after the existing `get_session()` function (after line 44) in `backend/nexus/app/database.py`. The required imports (`asynccontextmanager`, `AsyncGenerator`, `sqlalchemy`) are already present at the top of the file.

```python
@asynccontextmanager
async def get_bypass_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with RLS bypass enabled.

    Used ONLY for: admin routes, complete-invite, and onboarding completion.
    Never use on endpoints reachable by regular client users.
    """
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                sqlalchemy.text("SET LOCAL app.bypass_rls = 'true'")
            )
            yield session
```

- [ ] **Step 4: Run test — verify it passes**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_database.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_database.py app/database.py
git commit -m "feat: add get_bypass_session for admin/onboarding RLS bypass"
```

---

### Task 9: Update AuthMiddleware

**Files:**
- Modify: `backend/nexus/app/middleware/auth.py`

- [ ] **Step 1: Add auth endpoints to public paths**

In `backend/nexus/app/middleware/auth.py`, keep the existing `_PUBLIC_PATHS` and `_CANDIDATE_PREFIXES` (already defined at lines 12-21), and add a new prefix set after them:

```python
# Add this after _CANDIDATE_PREFIXES (line 21)
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/auth/verify-invite",  # Public — invite token verification
)
```

- [ ] **Step 2: Update the dispatch method to check prefixes and read app_role**

Replace the `dispatch` method body (lines 32-61):

```python
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip auth for public endpoints
        if path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth for public path prefixes
        if path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        # Candidate paths use a different token flow
        if path.startswith(_CANDIDATE_PREFIXES):
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid authorization header"})

        token = auth_header.removeprefix("Bearer ").strip()

        # Verify through provider-agnostic interface
        payload = verify_access_token(token)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        # Attach to request state for downstream handlers
        request.state.token_payload = payload
        request.state.user_id = payload.sub
        request.state.tenant_id = payload.tenant_id
        request.state.app_role = payload.app_role
        request.state.is_projectx_admin = payload.is_projectx_admin

        return await call_next(request)
```

- [ ] **Step 3: Update require_roles to read app_role**

Replace the `require_roles` function (lines 64-81):

```python
def require_roles(*allowed_roles: str):
    """FastAPI dependency that enforces RBAC on a route.

    Reads app_role from the JWT — NOT the Postgres 'role' claim (which is always 'authenticated').
    """
    from fastapi import Depends, HTTPException, Request as FastAPIRequest

    async def _check(request: FastAPIRequest) -> TokenPayload:
        payload: TokenPayload | None = getattr(request.state, "token_payload", None)
        if payload is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if payload.app_role not in allowed_roles:
            logger.warning("rbac.denied", app_role=payload.app_role, allowed=allowed_roles, path=request.url.path)
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return payload

    return Depends(_check)
```

- [ ] **Step 4: Commit**

```bash
git add app/middleware/auth.py
git commit -m "feat: update middleware for app_role, is_projectx_admin, public auth paths"
```

---

### Task 10: Update main.py CORS + write frontend env files

**Files:**
- Modify: `backend/nexus/app/main.py`
- Modify: `frontend/app/.env.local`
- Modify: `frontend/admin/.env.local`

- [ ] **Step 1: CORS is already handled via config.py (Task 5)**

No changes needed in `main.py` — it already reads from `settings.cors_origins`.

- [ ] **Step 2: Write frontend env example files**

`.env.local` files are gitignored by Next.js by default. Create `.env.local.example` files (which ARE committed) to document the required env vars:

Write `frontend/app/.env.local.example`:

```
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<from supabase status>
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

Write `frontend/admin/.env.local.example`:

```
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<from supabase status>
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

Then write the actual `.env.local` files (not committed) with the real anon key:

```
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

- [ ] **Step 3: Commit (example files only — .env.local is gitignored)**

```bash
git add frontend/app/.env.local.example frontend/admin/.env.local.example
git commit -m "feat: add frontend env example files for local Supabase"
```

---

### Task 11: Run all tests + integration verification

- [ ] **Step 1: Run all backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests pass (test_health, test_auth_service, test_database).

- [ ] **Step 2: Reset Supabase and verify migrations**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
```

Expected: Both migrations apply cleanly.

- [ ] **Step 3: Test signup and verify JWT contains custom claims**

Sign up a test user via curl against the local Supabase Auth API:

```bash
curl -s -X POST http://127.0.0.1:54321/auth/v1/signup \
  -H "apikey: sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH" \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "testpassword123"}' | python3 -m json.tool
```

Expected: Response contains `access_token`. Decode the JWT to verify it contains `tenant_id`, `app_role`, and `is_projectx_admin` claims (all empty/false for a user with no invite):

```bash
# Decode the JWT payload (URL-safe base64 with padding fix — just to inspect claims)
python3 -c "
import base64, json, sys
payload = sys.argv[1].split('.')[1]
padding = 4 - len(payload) % 4
print(json.dumps(json.loads(base64.urlsafe_b64decode(payload + '=' * padding)), indent=2))
" "<access_token>"
```

Expected: JWT payload includes `"tenant_id": ""`, `"app_role": ""`, `"is_projectx_admin": false`.

- [ ] **Step 4: Verify FastAPI can decode the token via JWKS**

Start the backend (in a separate terminal or background):

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then test with the access token from Step 3:

```bash
curl -s http://127.0.0.1:8000/api/auth/me \
  -H "Authorization: Bearer <access_token>"
```

Expected: `{"status": "not_implemented"}` (200 OK, not 401). This confirms the middleware accepted and decoded the ES256 JWT via JWKS.

- [ ] **Step 5: Verify Alembic still works**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && alembic upgrade head
```

Expected: No errors (Alembic migrations directory is empty — `versions/.gitkeep` only — so this is a no-op but confirms no conflicts).

---

## Phase 1 Acceptance Criteria Checklist

- [ ] `supabase db reset` runs both migrations without error
- [ ] All 3 tables visible in Studio (http://127.0.0.1:54323) with RLS enabled
- [ ] XOR constraint rejects `user_invites` rows with neither or both origin fields
- [ ] Auth hook registered, enriches JWT with `tenant_id`, `app_role`, `is_projectx_admin`
- [ ] `verify_access_token()` decodes ES256 token via JWKS (unit tests pass)
- [ ] `require_projectx_admin()` dependency exists
- [ ] `get_bypass_session()` sets `app.bypass_rls = 'true'`
- [ ] AuthMiddleware reads `app_role` (not `role`) and attaches `is_projectx_admin`
- [ ] `/api/auth/verify-invite` is in public paths (no auth required)
- [ ] Frontend `.env.local` files contain Supabase credentials
- [ ] All backend tests pass
- [ ] `alembic upgrade head` still succeeds

---

## What's Next

After Phase 1 is verified, proceed to **Phase 2: Pipeline A Spine** — the end-to-end flow from admin provisioning a client to Company Admin completing the invite. Phase 2 will have its own implementation plan at `docs/superpowers/plans/2026-04-03-phase2-pipeline-a-spine.md`.
