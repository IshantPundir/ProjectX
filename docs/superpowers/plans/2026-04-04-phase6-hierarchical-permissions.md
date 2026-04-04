# Phase 6: Hierarchical Permissions + Table Rename — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a delegated, tree-structured permission system with 16 bounded permissions, organizational units, and rename `companies → clients` throughout the stack.

**Architecture:** Two new Supabase migrations (rename + org units + permission columns, auth hook v2). Backend gets a permissions module for subset validation, updated auth endpoints that carry `is_admin`/`permissions`/`org_unit_id` through the invite → user flow, broadened settings invite endpoint for Admin role, and a new org units CRUD module. Frontend team page gets admin toggle + permission selector + org unit dropdown.

**Tech Stack:** PostgreSQL 17 (Supabase CLI), SQLAlchemy async, FastAPI, Next.js 16, Tailwind v4

**Depends on:** Phases 1-3 complete

**Design spec:** `docs/superpowers/specs/2026-04-04-phase6-hierarchical-permissions-design.md`

---

## File Map

### New Files (7)
| File | Responsibility |
|---|---|
| `backend/supabase/migrations/20260404000000_rename_clients_add_org_units.sql` | Rename companies→clients, create org_units table, add permission columns |
| `backend/supabase/migrations/20260404000001_auth_hook_v2.sql` | Auth hook v2 with is_admin + org_unit_id claims + grants |
| `backend/nexus/app/modules/auth/permissions.py` | Permission constants + validation (ALL_PERMISSIONS, validate_permissions, require_permission) |
| `backend/nexus/app/modules/org_units/__init__.py` | Package init |
| `backend/nexus/app/modules/org_units/schemas.py` | Org unit request/response schemas |
| `backend/nexus/app/modules/org_units/service.py` | Org unit CRUD service |
| `backend/nexus/app/modules/org_units/router.py` | Org unit API routes |

### Modified Files (16)
| File | What changes |
|---|---|
| `backend/nexus/app/models.py` | Company→Client, add OrganizationalUnit, add User/UserInvite permission columns |
| `backend/nexus/app/modules/auth/schemas.py` | TokenPayload: add is_admin, org_unit_id. MeResponse: add is_admin, permissions, org_unit_id; company_name→client_name. VerifyInviteResponse: company_name→client_name |
| `backend/nexus/app/modules/auth/router.py` | complete-invite: RETURNING adds new fields, User creation includes them. me: returns new fields. verify-invite: Company→Client. onboarding/complete: Company→Client |
| `backend/nexus/app/modules/admin/schemas.py` | company_name→client_name, company_id→client_id |
| `backend/nexus/app/modules/admin/service.py` | Company→Client, invite gets is_admin+permissions |
| `backend/nexus/app/modules/admin/router.py` | Variable renames |
| `backend/nexus/app/modules/settings/schemas.py` | TeamInviteRequest: add is_admin, permissions, org_unit_id. TeamMember: add is_admin, permissions |
| `backend/nexus/app/modules/settings/service.py` | Company→Client, permission validation, new invite fields |
| `backend/nexus/app/modules/settings/router.py` | Broaden invite to "Company Admin"+"Admin", fetch permissions from DB |
| `backend/nexus/app/middleware/auth.py` | Attach is_admin, org_unit_id to request.state |
| `backend/nexus/app/main.py` | Register org_units router |
| `scripts/supabase_hook.sql` | Replace with v2 |
| `frontend/app/app/(dashboard)/settings/team/page.tsx` | Admin toggle, permission selector, org unit dropdown |
| `frontend/app/app/(dashboard)/layout.tsx` | company_name→client_name in MeResponse type |
| `frontend/admin/app/(admin)/dashboard/page.tsx` | company_name→client_name, company_id→client_id |
| `frontend/admin/app/(admin)/dashboard/provision/page.tsx` | company_name→client_name |

---

### Task 1: Supabase migration — rename + org_units + permission columns

**Files:**
- Create: `backend/supabase/migrations/20260404000000_rename_clients_add_org_units.sql`

- [ ] **Step 1: Write the migration**

Create `backend/supabase/migrations/20260404000000_rename_clients_add_org_units.sql` using the SQL from the design spec. The migration must:

