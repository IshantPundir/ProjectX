# Roles & Permissions Refactor — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Scope:** Full-stack refactor of roles, permissions, and user management across backend (FastAPI/Supabase) and frontend (Next.js)

---

## Problem

Roles and permissions are stored directly on the `users` table as flat columns (`role`, `is_admin`, `permissions` JSONB). This conflates identity with authorization. The `user_org_assignments` junction table duplicates these fields. The JWT auth hook injects stale role data. Multiple layered migrations have made the schema dirty.

## Goals

1. Roles are functional archetypes (Recruiter, Interviewer, etc.) that define what a user can do in ProjectX.
2. Permissions are derived from roles — no independent permission storage per user.
3. A user can hold multiple roles within the same org unit and different roles across units.
4. Super admin is a tenant-level concept stored on `clients`, not tied to any org unit.
5. JWT stays thin — no role/permission data in the token.
6. Clean slate: squash all migrations into one correct initial schema.
7. Future-compatible: a `roles` table supports custom tenant-defined roles later.

## Non-Goals

- Custom roles CRUD (future feature — the schema supports it, but no API/UI for it now).
- Notification preferences (removed from users, can be re-added when needed).
- `culture_brief` on clients (removed for now).

---

## 1. Database Schema

All existing migrations are squashed into a single clean initial migration.

### 1.1 `clients`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK DEFAULT gen_random_uuid() | |
| name | TEXT NOT NULL | |
| domain | TEXT DEFAULT '' | |
| industry | TEXT DEFAULT '' | |
| size | TEXT DEFAULT '' | |
| logo_url | TEXT | |
| plan | TEXT NOT NULL DEFAULT 'trial' | trial, pro, enterprise |
| onboarding_complete | BOOLEAN NOT NULL DEFAULT FALSE | |
| super_admin_id | UUID FK → users NULLABLE DEFERRABLE INITIALLY DEFERRED | Set on invite completion |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| deleted_at | TIMESTAMPTZ | Soft delete |

**RLS:** Read by `id = current_setting('app.current_tenant')::UUID`. Service bypass for writes.

### 1.2 `users`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK DEFAULT gen_random_uuid() | |
| auth_user_id | UUID UNIQUE NOT NULL | Supabase Auth reference |
| tenant_id | UUID FK → clients NOT NULL | Tenant isolation |
| email | TEXT NOT NULL | |
| full_name | TEXT | |
| is_active | BOOLEAN NOT NULL DEFAULT TRUE | |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| deleted_at | TIMESTAMPTZ | Soft delete |

**Removed:** `role`, `is_admin`, `permissions`, `org_unit_id`, `parent_user_id`, `notification_prefs`

**RLS:** `tenant_id = current_setting('app.current_tenant')::UUID`. Service bypass.

**Indexes:** `tenant_id`, `auth_user_id` (unique).

### 1.3 `organizational_units`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK DEFAULT gen_random_uuid() | |
| client_id | UUID FK → clients NOT NULL | |
| parent_unit_id | UUID FK → self NULLABLE | Hierarchical |
| name | TEXT NOT NULL | |
| unit_type | TEXT NOT NULL | client_account, department, team, branch, region |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

**RLS:** `client_id = current_setting('app.current_tenant')::UUID`. Service bypass.

**Indexes:** `client_id`, `parent_unit_id`.

### 1.4 `roles`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK DEFAULT gen_random_uuid() | |
| tenant_id | UUID FK → clients NULLABLE | NULL = system role, set = custom tenant role (future) |
| name | TEXT NOT NULL | |
| description | TEXT DEFAULT '' | |
| permissions | JSONB NOT NULL DEFAULT '[]' | Array of permission strings |
| is_system | BOOLEAN NOT NULL DEFAULT FALSE | Prevents tenant modification/deletion |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

