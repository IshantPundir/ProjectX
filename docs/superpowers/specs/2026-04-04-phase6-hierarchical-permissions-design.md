# Phase 6: Hierarchical Permission System + Table Rename — Design Spec

**Status:** Approved  
**Date:** 2026-04-04  
**Depends on:** Phases 1-3 complete (Phases 4-5 skipped)

---

## 1. Overview

Implement a delegated, tree-structured permission system where every user has a bounded permission set (subset of their parent's). Admin nodes (`is_admin=TRUE`) can invite others and create sub-admins. Leaf users (`is_admin=FALSE`) have role-based access only and cannot invite.

Also rename `public.companies` → `public.clients` throughout the entire stack.

---

## 2. Architecture

### Two Parallel Trees

**User invite tree** — governed by `users.parent_user_id`:
```
Super Admin (parent_user_id=NULL)
  └── Admin (parent_user_id=super_admin.id)
        └── Sub-Admin (parent_user_id=admin.id)
              └── Recruiter (parent_user_id=sub_admin.id)
```

**Org unit tree** — governed by `organizational_units.parent_unit_id`:
```
Accenture Account (client_account, parent_unit_id=NULL)
  └── NYC Team (team, parent_unit_id=accenture.id)
```

Every user row has `org_unit_id` pointing to their org unit. Super Admin has `org_unit_id=NULL`.

### JWT Claims (v2)

The auth hook injects 5 custom claims (was 3):

| Claim | Source | Notes |
|---|---|---|
| `tenant_id` | `users.tenant_id` | unchanged |
| `app_role` | `users.role` | unchanged |
| `is_admin` | `users.is_admin` | NEW — TRUE = can invite others |
| `org_unit_id` | `users.org_unit_id` | NEW — NULL for Super Admin |
| `is_projectx_admin` | `app_metadata` | unchanged |

**`permissions` JSONB is NOT in the JWT** — too large, goes stale. Fetched once via `GET /api/auth/me` and cached in component state.

### Permission Set (16 strings)

```python
ALL_PERMISSIONS = {
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
}
```

**Enforcement rule (service layer, not DB):** `new_user.permissions ⊆ inviting_user.permissions`

Super Admin gets all 16. Every downstream node gets a subset of their parent's.

---

## 3. What Stays the Same

- Invite token mechanics (generate, SHA-256 hash, atomic UPDATE-WHERE-RETURNING)
- Auth hook structure and error handling pattern
- `get_bypass_db()` / `get_tenant_db()` session pattern
- RLS `SET LOCAL` pattern
- Frontend invite claim flow (`/invite` page, `signUp()`, `POST /api/auth/complete-invite`)
- Admin panel auth flow (ProjectX admin panel unchanged structurally)
- Dry-run email mode
- Proxy/middleware routing logic (no new protected routes need `is_admin` gating at middleware level)

---

## 4. Database Schema

### Migration 4: `20260404000000_rename_clients_add_org_units.sql`

1. `ALTER TABLE public.companies RENAME TO clients` + rename trigger and policies
2. Create `public.organizational_units` (FK to `clients`, self-referencing `parent_unit_id`, `unit_type` CHECK, RLS with tenant isolation + service bypass)
3. Add to `public.users`: `parent_user_id UUID FK`, `org_unit_id UUID FK`, `permissions JSONB DEFAULT '[]'`, `is_admin BOOLEAN DEFAULT FALSE`. Update role CHECK to include `'Admin'`
4. Add to `public.user_invites`: `is_admin BOOLEAN DEFAULT FALSE`, `permissions JSONB DEFAULT '[]'`, `org_unit_id UUID FK`
5. Backfill: existing Company Admin users get `is_admin=TRUE` + all 16 permissions. Same for Company Admin invites.

### Migration 5: `20260404000001_auth_hook_v2.sql`

`CREATE OR REPLACE` the hook function. Now SELECTs `is_admin, org_unit_id` from `users` and `user_invites`, injects them as JWT claims. `org_unit_id` uses `CASE WHEN ... IS NOT NULL THEN to_jsonb(...) ELSE 'null'::jsonb END`.

Grants block includes all previous grants plus:
```sql
GRANT SELECT ON public.organizational_units TO supabase_auth_admin;
```

Also adds `auth_hook_read` RLS policy on `organizational_units` for `supabase_auth_admin` (defensive — prevents Phase 1-style silent RLS blocking).

---

## 5. Backend Changes

### Permission Module (new)

**File:** `app/modules/auth/permissions.py`

- `ALL_PERMISSIONS: frozenset[str]` — the 16 permission strings
- `SUPER_ADMIN_PERMISSIONS: list[str]` — sorted list of all 16
- `validate_permissions(new_perms, parent_perms)` — raises `ValueError` if `new ⊄ parent` or unknown permissions
- `require_permission(user_perms, perm)` — raises `ValueError` if user lacks specific permission

### TokenPayload Schema

Add `is_admin: bool = False` and `org_unit_id: str | None = None`.

### Auth Middleware

Attach `is_admin` and `org_unit_id` to `request.state` in dispatch method.

### `complete-invite` Endpoint

RETURNING clause adds `is_admin, permissions, org_unit_id, invited_by`. User row created with:
```python
user = User(
    auth_user_id=auth_user_id,
    tenant_id=claimed_row.tenant_id,
    email=oauth_email,
    role=claimed_row.role,
    is_admin=claimed_row.is_admin,
    permissions=claimed_row.permissions or [],  # guard against None
    org_unit_id=claimed_row.org_unit_id,
    parent_user_id=claimed_row.invited_by,
)
```

The invite row is the single source of truth for what the new user gets. No separate lookup, no race condition.

### `GET /api/auth/me`

`MeResponse` adds: `is_admin: bool`, `permissions: list[str]`, `org_unit_id: str | None`. Renames `company_name` → `client_name`.

### Admin Module (companies → clients rename)

- `Company` model → `Client` throughout (import, constructor, variables)
- Schemas: `company_name` → `client_name`, `company_id` → `client_id`
- Provision endpoint: invite gets `is_admin=True`, `permissions=SUPER_ADMIN_PERMISSIONS`, `org_unit_id=None`
- Admin panel frontend: 3 files updated (dashboard, provision, API calls) — field name swap only

### Settings Module (Team Invites)

**Schema changes:**
- `TeamInviteRequest` adds: `is_admin: bool = False`, `permissions: list[str] = []`, `org_unit_id: str | None = None`
- `TeamMember` adds: `is_admin: bool`, `permissions: list[str]`

**Service changes:**
- `create_team_invite` accepts `inviting_user_permissions`, `is_admin`, `permissions`, `org_unit_id`
- Validates: `require_permission` checks caller has `users.invite_admins` (admin invites) or `users.invite_users` (regular), then `validate_permissions` enforces subset constraint
- `list_team_members` returns `is_admin` and `permissions` for both user rows and invite rows
- `Company` → `Client` rename throughout

**Router changes:**
- `invite_endpoint` accepts `require_roles("Company Admin", "Admin")` — broadened to allow Admin role
- Fetches caller's permissions from DB inside `get_tenant_db` session (NOT bypass), passes to service
- Checks `admin_user.is_admin` before allowing invite

**NOT broadened:** `deactivate` and `revoke` endpoints keep current role restrictions. For MVP, same-tenant RLS is the scope guard on destructive endpoints.

### Org Units Module (new)

- `POST /api/org-units` — create org unit (validates `unit_type`)
- `GET /api/org-units` — list org units for tenant
- `PUT /api/org-units/{id}` — update name/type
- All use `get_tenant_db`, protected by `require_roles("Company Admin", "Admin")`
- Service validates `unit_type` against: `client_account`, `department`, `team`, `branch`, `region`

---

## 6. Frontend Changes

### Client Dashboard Proxy (`proxy.ts`)

No change to routing logic. New JWT claims (`is_admin`, `org_unit_id`) are available but the proxy doesn't gate on them — it just needs a valid tenant session.

### Dashboard Layout (`layout.tsx`)

`MeResponse` field rename: `company_name` → `client_name`. Update any reference.

### Team Management Page (`settings/team/page.tsx`)

Invite form expands:
1. **"Invite as Admin" toggle** — only visible if current user has `users.invite_admins` permission
2. **Permission multi-select** — shown when `is_admin=true`. Checkboxes for each of the 16 permissions, filtered to only show permissions the current user holds
3. **Org unit dropdown** — populated from `GET /api/org-units`. Optional field.
4. **When `is_admin=false`** — permissions selector hidden, role selector shows: Recruiter, Hiring Manager, Interviewer, Observer. When `is_admin=true` — role locked to "Admin"

Member list table adds:
- `is_admin` badge next to admin users
- Permission count (e.g., "12/16 permissions")
- `is_admin` and `permissions` shown for both accepted users and pending invites

### Admin Panel Frontend (3 files)

String replacements only — `company_name` → `client_name`, `company_id` → `client_id`:
- `frontend/admin/app/(admin)/dashboard/page.tsx`
- `frontend/admin/app/(admin)/dashboard/provision/page.tsx`
- API call payloads/responses

### Unchanged

- `/invite` page — structurally identical, passes through new fields
- `/login`, `/onboarding` — unchanged
- Admin panel auth pages — unchanged

---

## 7. Security Invariants

- Permission bounding enforced at service write time — `validate_permissions(new, parent)` before every invite INSERT
- `is_admin` check before `users.invite_admins`: user with `users.invite_users` but not `users.invite_admins` cannot create admin nodes
- RLS scopes all `get_tenant_db` queries — org_units has the same isolation pattern
- `permissions[]` NOT in JWT — fetched via `GET /api/auth/me`, prevents stale permission escalation
- Destructive endpoints (`deactivate`, `revoke`) NOT broadened to `"Admin"` role in this phase — MVP uses same-tenant RLS as scope guard
- `asyncpg` returns JSONB as Python list — add `or []` guard on permission assignment
- `ALTER TABLE RENAME` preserves FKs, indexes, and RLS policies

---

## 8. File Summary

### New files (7)
- `backend/supabase/migrations/20260404000000_rename_clients_add_org_units.sql`
- `backend/supabase/migrations/20260404000001_auth_hook_v2.sql`
- `backend/nexus/app/modules/auth/permissions.py`
- `backend/nexus/app/modules/org_units/__init__.py`
- `backend/nexus/app/modules/org_units/schemas.py`
- `backend/nexus/app/modules/org_units/service.py`
- `backend/nexus/app/modules/org_units/router.py`

### Modified files (16)
- `backend/nexus/app/models.py`
- `backend/nexus/app/modules/auth/schemas.py`
- `backend/nexus/app/modules/auth/router.py`
- `backend/nexus/app/modules/admin/schemas.py`
- `backend/nexus/app/modules/admin/service.py`
- `backend/nexus/app/modules/admin/router.py`
- `backend/nexus/app/modules/settings/schemas.py`
- `backend/nexus/app/modules/settings/service.py`
- `backend/nexus/app/modules/settings/router.py`
- `backend/nexus/app/middleware/auth.py`
- `backend/nexus/app/main.py`
- `scripts/supabase_hook.sql`
- `frontend/app/app/(dashboard)/settings/team/page.tsx`
- `frontend/app/app/(dashboard)/layout.tsx`
- `frontend/admin/app/(admin)/dashboard/page.tsx`
- `frontend/admin/app/(admin)/dashboard/provision/page.tsx`

---

## 9. Acceptance Criteria

- [ ] `supabase db reset` applies all 5 migrations cleanly (3 existing + 2 new)
- [ ] `public.companies` table no longer exists; `public.clients` exists with same data
- [ ] `public.organizational_units` table exists with correct RLS + `auth_hook_read` policy
- [ ] `public.users` has columns: `parent_user_id`, `org_unit_id`, `permissions`, `is_admin`
- [ ] `public.user_invites` has columns: `is_admin`, `permissions`, `org_unit_id`
- [ ] JWT for existing Company Admin contains `is_admin: true`, `org_unit_id: null`
- [ ] JWT for a new Admin user contains `is_admin: true`, `org_unit_id: <uuid>`
- [ ] JWT for a Recruiter contains `is_admin: false`
- [ ] `GET /api/auth/me` returns `is_admin`, `permissions[]`, `org_unit_id`, `client_name`
- [ ] `POST /api/org-units` creates an org unit; `GET /api/org-units` lists them
- [ ] `POST /api/settings/team/invite` with `is_admin=true, permissions=[...]` validates subset constraint
- [ ] Inviting a user with permissions exceeding your own returns 400
- [ ] Inviting an admin without `users.invite_admins` permission returns 400
- [ ] Existing tests still pass (after Company→Client rename updates)
- [ ] Admin panel provisioning works (client created, Company Admin invite sent with full permissions)
- [ ] Team member list shows `is_admin` and `permissions` for both users and invites
- [ ] `deactivate` and `revoke` endpoints NOT broadened to Admin role