1. `ALTER TABLE public.companies RENAME TO clients` + rename trigger + rename policies
2. Create `public.organizational_units` with `client_id` FK, self-referencing `parent_unit_id`, `unit_type` CHECK, RLS (tenant_isolation + service_bypass), trigger, indexes
3. Add to `public.users`: `parent_user_id UUID FK`, `org_unit_id UUID FK`, `permissions JSONB DEFAULT '[]'`, `is_admin BOOLEAN DEFAULT FALSE`. Drop and re-add role CHECK to include `'Admin'`
4. Add to `public.user_invites`: `is_admin BOOLEAN DEFAULT FALSE`, `permissions JSONB DEFAULT '[]'`, `org_unit_id UUID FK`
5. Backfill existing Company Admin users and invites with `is_admin=TRUE` + all 16 permissions

The full SQL is provided in the user's spec. Use it as a template — verify column names and constraints match the existing schema before writing.

- [ ] **Step 2: Commit**

```bash
git add backend/supabase/migrations/20260404000000_rename_clients_add_org_units.sql
git commit -m "feat: migration to rename companies→clients, add org_units, add permission columns"
```

---

### Task 2: Supabase migration — auth hook v2

**Files:**
- Create: `backend/supabase/migrations/20260404000001_auth_hook_v2.sql`
- Modify: `scripts/supabase_hook.sql`

- [ ] **Step 1: Write the auth hook v2 migration**

`CREATE OR REPLACE` the hook function. Now SELECTs `is_admin, org_unit_id` from both `users` and `user_invites` and injects them as JWT claims. Uses `CASE WHEN ... IS NOT NULL THEN to_jsonb(...)::TEXT ELSE 'null'::jsonb END` for nullable `org_unit_id`.

Grants block must include:
```sql
GRANT EXECUTE ON FUNCTION public.projectx_custom_access_token_hook TO supabase_auth_admin;
GRANT USAGE ON SCHEMA public TO supabase_auth_admin;
GRANT SELECT ON public.users TO supabase_auth_admin;
GRANT SELECT ON public.user_invites TO supabase_auth_admin;
GRANT SELECT ON public.organizational_units TO supabase_auth_admin;
REVOKE EXECUTE ON FUNCTION public.projectx_custom_access_token_hook FROM authenticated, anon, public;

CREATE POLICY "auth_hook_read" ON public.organizational_units
  FOR SELECT
  TO supabase_auth_admin
  USING (true);
```

The full SQL is in the user's spec — use as template.

- [ ] **Step 2: Update scripts/supabase_hook.sql**

Replace the entire content of `scripts/supabase_hook.sql` with the auth hook v2 SQL (same as the migration), keeping the production deployment header comment.

- [ ] **Step 3: Commit**

```bash
git add backend/supabase/migrations/20260404000001_auth_hook_v2.sql scripts/supabase_hook.sql
git commit -m "feat: auth hook v2 with is_admin + org_unit_id claims"
```

---

### Task 3: Verify migrations

- [ ] **Step 1: Reset database**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
```

Expected: All 5 migrations apply cleanly.

- [ ] **Step 2: Verify table rename**

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -c "\dt public.*" | grep -E "clients|companies|organizational"
```

Expected: `clients` and `organizational_units` present. `companies` absent.