**Unique constraint:** `UNIQUE NULLS NOT DISTINCT (tenant_id, name)` — no duplicate role names within a scope. `NULLS NOT DISTINCT` (PostgreSQL 15+) ensures system roles (tenant_id = NULL) are also deduplicated.

**RLS:** `tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant', true)::UUID`. Service bypass.

**Indexes:** `is_system`, `tenant_id`.

**Pre-seeded system roles** (tenant_id = NULL, is_system = TRUE):

| Name | Permissions |
|---|---|
| Admin | users.invite_admins, users.invite_users, users.deactivate, org_units.create, org_units.manage, jobs.create, jobs.manage, candidates.view, candidates.evaluate, candidates.advance, interviews.schedule, interviews.conduct, reports.view, reports.export, settings.client, settings.integrations |
| Recruiter | jobs.create, jobs.manage, candidates.view, candidates.advance, interviews.schedule, reports.view |
| Hiring Manager | candidates.view, candidates.evaluate, candidates.advance, reports.view, reports.export |
| Interviewer | interviews.conduct, candidates.view, candidates.evaluate |
| Observer | candidates.view, reports.view |

### 1.5 `user_role_assignments`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK DEFAULT gen_random_uuid() | |
| user_id | UUID FK → users NOT NULL | |
| org_unit_id | UUID FK → organizational_units NOT NULL | |
| role_id | UUID FK → roles NOT NULL | |
| tenant_id | UUID FK → clients NOT NULL | Denormalized for RLS performance |
| assigned_by | UUID FK → users NULLABLE | |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

**Unique constraint:** `(user_id, org_unit_id, role_id)` — no duplicate assignments.

**RLS:** `tenant_id = current_setting('app.current_tenant')::UUID`. Service bypass.

**Indexes:** `user_id`, `org_unit_id`, `tenant_id`.

### 1.6 `user_invites`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK DEFAULT gen_random_uuid() | |
| tenant_id | UUID FK → clients NOT NULL | |
| invited_by | UUID FK → users NULLABLE | NULL if invited by projectx admin |
| projectx_admin_id | TEXT NULLABLE | NULL if invited by user |
| email | TEXT NOT NULL | |
| token_hash | TEXT UNIQUE NOT NULL | SHA-256 of raw token |
| status | TEXT NOT NULL DEFAULT 'pending' | pending, accepted, superseded, expired, revoked |
| superseded_by | UUID FK → self NULLABLE | |
| expires_at | TIMESTAMPTZ NOT NULL | Default: now() + 72 hours |
| accepted_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

**CHECK constraint:** Exactly one of `invited_by` or `projectx_admin_id` is set.

**Removed:** `role`, `is_admin`, `permissions`, `org_unit_id`

**RLS:** `tenant_id = current_setting('app.current_tenant')::UUID`. Service bypass.

**Indexes:** `tenant_id`, `token_hash` (unique), `(email, status)`.

### 1.7 Circular FK Resolution: `clients` ↔ `users`

`clients.super_admin_id → users.id` and `users.tenant_id → clients.id` is a circular dependency.

Resolution:
- `super_admin_id` is NULLABLE with `DEFERRABLE INITIALLY DEFERRED`
- Provisioning sequence (single transaction):
  1. `provision-client`: INSERT client (super_admin_id = NULL) + create invite
  2. `complete-invite`: INSERT user (tenant_id = client.id) → UPDATE clients SET super_admin_id = user.id

---

## 2. JWT Auth Hook

### `projectx_custom_access_token_hook(event JSONB) → JSONB`

Simplified. Only injects:
- `tenant_id` — from `users` or `user_invites`
- `is_projectx_admin` — from `auth.users.raw_app_meta_data`

**Removed from JWT claims:** `app_role`, `is_admin`, `org_unit_id`

