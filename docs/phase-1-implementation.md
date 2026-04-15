# Phase 1 Implementation — Developer Documentation

**Scope:** Auth, Client Onboarding, Team Invites, Roles & Permissions, Org Units, Workspace Modes, Audit Log
**Status:** Complete and functional
**Last updated:** 2026-04-15

---

**Sibling phase documentation:**
Phase 2A (JD pipeline): `docs/phase-2a-implementation.md`
Phase 2B (signal editing): `docs/phase-2b-implementation.md`
Phase 2C.1 (pipeline builder): `docs/phase-2c1-implementation.md`
Phase 2C.2 (question bank generation): `docs/phase-2c2-implementation.md`
Post-2C hardening: `docs/phase-hardening-implementation.md`

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema](#2-database-schema)
3. [Auth System](#3-auth-system)
4. [Client Provisioning & Onboarding](#4-client-provisioning--onboarding)
5. [Team Invite System](#5-team-invite-system)
6. [Roles & Permissions](#6-roles--permissions)
7. [Organizational Units](#7-organizational-units)
8. [API Reference](#8-api-reference)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Post-Phase-1 Amendments (Hardening Batches)](#10-post-phase-1-amendments-hardening-batches)
11. [Known Gaps & Technical Debt](#11-known-gaps--technical-debt)

---

## 1. Architecture Overview

Phase 1 delivers the foundation: identity, multi-tenancy, team management, and organizational structure. No interview features are implemented yet.

### Three Deployable Surfaces

| Surface | Location | Purpose | Port |
|---|---|---|---|
| **Nexus** (FastAPI) | `backend/nexus/` | API server, all business logic | 8000 |
| **Admin App** (Next.js) | `frontend/admin/` | Internal ProjectX operator panel | 3001 |
| **Client App** (Next.js) | `frontend/app/` | B2B dashboard for recruiting teams | 3000 |

### Tech Stack (Phase 1 Actual)

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (async), Python 3.12 |
| Database | PostgreSQL 17 via Supabase (local Docker for dev) |
| ORM | SQLAlchemy async + asyncpg driver |
| Schema management | Supabase migrations (Phase 1 initial schema) + Alembic (Phase 2A onwards — see note below) |
| Auth provider | Supabase GoTrue (email/password only, no OAuth) |
| JWT verification | PyJWKClient (JWKS endpoint), ES256 algorithm |
| Frontend framework | Next.js 16.2.2, React 19, TypeScript strict |
| Frontend auth | @supabase/ssr v0.10 (cookie-based SSR sessions) |
| CSS | Tailwind CSS v4 |
| Email | Resend (with dry-run mode for dev) |
| State management | Local useState/useEffect (Zustand/TanStack Query not yet adopted) |

**Alembic vs. Supabase migrations:** Phase 1's initial schema lives in `backend/supabase/migrations/20260405000000_initial_schema.sql` — all Phase 1 DDL, RLS policies, seed data, and the auth hook are in the Supabase migration because Alembic was adopted later. Alembic is configured at `backend/nexus/migrations/env.py` and, as of 2026-04-15, has 12 revisions (0001 through 0012) covering Phase 2A / 2B / 2C.1 / 2C.2 schema changes and the post-2C hardening migrations (0008–0012) that repaired Phase 1's RLS pattern. See Section 10 for the hardening migrations that touched Phase 1 tables.

---

## 2. Database Schema

Six tables in `public` schema. All have RLS enabled.

### Entity Relationship

```
clients (tenant root)
  ├── users (1:N, via tenant_id)
  │     └── user_role_assignments (1:N, via user_id)
  ├── organizational_units (1:N, via client_id, self-referencing tree)
  │     └── user_role_assignments (1:N, via org_unit_id)
  ├── roles (1:N, via tenant_id; NULL = system role)
  │     └── user_role_assignments (1:N, via role_id)
  └── user_invites (1:N, via tenant_id)
```

### Table: `clients`

The tenant root entity. One row per company using ProjectX.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `name` | TEXT NOT NULL | Company name |
| `domain` | TEXT | Default `''` |
| `industry` | TEXT | Default `''` |
| `size` | TEXT | Default `''` |
| `logo_url` | TEXT | Nullable |
| `plan` | TEXT NOT NULL | `trial` / `pro` / `enterprise`, default `trial` |
| `onboarding_complete` | BOOLEAN | Default `false` — gates dashboard access |
| `workspace_mode` | TEXT NOT NULL | `'enterprise'` / `'agency'`, default `'enterprise'` — determines available unit types |
| `super_admin_id` | UUID FK -> users | DEFERRABLE INITIALLY DEFERRED (circular ref) |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |
| `deleted_at` | TIMESTAMPTZ | Soft delete, nullable |

**RLS:** SELECT where `id = current_setting('app.current_tenant')::UUID` + service bypass.

### Table: `users`

One row per dashboard user (human identity). Does NOT store roles — those are in `user_role_assignments`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `auth_user_id` | UUID UNIQUE NOT NULL | Maps to `auth.users.id` in Supabase |
| `tenant_id` | UUID FK -> clients NOT NULL | |
| `email` | TEXT NOT NULL | |
| `full_name` | TEXT | Nullable |
| `is_active` | BOOLEAN | Default `true` |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |
| `deleted_at` | TIMESTAMPTZ | Soft delete, nullable |

**RLS:** SELECT where `tenant_id = current_setting('app.current_tenant')::UUID` + service bypass.
**Auth hook bypass:** `supabase_auth_admin` has unrestricted SELECT (needed for the custom access token hook).

### Table: `organizational_units`

Hierarchical tree of company divisions. Self-referencing via `parent_unit_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `client_id` | UUID FK -> clients NOT NULL | Tenant discriminator (named `client_id`, not `tenant_id`) |
| `parent_unit_id` | UUID FK -> self | Nullable — NULL means top-level |
| `name` | TEXT NOT NULL | |
| `unit_type` | TEXT NOT NULL | `company` / `division` / `client_account` / `region` / `team` |
| `is_root` | BOOLEAN NOT NULL | Default `false` — `true` only for the auto-created company root unit |
| `company_profile` | JSONB | Required for `company` and `client_account` types. Contains: display_name, industry, company_size, culture_summary, hiring_bar, brand_voice, what_good_looks_like |
| `created_by` | UUID FK -> users | |
| `deletable_by` | UUID FK -> users | Specific user authorized to delete |
| `admin_delete_disabled` | BOOLEAN | Default `false` — if true, only super admin can delete |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

**RLS:** SELECT where `client_id = current_setting('app.current_tenant')::UUID` + service bypass.

### Table: `roles`

Role definitions. System roles have `tenant_id = NULL` and are visible to all tenants.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK -> clients | NULL = system-wide role |
| `name` | TEXT NOT NULL | |
| `description` | TEXT | Default `''` |
| `permissions` | JSONB NOT NULL | Array of permission strings, default `[]` |
| `is_system` | BOOLEAN | Default `false` |
| `created_at` | TIMESTAMPTZ | |

**Unique constraint:** `(tenant_id, name)` with `NULLS NOT DISTINCT`.
**RLS:** SELECT where `tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant')::UUID` + service bypass.

### Table: `user_role_assignments`

Junction table: user + org unit + role. A user can hold multiple roles across multiple org units.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK -> users NOT NULL | |
| `org_unit_id` | UUID FK -> organizational_units NOT NULL | |
| `role_id` | UUID FK -> roles NOT NULL | |
| `tenant_id` | UUID FK -> clients NOT NULL | Denormalized for RLS |
| `assigned_by` | UUID FK -> users | Who made the assignment |
| `created_at` | TIMESTAMPTZ | |

**Unique constraint:** `(user_id, org_unit_id, role_id)` — prevents duplicate assignments.
**RLS:** SELECT where `tenant_id = current_setting('app.current_tenant')::UUID` + service bypass.

### Table: `user_invites`

Tracks every invite sent. Token hashes only — raw tokens are never stored.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK -> clients NOT NULL | |
| `invited_by` | UUID FK -> users | NULL if admin-originated |
| `projectx_admin_id` | TEXT | Email of ProjectX admin; NULL if team-originated |
| `email` | TEXT NOT NULL | |
| `token_hash` | TEXT UNIQUE NOT NULL | SHA-256 of raw token |
| `status` | TEXT NOT NULL | `pending` / `accepted` / `superseded` / `expired` / `revoked` |
| `superseded_by` | UUID FK -> self | Points to replacement invite on resend |
| `expires_at` | TIMESTAMPTZ | Default `NOW() + 72 hours` |
| `accepted_at` | TIMESTAMPTZ | |
| `created_at` | TIMESTAMPTZ | |

**CHECK constraint `invite_origin_xor`:** Exactly one of `invited_by` or `projectx_admin_id` must be non-null. Enforces that every invite has a clear origin.
**Auth hook bypass:** `supabase_auth_admin` has unrestricted SELECT (needed for invite lookup during first login).

### Table: `audit_log`

Append-only audit trail for all tenant-scoped mutations. Never update or delete rows.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `tenant_id` | UUID FK -> clients NOT NULL | |
| `actor_id` | UUID FK -> users | Nullable — NULL for system-initiated or ProjectX admin actions |
| `actor_email` | TEXT | Human-readable actor identifier |
| `action` | TEXT NOT NULL | Dot-notation: `resource.verb` (e.g., `user.invited`) |
| `resource` | TEXT NOT NULL | Entity type (e.g., `user_invite`, `org_unit`, `client`) |
| `resource_id` | UUID | ID of the affected entity |
| `payload` | JSONB | Action-specific context (before/after, relevant IDs) |
| `ip_address` | TEXT | Client IP from request, nullable |
| `created_at` | TIMESTAMPTZ | |

**RLS:** SELECT where `tenant_id = current_setting('app.current_tenant')::UUID` + service bypass (all operations).

**Canonical action strings:** `user.invited`, `user.invite_resent`, `user.invite_revoked`, `user.invite_claimed`, `user.deactivated`, `org_unit.created`, `org_unit.updated`, `org_unit.deleted`, `org_unit.member_added`, `org_unit.member_removed`, `org_unit.role_removed`, `client.provisioned`, `client.onboarding_completed`

### System Roles (Seeded at Migration)

| Role | Permissions |
|---|---|
| Admin | All 16 permissions (full control) |
| Recruiter | `jobs.create`, `jobs.manage`, `candidates.view`, `candidates.advance`, `interviews.schedule`, `reports.view` |
| Hiring Manager | `candidates.view`, `candidates.evaluate`, `candidates.advance`, `reports.view`, `reports.export` |
| Interviewer | `interviews.conduct`, `candidates.view`, `candidates.evaluate` |
| Observer | `candidates.view`, `reports.view` |

### RLS Pattern

Every tenant-scoped query runs inside a transaction that first sets the runtime role and the session variable:

```sql
SET LOCAL ROLE nexus_app;
SET LOCAL app.current_tenant = '<tenant-uuid>';
```

RLS policies read this via `NULLIF(current_setting('app.current_tenant', true), '')::uuid`.

For admin/internal operations that need cross-tenant access:

```sql
SET LOCAL app.bypass_rls = 'true';
```

**`auth.jwt()` is NOT used in any RLS policy.** It returns null when connecting via asyncpg (it only works through PostgREST).

> Note (2026-04-15): As shipped in Phase 1, Phase 1 tables used a non-canonical `FOR SELECT USING(...)`-only policy pattern, the `tenant_isolation` USING clause used a raw `::uuid` cast, and the runtime did NOT switch to a NOBYPASSRLS role — all three were runtime no-ops or latent bugs. Migrations 0008–0012 repaired each of these. The canonical pattern shown above is what lives on Phase 1 tables today. See Section 10 for the full mechanism and each migration's scope. The per-table `RLS:` summaries in Section 2 describe the intent of each policy (SELECT filter on tenant_id + service bypass); the on-disk policies are now full-command `tenant_isolation` (USING + WITH CHECK) pairs plus `service_bypass`, with `audit_log` additionally carrying a `FOR INSERT WITH CHECK` policy so tenant sessions can write rows.

---

## 3. Auth System

### JWT Lifecycle

```
User submits email+password
    → Supabase GoTrue validates credentials
    → Auth hook fires (projectx_custom_access_token_hook)
    → Hook injects tenant_id + is_projectx_admin into JWT claims
    → JWT signed with ES256, returned to frontend
    → Frontend attaches Bearer token to every API call
    → AuthMiddleware verifies via JWKS endpoint
    → request.state populated with user context
    → Route handler processes request
```

### Custom Access Token Hook

**Location:** `backend/supabase/migrations/20260405000000_initial_schema.sql` (line 183)
**Registration:** `backend/supabase/config.toml` (line 267)

The PostgreSQL function `projectx_custom_access_token_hook` fires on every token issuance. It injects two custom claims:

- `tenant_id` (UUID string) — the company this user belongs to
- `is_projectx_admin` (boolean) — whether this is a ProjectX internal operator

**Decision tree (5 branches):**

1. **ProjectX admin?** Check `app_metadata.is_projectx_admin`. If true → `tenant_id=""`, `is_projectx_admin=true`. Return immediately.

2. **Known active user?** Query `public.users` by `auth_user_id = sub`. If found → stamp `tenant_id` from the user row. This is the normal post-onboarding path.

3. **Token refresh?** If `authentication_method.method = 'token_refresh'` and no user found → return empty claims. Skip invite lookup on refresh to avoid stale data.

4. **Pending invite?** Query `public.user_invites` by email where `status = 'pending'` AND `expires_at > NOW()`. If found → use invite's `tenant_id`. This bridges the gap where a user has signed up via Supabase but hasn't yet completed invite claiming — their first JWT already carries the correct `tenant_id`.

5. **Fallback.** `tenant_id=""`, `is_projectx_admin=false`. Never throws.

### Backend JWT Verification

**File:** `backend/nexus/app/modules/auth/service.py`

```python
# Uses PyJWKClient for JWKS-based verification
_jwks_client = PyJWKClient(settings.supabase_jwks_url)

def verify_access_token(token: str) -> TokenPayload | None:
    # Fetch signing key from JWKS endpoint (cached)
    # Decode with algorithms=["ES256", "RS256"], verify_exp=True
    # Extract: sub, tenant_id, email, role, is_projectx_admin, exp
    # Returns None on any failure — never raises
```

> Note (2026-04-15): Batch G hardened this path. `algorithms` is now pinned to `["ES256"]` only (RS256 removed), and both an audience check (`aud=authenticated`) and an issuer check (`iss == {supabase_url}/auth/v1` when configured) are enforced. See Section 10 for the full mechanism.

### Middleware Chain

**Registration order in `app/main.py`:**

```python
application.add_middleware(TenantMiddleware)   # runs outer
application.add_middleware(AuthMiddleware)      # runs inner
```

**Request flow:** `TenantMiddleware` → `AuthMiddleware` → Route Handler

- **AuthMiddleware** (`middleware/auth.py`): Extracts Bearer token, calls `verify_access_token()`, attaches `token_payload`, `user_id`, `tenant_id`, `is_projectx_admin` to `request.state`. Skips public paths (`/health`, `/docs`, `/api/auth/verify-invite`) and candidate paths (`/api/candidate-session/`).

- **TenantMiddleware** (`middleware/tenant.py`): Binds `tenant_id` from `request.state` to structlog context for structured logging.

**RLS enforcement** is NOT in middleware — it happens inside `get_tenant_db()` (a FastAPI dependency) which runs `SET LOCAL ROLE nexus_app` followed by `SET LOCAL app.current_tenant = '<uuid>'` at the start of each database session. The role switch is what makes RLS actually fire at runtime — the default `postgres` role has `rolbypassrls=true` and would otherwise silently skip every policy. See Section 10.

### Three Database Session Types

| Dependency | RLS Behavior | Used For |
|---|---|---|
| `get_tenant_db(request)` | Sets `app.current_tenant` | Normal tenant-scoped routes |
| `get_bypass_db()` | Sets `app.bypass_rls = 'true'` | Admin routes, invite claiming, onboarding |
| `get_session()` | No RLS variable set | Non-tenant operations |

### Token Types

| Token | Algorithm | Issuer | Lifetime | Purpose |
|---|---|---|---|---|
| Dashboard JWT | ES256 | Supabase GoTrue | 1 hour (refresh rotation) | Staff authentication |
| Candidate JWT | HS256 | Nexus backend | Configurable | Single-use session access |

### Environment Variables (Auth)

| Variable | Purpose |
|---|---|
| `SUPABASE_JWKS_URL` | JWKS endpoint for token verification |
| `SUPABASE_URL` | Supabase Admin API base URL (for user deletion) |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key for Admin API calls |
| `CANDIDATE_JWT_SECRET` | HS256 signing key for candidate tokens |

---

## 4. Client Provisioning & Onboarding

The full lifecycle of a new tenant: from ProjectX operator action to a fully set up company.

### Step 1 — ProjectX Admin Provisions Client

**Who:** ProjectX internal operator (has `is_projectx_admin` in their JWT)
**Where:** Admin App → `/dashboard/provision`
**API:** `POST /api/admin/provision-client`

```
ProjectX Admin fills form (company name, admin email, domain, industry, plan)
    → Backend creates Client row
    → Generates raw_token = secrets.token_urlsafe(32)
    → Stores SHA-256(raw_token) in user_invites table
    → Sends invite email with URL: {base_url}/invite?token={raw_token}
    → Raw token discarded from server memory
    → Returns client_id + invite_url to Admin App
```

**Key details:**
- The invite has `projectx_admin_id` set (admin email) and `invited_by = NULL`
- The `invite_origin_xor` constraint enforces this XOR
- The invite URL points to the Client App, not the Admin App

### Step 2 — Company Admin Receives Invite

The invited person clicks the link, lands on the Client App invite page (`/invite?token=<raw_token>`).

```
Frontend loads → calls GET /api/auth/verify-invite?token=<raw_token>
    → Backend hashes token, looks up invite by token_hash
    → Returns { email, client_name } if valid
    → Shows account setup form with email locked (read-only)
```

### Step 3 — Company Admin Creates Account & Claims Invite

```
User enters password → frontend calls supabase.auth.signUp({ email, password })
    → Supabase creates auth.users row
    → Auth hook fires → finds pending invite by email → injects tenant_id into JWT
    → Frontend gets JWT with tenant_id already set
    → Frontend calls POST /api/auth/complete-invite { raw_token }
    → Backend atomically:
        1. UPDATE user_invites SET status='accepted' WHERE token_hash=SHA256(raw_token)
        2. Verify invite.email matches JWT email
        3. CREATE users row (auth_user_id, tenant_id, email)
        4. Detect projectx_admin_id is set → this is a super admin
        5. UPDATE clients SET super_admin_id = new_user.id
        6. Auto-create root company unit (unit_type='company', is_root=true, placeholder company_profile)
    → Returns { redirect_to: "/onboarding", user_id, tenant_id, root_unit_id }
```

**If user already has a Supabase account** (e.g., re-accepting after a partial flow): the frontend falls back to `signInWithPassword()` instead of `signUp()`.

### Step 4 — Onboarding Wizard

**Route:** `/onboarding` (Client App)
**Guard:** Dashboard layout redirects here when `me.is_super_admin && !me.onboarding_complete`

Note: The root `company` unit was already auto-created in Step 3 (during invite claiming). Onboarding collects the workspace mode and company profile.

**Step 1 of 2 — Select Workspace Type:**
- Two cards: "We're hiring for our own company" (enterprise) / "We're a recruiting agency" (agency)
- API: `PATCH /api/settings/workspace` with `{ workspace_mode: "enterprise" | "agency" }`

**Step 2 of 2 — Company Profile:**
- Fetches org units via `GET /api/org-units`, finds root unit (`is_root === true`)
- Form: Company Name (required), Industry, Company Size, Culture Summary, What a Strong Hire Looks Like
- API: `PUT /api/org-units/{root_unit_id}` with `{ name, set_company_profile: true, company_profile: {...} }`
- API: `POST /api/auth/onboarding/complete`
- Backend validates: caller is super admin AND at least one org unit exists
- Sets `client.onboarding_complete = true`
- Frontend redirects to `/` (dashboard home)

### Sequence Diagram

```
ProjectX Admin          Admin App           Nexus API           Supabase Auth        Database
     │                     │                   │                     │                  │
     ├──[provision form]──→│                   │                     │                  │
     │                     ├──POST /provision──→│                    │                  │
     │                     │                   ├──INSERT client────────────────────────→│
     │                     │                   ├──INSERT invite (token_hash)──────────→│
     │                     │                   ├──send_email(invite_url)               │
     │                     │←──{invite_url}────┤                     │                  │
     │                     │                   │                     │                  │
Company Admin           Client App            │                     │                  │
     │                     │                   │                     │                  │
     ├──clicks invite──→  │                   │                     │                  │
     │                     ├──GET /verify-invite→                    │                  │
     │                     │←──{email, name}───┤                     │                  │
     ├──enters password──→│                   │                     │                  │
     │                     ├──signUp()─────────────────────────────→│                  │
     │                     │                   │    ┌─auth hook──→ │──SELECT invite──→│
     │                     │                   │    │  injects      │←─{tenant_id}────┤
     │                     │←──JWT(tenant_id)──────┘  tenant_id    │                  │
     │                     ├──POST /complete-invite→│               │                  │
     │                     │                   ├──UPDATE invite.status='accepted'────→│
     │                     │                   ├──INSERT user────────────────────────→│
     │                     │                   ├──UPDATE client.super_admin_id──────→│
     │                     │←──{redirect: /onboarding}              │                  │
     │                     │                   │                     │                  │
     ├──creates org unit──→├──POST /org-units──→                    │                  │
     │                     │                   ├──INSERT org unit───────────────────→│
     ├──completes──────→  ├──POST /onboarding/complete→             │                  │
     │                     │                   ├──UPDATE client.onboarding_complete──→│
     │                     │←──redirect to /───┤                     │                  │
```

---

## 5. Team Invite System

### Sending an Invite

**Who:** Super Admin only
**Where:** Client App → `/settings/team`
**API:** `POST /api/settings/team/invite`

```
Super Admin enters email
    → Backend generates raw_token = secrets.token_urlsafe(32)
    → Stores SHA-256(raw_token) in user_invites with invited_by = caller's user.id
    → Email sent via BackgroundTasks (after DB commit)
    → Returns { invite_url }
```

**No role is assigned at invite time.** The UI explicitly states: "Roles and org unit assignments can be configured after the user joins."

### Accepting an Invite

The flow is identical to the Company Admin invite (Step 3 above), with one difference:

- `projectx_admin_id` is NULL, `invited_by` is set → `is_super_admin = false`
- `redirect_to` is `"/"` (dashboard), not `"/onboarding"`

### Invite Management

| Action | API | Behavior |
|---|---|---|
| **Resend** | `POST /api/settings/team/resend/{invite_id}` | Sets old invite to `superseded`, creates new invite with new token, sends new email |
| **Revoke** | `POST /api/settings/team/revoke/{invite_id}` | Sets status to `revoked` — token can no longer be claimed |
| **Deactivate user** | `POST /api/settings/team/deactivate/{user_id}` | Sets `user.is_active = false`, revokes their accepted invites, deletes Supabase auth account via Admin API |

**Deactivation cascade:**
1. `user.is_active = false` (immediate — blocks all authenticated endpoints)
2. All `user_invites` for that email → `status = 'revoked'`
3. All `user_role_assignments` for the user in this tenant → deleted (removes from every org unit)
4. All `organizational_units` where `deletable_by = user.id` → `deletable_by = NULL`
5. Audit log entry recorded (action: `user.deactivated`)
6. HTTP DELETE to Supabase Admin API scheduled as a **background task** (best-effort cleanup, not a security boundary)

Self-deactivation is blocked.

### Invite Status Lifecycle

```
pending → accepted     (user claims the invite)
pending → superseded   (admin resends — new invite created)
pending → revoked      (admin explicitly revokes)
pending → expired      (72 hours pass — enforced in query, not by a cron)
```

---

## 6. Roles & Permissions

### Permission Model

Permissions are string-based, stored as JSONB arrays on `roles`. There are 16 canonical permissions defined in `backend/nexus/app/modules/auth/permissions.py`:

```
users.invite_admins    users.invite_users    users.deactivate
org_units.create       org_units.manage
jobs.create            jobs.manage
candidates.view        candidates.evaluate   candidates.advance
interviews.schedule    interviews.conduct
reports.view           reports.export
settings.client        settings.integrations
```

### How Permissions Are Checked

**Roles are NOT in the JWT.** This is by design — mutable state like role assignments must be fetched from the database on each request, not baked into a 1-hour-lived token.

The `UserContext` dataclass (loaded via `get_current_user_roles` dependency) provides:

```python
@dataclass
class UserContext:
    user: User
    is_super_admin: bool
    workspace_mode: str = "enterprise"  # from clients.workspace_mode
    assignments: list[RoleAssignment]   # pre-loaded with role + org_unit

    def has_role_in_unit(self, org_unit_id: UUID, role_name: str) -> bool
    def has_permission_in_unit(self, org_unit_id: UUID, permission: str) -> bool
    def permissions_in_unit(self, org_unit_id: UUID) -> set[str]
    def all_permissions(self) -> set[str]  # union across all assignments
```

### Authorization Guards

| Guard | Implementation | Used By |
|---|---|---|
| `require_projectx_admin()` | Checks `is_projectx_admin` JWT claim | Admin module routes |
| `require_super_admin()` | Checks `client.super_admin_id == user.id` | Team/settings routes |
| Unit Admin check | `is_super_admin OR has Admin role in unit` | Org unit management routes |

**There is no route-level RBAC middleware enforcing permissions by permission string.** Permission checking is done in route handlers/service layer using `UserContext` methods. This is an area for future hardening.

### Frontend Role Checks

The frontend uses two levels of role-based UI gating:

1. **`is_super_admin` boolean** — controls visibility of invite form, org unit creation, deactivation actions
2. **`canManage` computed** — `is_super_admin OR Admin role in the specific unit` — controls edit/delete/member-management UI in org unit detail

All enforcement is server-side. The frontend role checks are UX convenience, not security boundaries.

---

## 7. Organizational Units

### Unit Types

Valid types: `company`, `division`, `client_account`, `region`, `team`

| Type | Purpose | Rules |
|---|---|---|
| `company` | Root unit. Auto-created during onboarding. | Exactly one per tenant. `parent_unit_id` must be NULL. `is_root = true`. Non-deletable. Type immutable. `company_profile` required. |
| `division` | General intermediate grouping. | Cannot be nested under a `team`. No special data requirements. |
| `client_account` | Represents an external client (agency use). | Only when `workspace_mode = 'agency'`. `company_profile` required. Cannot nest under `team` or another `client_account`. |
| `region` | Geographic grouping. | Cannot be nested under a `team`. No special data requirements. |
| `team` | Leaf node. | Cannot have child units of any type. |

### Workspace Modes

Each tenant has a `workspace_mode` on the `clients` table:
- **`enterprise`** (default): Hiring for own company. `client_account` units not available.
- **`agency`**: Recruiting firm hiring for external clients. `client_account` units available.

Set during onboarding via `PATCH /api/settings/workspace`.

### Nesting Rules

| Unit type being created | Forbidden parent types |
|---|---|
| `company` | Any — must have NULL parent |
| `client_account` | `team`, `client_account` |
| `division` | `team` |
| `region` | `team` |
| `team` | `team` |

All nesting rules enforced in `create_org_unit` in `app/modules/org_units/service.py`.

### Tree Structure

Org units form a self-referencing hierarchy rooted at the `company` unit:
- The root company unit has `parent_unit_id = NULL` and `is_root = true`
- All other units are descendants of the company root
- Sub-units reference their parent via `parent_unit_id`

### Company Profile

`company` and `client_account` units store a `company_profile` JSONB field:
```json
{
  "display_name": "Acme Corp",
  "industry": "Technology",
  "company_size": "Enterprise (500+)",
  "culture_summary": "...",
  "hiring_bar": "...",
  "brand_voice": "professional",
  "what_good_looks_like": "..."
}
```

This profile is required (cannot be NULL) for these two types. Enforced at the application layer.

### Visibility Rules

- **Super Admin:** Sees all org units in the tenant
- **Other users:** See only units they have role assignments in, plus ancestor units in the tree (for navigation context)
- Units outside a user's access show `is_accessible: false` in the API response

### Member Management

Members are assigned to org units with specific roles:

- **Add member:** `POST /api/org-units/{unit_id}/members` with `{ user_id, role_id }`
- **Remove specific role:** `DELETE /api/org-units/{unit_id}/members/{user_id}/roles/{role_id}`
- **Remove all roles in unit:** `DELETE /api/org-units/{unit_id}/members/{user_id}`

### Deletion Rules

An org unit can only be deleted if:
1. `is_root` is `false` (the root company unit can never be deleted)
2. It has no sub-units
3. It has no member assignments
4. The caller has permission: super admin, OR (`canManage` AND `deletable_by == caller` AND `admin_delete_disabled == false`)

---

## 8. API Reference

### Auth Routes (`/api/auth`)

#### `GET /api/auth/verify-invite?token=<raw_token>`
**Auth:** Public (no token required)
**Purpose:** Validates an invite token before account creation
**Response:** `{ email: string, client_name: string }`
**Errors:** 400 (invalid/expired/already used)

#### `POST /api/auth/complete-invite`
**Auth:** Bearer (Supabase JWT)
**Body:** `{ raw_token: string }`
**Purpose:** Claims an invite, creates user row, sets super_admin_id if applicable. For super admins, also auto-creates the root `company` unit with a placeholder profile.
**Response:**
```json
{
  "redirect_to": "/onboarding" or "/",
  "user_id": "uuid",
  "tenant_id": "uuid",
  "root_unit_id": "uuid"  // auto-created company root (empty string for team members)
}
```
**Errors:** 400 (invalid token, email mismatch, already claimed)

#### `GET /api/auth/me`
**Auth:** Bearer (Supabase JWT)
**Purpose:** Returns current user profile with role assignments and onboarding status
**Response:**
```json
{
  "user_id": "uuid",
  "email": "user@example.com",
  "full_name": "Jane Doe",
  "tenant_id": "uuid",
  "is_super_admin": true,
  "onboarding_complete": true,
  "has_org_units": true,
  "workspace_mode": "enterprise",
  "assignments": [
    {
      "org_unit_id": "uuid",
      "org_unit_name": "Engineering",
      "role_id": "uuid",
      "role_name": "Admin",
      "permissions": ["users.invite_admins", "..."]
    }
  ]
}
```

#### `POST /api/auth/onboarding/complete`
**Auth:** Bearer (Super Admin only)
**Purpose:** Marks onboarding as complete
**Precondition:** At least one org unit must exist
**Response:** `{ status: "ok" }`

### Admin Routes (`/api/admin`)

#### `POST /api/admin/provision-client`
**Auth:** Bearer (`is_projectx_admin` required)
**Body:** `{ company_name, admin_email, domain?, industry?, plan? }`
**Purpose:** Creates a new tenant and sends the Company Admin invite
**Response:** `{ client_id: string, invite_url: string }`

#### `GET /api/admin/clients`
**Auth:** Bearer (`is_projectx_admin` required)
**Purpose:** Lists all clients with their super admin and invite status
**Response:** Array of `{ id, name, domain, plan, onboarding_complete, super_admin_email, invite_status, created_at }`

### Workspace Routes (`/api/settings`)

#### `PATCH /api/settings/workspace`
**Auth:** Bearer (Super Admin only)
**Body:** `{ workspace_mode: "enterprise" | "agency" }`
**Purpose:** Set workspace mode during onboarding. Determines which unit types are available.
**Response:** `{ status: "ok", workspace_mode: string }`

### Team Routes (`/api/settings/team`)

#### `POST /api/settings/team/invite`
**Auth:** Bearer (Super Admin only)
**Body:** `{ email: string }`
**Purpose:** Sends a team invite email
**Response:** `{ id, email, status, invite_url, expires_at, created_at }`

#### `GET /api/settings/team/members`
**Auth:** Bearer (any authenticated user)
**Purpose:** Lists active users and pending invites with role assignments
**Response:** Array of `TeamMember` — unified model with `source: "user" | "invite"` discriminator

#### `POST /api/settings/team/resend/{invite_id}`
**Auth:** Bearer (Super Admin only)
**Purpose:** Supersedes old invite, creates and sends new one

#### `POST /api/settings/team/revoke/{invite_id}`
**Auth:** Bearer (Super Admin only)
**Purpose:** Revokes a pending invite

#### `POST /api/settings/team/deactivate/{user_id}`
**Auth:** Bearer (Super Admin only)
**Purpose:** Deactivates user + deletes their Supabase auth account

### Org Unit Routes (`/api/org-units`)

#### `POST /api/org-units`
**Auth:** Bearer (Super Admin for top-level; Admin role in parent for sub-units)
**Body:** `{ name, unit_type, parent_unit_id? }`

#### `GET /api/org-units`
**Auth:** Bearer
**Purpose:** Returns org unit tree. Super admin sees all; others see assigned + ancestors.
**Response:** Array with `is_accessible` flag per unit

#### `PUT /api/org-units/{unit_id}`
**Auth:** Bearer (Super Admin or Admin in unit)
**Body:** `{ name?, unit_type?, deletable_by?, admin_delete_disabled? }`

#### `DELETE /api/org-units/{unit_id}`
**Auth:** Bearer (Super Admin or authorized Admin)
**Precondition:** No sub-units, no member assignments

#### `GET /api/org-units/{unit_id}/members`
**Auth:** Bearer (Super Admin or Admin in unit)
**Response:** Members with their role assignments

#### `POST /api/org-units/{unit_id}/members`
**Auth:** Bearer (Super Admin or Admin in unit)
**Body:** `{ user_id, role_id }`

#### `DELETE /api/org-units/{unit_id}/members/{user_id}`
**Auth:** Bearer — removes all roles for user in unit

#### `DELETE /api/org-units/{unit_id}/members/{user_id}/roles/{role_id}`
**Auth:** Bearer — removes specific role assignment

### Roles Route (`/api/roles`)

#### `GET /api/roles`
**Auth:** Bearer
**Purpose:** Lists system roles (visible to all) + tenant custom roles
**Response:** Array of `{ id, name, description, permissions, is_system }`

### Infrastructure

#### `GET /health`
**Auth:** Public
**Response:** `{ status: "ok" }`

---

## 9. Frontend Architecture

### Admin App (`frontend/admin/`)

Minimal internal tool for ProjectX operators. Six pages total.

| Route | Purpose | Auth |
|---|---|---|
| `/login` | Email/password login via Supabase | Public |
| `/signup` | Admin account creation | Public |
| `/pending-approval` | Post-signup waiting screen | Public (shows after signup) |
| `/dashboard` | Client list table | Client-side session check |
| `/dashboard/provision` | New client provisioning form | Client-side session check |

**Auth pattern:** Client-side only. `useEffect` checks `supabase.auth.getSession()` and redirects to `/login` if missing. No Next.js middleware. No server-side guard.

**No component library.** All UI is inline within page files. No `components/` directory.

### Client App (`frontend/app/`)

The main B2B product interface for recruiting teams.

| Route | Purpose | Auth |
|---|---|---|
| `/login` | Email/password + tenant_id JWT check | Public |
| `/invite?token=` | Invite acceptance + account setup | Public |
| `/onboarding` | 2-step wizard (create org unit → complete) | Requires valid session (backend enforces) |
| `/` | Dashboard home (placeholder) | Server-side auth guard |
| `/profile` | User profile + role assignments | Server-side auth guard |
| `/settings/team` | Team management, invites | Server-side auth guard |
| `/settings/org-units` | Org unit tree + create | Server-side auth guard |
| `/settings/org-units/[unitId]` | Unit detail, members, sub-units | Server-side auth guard |

**Auth pattern (dashboard routes):** Server-side guard in `app/(dashboard)/layout.tsx`:
1. `supabase.auth.getUser()` — validates session server-side
2. If no user → `redirect("/login")` — server redirect, no flash
3. Fetches `/api/auth/me` via `React.cache()` (deduplicated per render)
4. If `is_super_admin && !onboarding_complete` → `redirect("/onboarding")`

**Login tenant check:** After `signInWithPassword()`, the frontend manually decodes the JWT using `atob()` and checks for `tenant_id`. If missing (ProjectX admin account), signs out and shows error. This prevents admin accounts from accessing the client dashboard.

**API client (`lib/api/client.ts`):** Simple `fetch` wrapper. Injects `Authorization: Bearer` header. Handles FastAPI error shape (`{ detail: string }`). Token fetched fresh from `supabase.auth.getSession()` before each call.

### State Management (Current)

No Zustand or TanStack Query installed. Everything uses `useState` + `useEffect`:

```typescript
// Recurring pattern across all pages:
const [data, setData] = useState<T | null>(null);
const [loading, setLoading] = useState(true);

useEffect(() => {
  async function load() {
    const token = await getToken();
    const result = await apiFetch<T>("/api/...", { token });
    setData(result);
    setLoading(false);
  }
  load();
}, []);
```

### Dependencies (Actual, from package.json)

Both frontend apps have identical deps: `next@16.2.2`, `react@19`, `react-dom@19`, `@supabase/ssr@0.10`, `@supabase/supabase-js`, `typescript`, `@tailwindcss/postcss`, `tailwindcss@4`. No shadcn/ui, no Zustand, no TanStack Query, no React Hook Form, no Zod.

---

## 10. Post-Phase-1 Amendments (Hardening Batches)

This document was first written on 2026-04-07, before Batches A / E / F / G landed. Several changes shipped between 2026-04-14 and 2026-04-15 touched Phase 1 tables and the RLS enforcement model. The full walkthrough lives in `docs/phase-hardening-implementation.md`; a summary below so readers of this doc understand the current state of the Phase 1 tables.

### RLS pattern evolution

Phase 1 tables (`clients`, `users`, `organizational_units`, `roles`, `user_role_assignments`, `user_invites`, `audit_log`) originally shipped with a non-canonical RLS pattern. Five Alembic migrations repaired it:

| Migration | Fix |
|---|---|
| `0008_audit_log_tenant_insert` | `audit_log` was missing a `FOR INSERT WITH CHECK` policy, silently dropping tenant-scoped writes. Added the missing policy. |
| `0009_phase1_rls_full_command` | Phase 1 tables shipped with `FOR SELECT USING(...)`-only policies, which silently blocked INSERT/UPDATE/DELETE from tenant sessions because the implicit CHECK fell through to `service_bypass` (false when `app.bypass_rls` is unset). Replaced with the canonical full-command `tenant_isolation` pair (USING + WITH CHECK). |
| `0010_create_nexus_app_role` | Created the `nexus_app` Postgres role with `NOBYPASSRLS`. Every `get_tenant_db` / `get_bypass_db` session now runs `SET LOCAL ROLE nexus_app` at the top of the transaction. Without this, every RLS policy was a runtime no-op because the default `postgres` role has `rolbypassrls=true`. |
| `0011_rls_null_safe_current_tenant` | Wrapped `current_setting('app.current_tenant', true)::uuid` in `NULLIF(..., '')::uuid` on every policy. Fixes a Postgres quirk where `SET LOCAL` restores a custom GUC to empty string (not NULL) at transaction end, which crashed the next pooled request. |
| `0012_rename_service_role_bypass` | Renamed `service_role_bypass` → `service_bypass` on tables that were still on the old name, for consistency with the canonical name used by the startup assertion. |

Section 2's per-table `RLS:` summaries describe the intent of each policy (the SELECT filter on `tenant_id` and the service bypass). The on-disk policies after migration 0012 are canonical full-command `tenant_isolation` pairs (USING + WITH CHECK) plus `service_bypass`, with `audit_log` additionally carrying an INSERT WITH CHECK policy.

### Startup RLS completeness check

`app/main.py::_assert_rls_completeness` now queries `pg_policies` at boot and aborts with a CRITICAL log if any enumerated tenant-scoped table is missing `tenant_isolation` (with non-NULL `WITH CHECK`) or `service_bypass`. Skipped under `ENVIRONMENT=test` or when `DB_RUNTIME_ROLE` is unset (for the bootstrap-from-fresh-cluster case).

### Auth hardening

Phase 1's `verify_access_token` gained three additional checks in Batch G:

1. **Algorithm pinning** — `algorithms=["ES256"]` only. RS256 was removed to close an algorithm-confusion surface.
2. **Audience check** — `aud=authenticated` (Supabase's default role claim).
3. **Issuer check** — when `settings.supabase_url` is set, `iss` must match `{supabase_url}/auth/v1`. Prevents a token minted by a different Supabase project sharing the JWKS path from authenticating.

### Frontend hardening that affected Phase 1 surfaces

- **Security headers** on both `frontend/app/next.config.ts` and `frontend/admin/next.config.ts` — `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`. The dashboard additionally sets `Permissions-Policy: camera=(self), microphone=(self), geolocation=()`.
- **Configurable `FRONTEND_BASE_URL`** — removed the `settings.debug` branching that sent staging invites to production.
- **Same-origin redirect allowlist** on the invite completion flow.
- **CORS-on-401 fix** — the frontend couldn't read 401 error detail until CORS headers were applied on all error paths.

For the full mechanism of each fix and the commits that made them, see `docs/phase-hardening-implementation.md`.

---

## 11. Known Gaps & Technical Debt

### Architecture

| Gap | Impact | Priority |
|---|---|---|
| No `middleware.ts` in either frontend app | `/onboarding` route accessible by direct URL without auth; admin app has no server-side protection | Medium |
| Admin app has no server-side auth guard | Unauthenticated users see the admin shell briefly before client-side redirect | Low (internal tool) |
| Middleware ordering in Nexus | `TenantMiddleware` reads `request.state.tenant_id` before `AuthMiddleware` sets it — structlog tenant context is always `None` on inbound | Low (logging only, RLS unaffected) |
| Alembic adoption split point | Phase 1 DDL lives in the Supabase migration; Phase 2A onwards uses Alembic (revisions 0001–0012 as of 2026-04-15). New Phase 1 table changes now go through Alembic — see migrations 0008–0012 for examples. | Resolved |
| `settings/org-units/new/page.tsx` outside `(dashboard)` group | Legacy route — auth guard added, deprecation comment added. Should be removed in a future cleanup. | Low |
| `complete_invite` inline in router | Business logic (invite claiming, user creation) lives in `auth/router.py` instead of a service function. Audit call is a pragmatic exception. | Low (flagged with TODO) |

### Missing Libraries

| Library | Specified in CLAUDE.md | Status |
|---|---|---|
| Zustand | Yes | Not installed |
| TanStack Query | Yes | Not installed |
| React Hook Form + Zod | Yes | Not installed |
| shadcn/ui | Yes | Not installed |

These are specified as the intended stack but were not needed for Phase 1's relatively simple forms. Should be adopted before Phase 2 introduces more complex UI.

### Email

- `team_invite.html` template references `{{ role }}` variable that is never passed — renders as empty string
- Notifications run in dry-run mode by default (`NOTIFICATIONS_DRY_RUN=true`) — invite URLs displayed in UI instead of emailed
- Missing `seed.sql` file referenced in `supabase/config.toml`

### Security

| Item | Detail |
|---|---|
| No rate limiting on auth endpoints | `/api/auth/verify-invite` and `/api/auth/complete-invite` have no throttling |
| No per-permission route enforcement | Permissions exist but are checked ad-hoc, not declaratively per route |
| No session invalidation mechanism | Beyond deleting the Supabase auth user (nuclear option), there's no way to revoke a specific JWT |

---

## File Reference

### Backend — Essential Files

| File | Purpose |
|---|---|
| `backend/supabase/migrations/20260405000000_initial_schema.sql` | Complete DDL, RLS policies, system role seeds, auth hook |
| `backend/supabase/config.toml` | Supabase config, auth hook registration, JWT settings |
| `backend/nexus/app/main.py` | App factory, middleware + router registration |
| `backend/nexus/app/config.py` | All environment variables (pydantic-settings) |
| `backend/nexus/app/database.py` | Three session types (tenant, bypass, raw) |
| `backend/nexus/app/models.py` | All 7 ORM models (includes AuditLog) |
| `backend/nexus/app/middleware/auth.py` | JWT extraction, public path exclusions |
| `backend/nexus/app/middleware/tenant.py` | Structlog tenant context binding |
| `backend/nexus/app/modules/auth/service.py` | `verify_access_token()`, `require_projectx_admin()` |
| `backend/nexus/app/modules/auth/context.py` | `UserContext`, `get_current_user_roles()`, `require_super_admin()` |
| `backend/nexus/app/modules/auth/schemas.py` | `TokenPayload`, `MeResponse` |
| `backend/nexus/app/modules/auth/router.py` | verify-invite, complete-invite, /me, onboarding/complete |
| `backend/nexus/app/modules/auth/permissions.py` | 16 canonical permission constants |
| `backend/nexus/app/modules/admin/service.py` | `provision_client()` |
| `backend/nexus/app/modules/settings/service.py` | Team invite CRUD, user deactivation |
| `backend/nexus/app/modules/org_units/service.py` | Org unit CRUD, role assignment |
| `backend/nexus/app/modules/audit/service.py` | `log_event()` — append-only audit trail helper |
| `backend/nexus/app/modules/audit/actions.py` | Canonical audit action string constants |
| `backend/nexus/app/modules/notifications/service.py` | Email abstraction (dry-run/Resend) |
| `backend/supabase/migrations/20260405000001_audit_log.sql` | Audit log table DDL + RLS |
| `backend/supabase/migrations/20260406000000_unit_types_v2.sql` | One-root-per-tenant unique index |

### Frontend — Essential Files

| File | Purpose |
|---|---|
| `frontend/app/app/(dashboard)/layout.tsx` | Server-side auth guard, onboarding redirect, getMe |
| `frontend/app/app/(auth)/login/page.tsx` | Login + JWT tenant check |
| `frontend/app/app/(auth)/invite/page.tsx` | Invite acceptance + account setup |
| `frontend/app/app/onboarding/page.tsx` | 2-step onboarding: workspace type selection + company profile |
| `frontend/app/app/(dashboard)/settings/team/page.tsx` | Team management |
| `frontend/app/app/(dashboard)/settings/org-units/page.tsx` | Org unit tree |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx` | Unit detail + members |
| `frontend/app/app/(dashboard)/profile/page.tsx` | Profile + role assignments |
| `frontend/app/lib/api/client.ts` | API fetch wrapper |
| `frontend/app/lib/supabase/client.ts` | Browser Supabase client |
| `frontend/app/lib/supabase/server.ts` | Server Supabase client |
| `frontend/admin/app/(admin)/dashboard/provision/page.tsx` | Client provisioning form |
| `frontend/admin/app/(admin)/dashboard/page.tsx` | Client list |