- [ ] **Step 3: Verify new columns**

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -c "\d public.users" | grep -E "parent_user|org_unit|permissions|is_admin"
```

Expected: All 4 new columns present.

- [ ] **Step 4: Verify hook returns new claims**

```bash
docker exec -i $(docker ps -q -f name=supabase_db) psql -U postgres -t -c "
SELECT public.projectx_custom_access_token_hook('{
  \"user_id\": \"00000000-0000-0000-0000-000000000000\",
  \"claims\": {\"sub\": \"test\", \"email\": \"test@test.com\", \"role\": \"authenticated\", \"aud\": \"authenticated\", \"app_metadata\": {}},
  \"authentication_method\": \"password\"
}'::jsonb);" | python3 -m json.tool | grep -E "is_admin|org_unit_id"
```

Expected: `"is_admin": false` and `"org_unit_id": null`.

---

### Task 4: ORM models update

**Files:**
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Replace models.py**

Replace the entire file. Changes:
- `Company` class → `Client` class, `__tablename__ = "clients"`
- New `OrganizationalUnit` class
- `User` class: add `is_admin`, `permissions`, `org_unit_id`, `parent_user_id` columns
- `UserInvite` class: add `is_admin`, `permissions`, `org_unit_id` columns
- All FKs referencing `companies.id` → `clients.id`

Use the model code from the user's spec as template. Ensure every column matches the migration.

- [ ] **Step 2: Commit**

```bash
git add app/models.py
git commit -m "feat: update ORM models — Company→Client, add OrganizationalUnit, permission columns"
```

---

### Task 5: Permission constants module

**Files:**
- Create: `backend/nexus/app/modules/auth/permissions.py`

- [ ] **Step 1: Create permissions.py**

Use the code from the user's spec. Contains:
- `ALL_PERMISSIONS: frozenset[str]` — 16 permission strings
- `SUPER_ADMIN_PERMISSIONS: list[str]` — sorted list of all 16
- `validate_permissions(new_permissions, parent_permissions)` — raises `ValueError` if new ⊄ parent or unknown perms
- `require_permission(user_permissions, permission)` — raises `ValueError` if user lacks perm

- [ ] **Step 2: Commit**

```bash
git add app/modules/auth/permissions.py
git commit -m "feat: add permission constants and validation module"
```

---

### Task 6: Auth schemas + middleware update

**Files:**
- Modify: `backend/nexus/app/modules/auth/schemas.py`
- Modify: `backend/nexus/app/middleware/auth.py`

- [ ] **Step 1: Update TokenPayload**

Add to `TokenPayload` class:
```python
    is_admin: bool = False            # NEW: TRUE = can invite others
    org_unit_id: str | None = None    # NEW: UUID string or None for Super Admin
```

- [ ] **Step 2: Update MeResponse**

Add fields and rename:
```python
class MeResponse(BaseModel):
    user_id: str
    auth_user_id: str
    email: str
    full_name: str | None
    role: str
    is_admin: bool                # NEW
    permissions: list[str]        # NEW
    org_unit_id: str | None       # NEW
    tenant_id: str
    client_name: str              # was company_name
    onboarding_complete: bool
```

- [ ] **Step 3: Update VerifyInviteResponse**

Rename `company_name` → `client_name`:
```python
class VerifyInviteResponse(BaseModel):
    email: str
    role: str
    client_name: str              # was company_name
```

- [ ] **Step 4: Update Role enum**

Add `ADMIN = "Admin"` to the `Role` enum.

- [ ] **Step 5: Update middleware dispatch**

In `app/middleware/auth.py`, add after `request.state.is_projectx_admin = payload.is_projectx_admin`:
```python
        request.state.is_admin = payload.is_admin
        request.state.org_unit_id = payload.org_unit_id
```

- [ ] **Step 6: Update verify_access_token in service.py**

In `app/modules/auth/service.py`, update the `TokenPayload` construction to include:
```python
            is_admin=payload.get("is_admin", False),
            org_unit_id=payload.get("org_unit_id"),
```

- [ ] **Step 7: Commit**

```bash
git add app/modules/auth/schemas.py app/middleware/auth.py app/modules/auth/service.py
git commit -m "feat: update TokenPayload, MeResponse, middleware for is_admin + org_unit_id"
```

---

### Task 7: Auth endpoints update (complete-invite, me, verify-invite, onboarding)

**Files:**
- Modify: `backend/nexus/app/modules/auth/router.py`

- [ ] **Step 1: Update verify-invite**

Change `Company` import to `Client`. Update the join and response:
```python
from app.models import Client, User, UserInvite
# ...
.join(Client, UserInvite.tenant_id == Client.id)
# ...
return VerifyInviteResponse(
    email=invite.email,
    role=invite.role,
    client_name=company.name,  # variable can stay as 'company' internally
)
```

- [ ] **Step 2: Update complete-invite RETURNING clause**

Add new fields to the raw SQL:
```python
result = await db.execute(
    sqlalchemy.text("""
        UPDATE public.user_invites
           SET status = 'accepted', accepted_at = NOW()
         WHERE token_hash  = :token_hash
           AND status      = 'pending'
           AND expires_at  > NOW()
        RETURNING id, tenant_id, email, role, is_admin, permissions, org_unit_id, invited_by
    """),
    {"token_hash": token_hash},
)
```

Update User creation:
```python
    user = User(
        auth_user_id=auth_user_id,
        tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
        email=oauth_email,
        role=claimed_row.role,
        is_admin=claimed_row.is_admin,
        permissions=claimed_row.permissions or [],  # guard against None
        org_unit_id=claimed_row.org_unit_id,
        parent_user_id=claimed_row.invited_by,
    )