**Logic flow:**
1. Check `is_projectx_admin` in app_metadata → return `{is_projectx_admin: true}` with empty tenant_id
2. Look up `users` by auth_user_id where is_active = TRUE → inject `tenant_id`
3. If no user found AND auth_method != 'token_refresh' → look up latest pending, non-expired invite by email → inject `tenant_id`
4. If no match → return empty claims (safe defaults)
5. Exception handler → return safe defaults, never block login

**Constraints:** 2-second hard timeout. READ-ONLY. Preserves all required JWT fields.

**Grants:**
```sql
GRANT EXECUTE ON FUNCTION projectx_custom_access_token_hook TO supabase_auth_admin;
GRANT SELECT ON users, user_invites TO supabase_auth_admin;
```

---

## 3. Backend Auth Middleware & Dependencies

### 3.1 Auth Middleware

Provider-agnostic JWT verification. Attaches to `request.state`:
- `user_id` (sub from JWT)
- `tenant_id` (from JWT claim)
- `is_projectx_admin` (from JWT claim)

**Removed from request.state:** `app_role`, `is_admin`, `org_unit_id`

### 3.2 `get_current_user_roles` Dependency

Runs per authenticated request. Builds a `UserContext` object:

```python
class UserContext:
    user: User                           # The user record
    is_super_admin: bool                 # clients.super_admin_id == user.id
    assignments: list[RoleAssignment]    # All role assignments with permissions

class RoleAssignment:
    org_unit_id: UUID
    org_unit_name: str
    role_id: UUID
    role_name: str
    permissions: list[str]
```

**Implementation constraint:** Single JOIN query across `user_role_assignments`, `roles`, and `organizational_units` to build the assignments list. The super admin check is a comparison against `clients.super_admin_id` (loaded alongside user or from the same query).

**Helper methods on UserContext:**
- `has_role_in_unit(org_unit_id, role_name) → bool`
- `has_permission_in_unit(org_unit_id, permission) → bool`
- `permissions_in_unit(org_unit_id) → set[str]` — union of all role permissions in that unit
- `all_permissions() → set[str]` — union across all units (for UI display)

### 3.3 Authorization Patterns

**Pattern 1 — Super admin only:**
```python
if not ctx.is_super_admin:
    raise HTTPException(403)
```

Used by: create org unit, invite user, deactivate user, resend/revoke invite, client settings.

**Pattern 2 — Super admin OR Admin in specific unit:**
```python
if not (ctx.is_super_admin or ctx.has_role_in_unit(org_unit_id, "Admin")):
    raise HTTPException(403)
```

Used by: assign role in unit, remove role from unit, remove user from unit, update unit, list unit members.

**Pattern 3 — Super admin OR specific permission in specific unit:**
```python
if not (ctx.is_super_admin or ctx.has_permission_in_unit(org_unit_id, "jobs.create")):
    raise HTTPException(403)
```

Used by: resource-scoped operations (jobs, candidates, interviews, reports).

---

## 4. Backend API Changes

### 4.1 Auth Module (`/api/auth/`)

**`GET /api/auth/me`**

Response:
```json
{
    "user_id": "uuid",
    "email": "user@example.com",
    "full_name": "Jane Doe",
    "tenant_id": "uuid",
    "client_name": "Acme Corp",
    "is_super_admin": true,
    "onboarding_complete": true,
    "has_org_units": true,
    "assignments": [
        {
            "org_unit_id": "uuid",
            "org_unit_name": "Engineering",
            "role_name": "Recruiter",
            "permissions": ["jobs.create", "jobs.manage", "..."]
        }
    ]
}
```

Two queries:
1. `user_role_assignments JOIN roles JOIN organizational_units` → assignments list
2. `SELECT EXISTS(SELECT 1 FROM organizational_units WHERE client_id = ...)` → has_org_units

**`GET /api/auth/verify-invite`**

Response: `{ "email": "...", "client_name": "..." }`

Removed: `role`

**`POST /api/auth/complete-invite`**

Simplified:
1. Verify raw_token, find pending non-expired invite
2. Atomically: UPDATE invite → accepted, INSERT user (identity only)
3. If invited by projectx_admin → UPDATE `clients.super_admin_id = user.id`
4. Redirect: super admin → `/onboarding`, others → `/`

No role assignment during invite completion.

**`POST /api/auth/onboarding/complete`** — Requires super admin (`clients.super_admin_id = user.id`, enforced in endpoint handler — not just implied by dashboard flow). Validates that at least one org unit exists before allowing completion. Returns 403 if caller is not super admin. Returns 400 if no org units exist for the tenant. This prevents a state where `onboarding_complete = true` but `has_org_units = false`.

### 4.2 Org Units Module (`/api/org-units/`)

| Endpoint | Method | Authorization | Notes |
|---|---|---|---|
| `/api/org-units` | POST | Super admin only | Create org unit |
| `/api/org-units` | GET | Authenticated | Super admin: all units. Others: units they're assigned to. Users with no assignments receive an empty list (not 403). |
| `/api/org-units/{id}` | PUT | Super admin OR Admin in unit | Update unit name/type |
| `/api/org-units/{id}/members` | GET | Super admin OR Admin in unit | List members with roles |
| `/api/org-units/{id}/members` | POST | Super admin OR Admin in unit | Assign one role: `{ user_id, role_id }` |
| `/api/org-units/{id}/members/{user_id}` | DELETE | Super admin OR Admin in unit | Remove ALL roles for user in unit |
| `/api/org-units/{id}/members/{user_id}/roles/{role_id}` | DELETE | Super admin OR Admin in unit | Remove specific role assignment |

**Member list response per member:**
```json
{
    "user_id": "uuid",
    "email": "...",
    "full_name": "...",
    "roles": [
        { "role_id": "uuid", "role_name": "Recruiter", "assigned_at": "..." },
        { "role_id": "uuid", "role_name": "Interviewer", "assigned_at": "..." }
    ]
}
```

**Auto-assignment on org unit creation removed.** The super admin creates the unit, then explicitly assigns members and roles.

### 4.3 Admin Module (`/api/admin/`)

| Endpoint | Method | Authorization | Notes |
|---|---|---|---|
| `/api/admin/provision-client` | POST | ProjectX admin | Create client + email-only invite |
| `/api/admin/clients` | GET | ProjectX admin | List clients (no invite role shown) |

`provision-client` no longer sets role/permissions/is_admin on the invite.

### 4.4 Team/Settings Module

| Endpoint | Method | Authorization | Notes |
|---|---|---|---|
| `/api/settings/team/members` | GET | Authenticated | List users + invites with assignments |
| `/api/settings/team/invite` | POST | Super admin only | `{ email }` — no role |
| `/api/settings/team/resend/{id}` | POST | Super admin only | Resend invite email |
| `/api/settings/team/revoke/{id}` | POST | Super admin only | Revoke pending invite |
| `/api/settings/team/deactivate/{id}` | POST | Super admin only | Deactivate user |

**Team member response:**
```json
{
    "id": "uuid",
    "email": "...",
    "full_name": "...",
    "is_active": true,
    "is_super_admin": false,
    "source": "user",
    "status": "active",
    "assignments": [
        {
            "org_unit_id": "uuid",
            "org_unit_name": "Engineering",
            "role_name": "Recruiter"
        }
    ]
}
```

### 4.5 Roles Endpoint (new, read-only)

| Endpoint | Method | Authorization | Notes |
|---|---|---|---|
| `/api/roles` | GET | Authenticated | System roles + tenant custom roles (future) |

Response:
```json
[
    { "id": "uuid", "name": "Admin", "description": "...", "permissions": [...], "is_system": true },
    { "id": "uuid", "name": "Recruiter", "description": "...", "permissions": [...], "is_system": true }
]
```

---

## 5. Frontend Changes

### 5.1 Types

```typescript
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
    onboarding_complete: boolean;
    has_org_units: boolean;
    assignments: RoleAssignment[];
}
```