```

- [ ] **Step 3: Update GET /me**

Change `Company` to `Client`, update response:
```python
    return MeResponse(
        user_id=str(user.id),
        auth_user_id=str(user.auth_user_id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_admin=user.is_admin,
        permissions=user.permissions or [],
        org_unit_id=str(user.org_unit_id) if user.org_unit_id else None,
        tenant_id=str(user.tenant_id),
        client_name=client.name,
        onboarding_complete=client.onboarding_complete,
    )
```

- [ ] **Step 4: Update onboarding/complete**

Change `Company` to `Client` in the import and query.

- [ ] **Step 5: Commit**

```bash
git add app/modules/auth/router.py
git commit -m "feat: update auth endpoints for permission fields + Company→Client rename"
```

---

### Task 8: Admin module rename

**Files:**
- Modify: `backend/nexus/app/modules/admin/schemas.py`
- Modify: `backend/nexus/app/modules/admin/service.py`
- Modify: `backend/nexus/app/modules/admin/router.py`

- [ ] **Step 1: Update admin schemas**

Rename all `company_*` fields to `client_*`:
- `ProvisionClientRequest`: `company_name` → `client_name`
- `ProvisionClientResponse`: `company_id` → `client_id`
- `ClientListItem`: `company_id` → `client_id`, `company_name` → `client_name`

- [ ] **Step 2: Update admin service**

- Replace `from app.models import Company` with `from app.models import Client`
- Replace `Company(...)` with `Client(...)`
- Add `is_admin=True` and `permissions=SUPER_ADMIN_PERMISSIONS` to the Company Admin invite:
```python
from app.modules.auth.permissions import SUPER_ADMIN_PERMISSIONS

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
- Update `list_clients` to use `Client` instead of `Company`
- Update return dict keys: `company_id` → `client_id`, `company_name` → `client_name`
- Update the `client_name` parameter name in `provision_client` function signature

- [ ] **Step 3: Update admin router**

Update variable names and response field names to match the renamed schemas.

- [ ] **Step 4: Commit**

```bash
git add app/modules/admin/
git commit -m "feat: rename Company→Client in admin module, set is_admin+permissions on provision"
```

---

### Task 9: Settings module update

**Files:**
- Modify: `backend/nexus/app/modules/settings/schemas.py`
- Modify: `backend/nexus/app/modules/settings/service.py`
- Modify: `backend/nexus/app/modules/settings/router.py`

- [ ] **Step 1: Update settings schemas**

Add to `TeamInviteRequest`:
```python
    is_admin: bool = False
    permissions: list[str] = []
    org_unit_id: str | None = None
```

Add to `TeamMember`:
```python
    is_admin: bool
    permissions: list[str]
```

- [ ] **Step 2: Update settings service**

- Replace `Company` with `Client` in imports and queries
- Update `create_team_invite` to accept and validate permission fields:
  - Add params: `inviting_user_permissions: list[str]`, `is_admin: bool`, `permissions: list[str] | None`, `org_unit_id: uuid_mod.UUID | None`
  - Call `require_permission(inviting_user_permissions, "users.invite_admins")` for admin invites or `"users.invite_users"` for regular
  - Call `validate_permissions(target_permissions, inviting_user_permissions)` for subset check
  - Add `is_admin`, `permissions`, `org_unit_id` to the `UserInvite` constructor
  - Update allowed roles to include `"Admin"`
- Update `list_team_members` to include `is_admin` and `permissions` in output dicts for both user rows and invite rows
- Update function return docstrings: `company_name` → `client_name`

- [ ] **Step 3: Update settings router**

- Change `require_roles("Company Admin")` to `require_roles("Company Admin", "Admin")` on the **invite endpoint ONLY**. Keep `deactivate` and `revoke` as `require_roles("Company Admin")`.
- In `invite_endpoint`: fetch inviting user's full record from DB, check `admin_user.is_admin`, pass `admin_user.permissions or []` to the service
- Pass new fields from `TeamInviteRequest` to `create_team_invite`

- [ ] **Step 4: Commit**

```bash
git add app/modules/settings/
git commit -m "feat: update settings module with permission validation + Admin role support"
```

---

### Task 10: Org units module

**Files:**
- Create: `backend/nexus/app/modules/org_units/__init__.py`
- Create: `backend/nexus/app/modules/org_units/schemas.py`
- Create: `backend/nexus/app/modules/org_units/service.py`
- Create: `backend/nexus/app/modules/org_units/router.py`

- [ ] **Step 1: Create the module**

Use the code from the user's spec as template:
- `schemas.py`: `CreateOrgUnitRequest`, `OrgUnitResponse`, `UpdateOrgUnitRequest`
- `service.py`: `create_org_unit`, `list_org_units`, `get_org_unit`, `update_org_unit`. All use `OrganizationalUnit` model. Validate `unit_type` against allowed set.
- `router.py`: POST/GET/PUT at `/api/org-units`. All use `get_tenant_db`, protected by `require_roles("Company Admin", "Admin")`

- [ ] **Step 2: Register router in main.py**

Add after existing router registrations:
```python
    from app.modules.org_units.router import router as org_units_router
    application.include_router(org_units_router)
```

- [ ] **Step 3: Commit**

```bash
git add app/modules/org_units/ app/main.py
git commit -m "feat: add org units module with CRUD endpoints"
```

---

### Task 11: Update tests

**Files:**
- Modify: `backend/nexus/tests/test_auth_endpoints.py`
- Modify: `backend/nexus/tests/test_settings.py`
- Create: `backend/nexus/tests/test_permissions.py`
- Create: `backend/nexus/tests/test_org_units.py`

- [ ] **Step 1: Add permission validation tests**

Create `backend/nexus/tests/test_permissions.py`:
```python
"""Tests for permission validation logic."""

import pytest
from app.modules.auth.permissions import (
    ALL_PERMISSIONS,
    SUPER_ADMIN_PERMISSIONS,
    validate_permissions,
    require_permission,
)


def test_super_admin_has_all_permissions():
    assert set(SUPER_ADMIN_PERMISSIONS) == ALL_PERMISSIONS


def test_validate_subset_passes():
    parent = ["jobs.create", "jobs.manage", "candidates.view"]
    child = ["jobs.create", "candidates.view"]
    validate_permissions(child, parent)  # should not raise


def test_validate_exceeds_parent_fails():
    parent = ["jobs.create"]
    child = ["jobs.create", "jobs.manage"]
    with pytest.raises(ValueError, match="exceed"):
        validate_permissions(child, parent)


def test_validate_unknown_permission_fails():
    with pytest.raises(ValueError, match="Unknown"):
        validate_permissions(["fake.permission"], list(ALL_PERMISSIONS))


def test_require_permission_passes():
    require_permission(["jobs.create", "jobs.manage"], "jobs.create")


def test_require_permission_fails():
    with pytest.raises(ValueError, match="do not have"):
        require_permission(["jobs.create"], "jobs.manage")
```

- [ ] **Step 2: Add org units auth test**

Create `backend/nexus/tests/test_org_units.py`:
```python
"""Tests for org units endpoints — auth guard tests."""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


class TestOrgUnits:
    @pytest.mark.asyncio
    async def test_create_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/org-units", json={"name": "Test", "unit_type": "team"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/org-units")
        assert resp.status_code == 401
```

- [ ] **Step 3: Run all tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests pass (existing + new permission + org_units tests).

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: add permission validation and org units auth tests"
```

---

### Task 12: Admin panel frontend rename

**Files:**
- Modify: `frontend/admin/app/(admin)/dashboard/page.tsx`
- Modify: `frontend/admin/app/(admin)/dashboard/provision/page.tsx`

- [ ] **Step 1: Update dashboard page**

In `frontend/admin/app/(admin)/dashboard/page.tsx`:
- Interface `Client`: rename `company_id` → `client_id`, `company_name` → `client_name`
- Table body: `c.company_id` → `c.client_id`, `c.company_name` → `c.client_name`
- Table header: "Company" label stays (it's display text, not a field name)

- [ ] **Step 2: Update provision page**

In `frontend/admin/app/(admin)/dashboard/provision/page.tsx`:
- State variable: `companyName` can stay (internal JS name)
- API payload: `company_name: companyName` → `client_name: companyName`
- Response type: `company_id` → `client_id`

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/admin/
git commit -m "feat: rename company_name→client_name in admin panel frontend"
```

---

### Task 13: Client dashboard frontend updates

**Files:**
- Modify: `frontend/app/app/(dashboard)/layout.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/team/page.tsx`

- [ ] **Step 1: Update dashboard layout**

In `frontend/app/app/(dashboard)/layout.tsx`, the `getMe` cache function return type uses `{ role: string; onboarding_complete: boolean }`. No rename needed here since it reads from the response dynamically. But if `company_name` is referenced anywhere, change to `client_name`.

- [ ] **Step 2: Rewrite team management page**

Replace `frontend/app/app/(dashboard)/settings/team/page.tsx` entirely. The new version adds:

1. **Fetch current user's permissions** on page load via `GET /api/auth/me` — store `me.permissions` and `me.is_admin`
2. **Fetch org units** via `GET /api/org-units` — populate dropdown
3. **"Invite as Admin" toggle** — only visible if `me.permissions.includes("users.invite_admins")`
4. **Permission multi-select** — shown when `inviteAsAdmin=true`. Checkboxes for each of the 16 permissions, filtered to `me.permissions` only
5. **Org unit dropdown** — populated from org units API. Optional.
6. **When `inviteAsAdmin=false`** — hide permissions, role selector shows Recruiter/HM/Interviewer/Observer. When `inviteAsAdmin=true` — role locked to "Admin"
7. **Member list**: add `is_admin` badge + permissions count column for both users and invites
8. **API payload** for invite: `{ email, role, is_admin, permissions, org_unit_id }`

The `TeamMember` interface adds `is_admin: boolean` and `permissions: string[]`.

The full page code should be approximately 350-400 lines. The implementer should use the existing page as a base and extend it with the new fields.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/
git commit -m "feat: update team page with permission selector, admin toggle, org unit dropdown"
```

---

### Task 14: End-to-end verification

- [ ] **Step 1: Run all backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 2: Verify frontend builds**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app && npm run build 2>&1 | tail -10
cd /home/ishant/Projects/ProjectX/frontend/admin && npm run build 2>&1 | tail -10
```

Expected: Both build successfully.

- [ ] **Step 3: Reset Supabase and restart**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase stop && supabase start
```

- [ ] **Step 4: Full Pipeline A+B test**

1. Set up admin user (signup + set is_projectx_admin flag)
2. Provision client "BinQle" with admin email
3. Claim invite as Company Admin → verify JWT has `is_admin: true`, `org_unit_id: null`
4. Complete onboarding
5. Create an org unit via POST /api/org-units
6. Invite an Admin user with `is_admin=true`, `permissions=["jobs.create", "jobs.manage"]`, `org_unit_id=<uuid>`
7. Claim invite as Admin → verify JWT has `is_admin: true`, `org_unit_id: <uuid>`
8. As Admin, try to invite with `permissions=["settings.client"]` → should fail (exceeds own permissions)
9. As Admin, invite a Recruiter (is_admin=false) → succeeds
10. Verify `GET /api/auth/me` returns correct `permissions[]` for all users
11. Verify admin panel still works (client list shows client_name)

---

## Acceptance Criteria

- [ ] `supabase db reset` applies all 5 migrations cleanly
- [ ] `public.companies` gone; `public.clients` exists
- [ ] `public.organizational_units` exists with RLS + auth_hook_read policy
- [ ] `public.users` has: parent_user_id, org_unit_id, permissions, is_admin
- [ ] `public.user_invites` has: is_admin, permissions, org_unit_id
- [ ] JWT for Company Admin: `is_admin: true`, `org_unit_id: null`
- [ ] JWT for Admin user: `is_admin: true`, `org_unit_id: <uuid>`
- [ ] JWT for Recruiter: `is_admin: false`
- [ ] `GET /api/auth/me` returns is_admin, permissions[], org_unit_id, client_name
- [ ] Org units CRUD works (POST, GET, PUT)
- [ ] Permission subset validation enforced on invite
- [ ] Exceeding own permissions returns 400
- [ ] Inviting admin without `users.invite_admins` returns 400
- [ ] `deactivate`/`revoke` NOT broadened to Admin role
- [ ] All backend tests pass
- [ ] Both frontends build cleanly
- [ ] Admin panel shows client_name (not company_name)