Removed: `role`, `is_admin`, `permissions`, `org_unit_id`

### 5.2 Dashboard Layout

- `me.is_super_admin && !me.onboarding_complete` → redirect to `/onboarding` (onboarding flow includes creating first org unit; backend enforces at least one org unit exists before allowing onboarding completion)
- Nav items "Org Units" and "Team" visible to all authenticated users (team page is read-only for non-super-admins)

### 5.3 Profile Page

- Show "Super Admin" badge if `is_super_admin`
- Show assignments grouped by org unit:
  ```
  Engineering Department
    Recruiter, Interviewer
  Sales Team
    Admin
  ```
- If no assignments and not super admin: "No roles assigned yet. Contact your administrator."

### 5.4 Team Page

- Member list: visible to all authenticated users
- Each member shows super admin badge (if applicable) and aggregated role assignments
- **Super admin only actions:** invite form (email only), deactivate button, resend/revoke invite
- Non-super-admin users see the list as read-only

### 5.5 Org Units Page

Primary role management surface:
- Unit list: super admin sees all, others see units they're assigned to
- Member list per unit: shows all roles per user
- "Add member" dialog: select user (from tenant users) + select role (from `GET /api/roles`)
- Can add same user multiple times with different roles
- "Remove role" action per individual role row
- "Remove from unit" action removes all roles for that user in the unit
- **Authorization:** add/remove actions visible only to super admin or users with Admin role in that unit

### 5.6 Invite Page

- Shows: "You've been invited to join {client_name}"
- No role displayed (invites carry no role info)
- Password setup form unchanged

### 5.7 Proxy/Middleware & Login Page

No changes. Both only check `tenant_id` in JWT, which is still present.

---

## 6. Permission Definitions

All permissions (used by the system roles, available for future custom roles):

| Permission | Description |
|---|---|
| users.invite_admins | Invite users with admin privileges |
| users.invite_users | Invite regular users |
| users.deactivate | Deactivate user accounts |
| org_units.create | Create organizational units |
| org_units.manage | Manage org unit settings and members |
| jobs.create | Create job descriptions |
| jobs.manage | Edit/archive job descriptions |
| candidates.view | View candidate profiles |
| candidates.evaluate | Score/evaluate candidates |
| candidates.advance | Advance/reject candidates |
| interviews.schedule | Schedule interview sessions |
| interviews.conduct | Conduct live interviews |
| reports.view | View evaluation reports |
| reports.export | Export reports |
| settings.client | Manage client/tenant settings |
| settings.integrations | Manage ATS and other integrations |

---

## 7. Migration Strategy

**Clean slate approach:**
1. Delete all existing migration files in `backend/supabase/migrations/`
2. Write a single `YYYYMMDDHHMMSS_initial_schema.sql` that creates the correct schema from scratch
3. Include the auth hook function and its grants
4. Include RLS policies on all tables
5. Include seed data for system roles
6. Reset local Supabase: `supabase db reset`

**Alembic models** (`backend/nexus/app/models.py`): Rewrite to match the new schema exactly.

---

## 8. Authorization Summary

| Operation | Who |
|---|---|
| Provision client | ProjectX admin |
| Invite user to tenant | Super admin |
| Deactivate/reactivate user | Super admin |
| Resend/revoke invite | Super admin |
| Create org unit | Super admin |
| Update org unit | Super admin OR Admin in that unit |
| List org unit members | Super admin OR Admin in that unit |
| Assign role in org unit | Super admin OR Admin in that unit |
| Remove role from org unit | Super admin OR Admin in that unit |
| Remove user from org unit | Super admin OR Admin in that unit |
| Complete onboarding | Super admin only (explicit check, not just flow-implied) |
| Unit-scoped resource operations | Super admin OR user with required permission in that unit |
| View own profile | Any authenticated user |
| View team list | Any authenticated user |
| List available roles | Any authenticated user |
