# Phase 1 Hardening — Deactivation Fix, deletable_by Cleanup, Audit Log

**Date:** 2026-04-05
**Status:** Approved
**Scope:** Backend only (`backend/nexus/`)

---

## Overview

Three tasks that harden the Phase 1 foundation before Phase 2 feature work begins:

1. **Fix user deactivation** — Move Supabase auth account deletion out of the DB transaction into a background task. DB-level `is_active = False` is the security boundary; Supabase deletion is best-effort cleanup.
2. **Fix `deletable_by` stale references** — Nullify `deletable_by` on org units when the referenced user is deactivated.
3. **Create audit log** — Append-only audit trail table, ORM model, logging helper, canonical action constants, and wiring into all existing Phase 1 mutation paths.

Plus a cross-cutting deliverable: **integration test infrastructure** with per-test transaction rollback.

---

## Implementation Order

```
Test Infrastructure → Task 2 → Task 1 → Task 3
```

- Task 1 calls `nullify_deletable_by_for_user` from Task 2
- Task 3 wires audit calls into service functions modified by Tasks 1 and 2

---

## Test Infrastructure

### Database

Use the existing PostgreSQL instance from docker-compose. A separate test database (`projectx_test`), not the dev database.

**Environment variable:** `TEST_DATABASE_URL` in `.env.example`:
```
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/projectx_test
```

### Table Creation

`Base.metadata.create_all` at test session start via a session-scoped fixture. No Supabase migrations. No RLS policies. Tables created once, not per test.

### Per-Test Rollback

Connection-level transaction rollback pattern (handles SQLAlchemy async session commit gotcha):

```python
@pytest_asyncio.fixture
async def db():
    async with test_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await conn.rollback()
            await session.close()
```

The connection holds the real transaction. The session's own commit/flush calls operate within that connection's transaction boundary and are all undone by the connection-level rollback.

### Factory Helpers

Exactly three. Sensible defaults for all required fields. Tests only specify what's relevant.

```python
async def create_test_client(db: AsyncSession, **kwargs) -> Client
async def create_test_user(db: AsyncSession, client_id: UUID, **kwargs) -> User
async def create_test_org_unit(db: AsyncSession, client_id: UUID, **kwargs) -> OrganizationalUnit
```

No fixtures for invites, roles, or role assignments. Add when a test needs them.

### Test Approach

Service functions called directly with the `db` session. No HTTP layer, no FastAPI dependency injection. Testing service-layer correctness, not HTTP behavior.

---

## Task 2 — Fix `deletable_by` Not Being Nullified on User Deactivation

### Problem

When a user is deactivated, `deactivate_team_user` sets `user.is_active = False` and revokes invites, but does NOT nullify `deletable_by` references on `organizational_units`. A deactivated user's UUID persists in `deletable_by`, causing stale error messages in `delete_org_unit`.

### Changes

**`backend/nexus/app/modules/org_units/service.py`** — New function:

```python
async def nullify_deletable_by_for_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
) -> int:
    """
    Set deletable_by = NULL on all org units in this tenant where
    deletable_by == user_id. Returns count of units updated.
    """
```

Single UPDATE query. Returns affected row count for logging.

**`backend/nexus/app/modules/settings/service.py`** — Call inside `deactivate_team_user`:

```python
from app.modules.org_units.service import nullify_deletable_by_for_user

# Inside deactivate_team_user, after setting is_active = False:
units_updated = await nullify_deletable_by_for_user(db, tenant_id, user_id)
log.info("settings.deletable_by_nullified_on_deactivation", user_id=str(user_id), units_updated=units_updated)
```

### Tests

- Deactivating a user nullifies their `deletable_by` references in the same tenant.
- Org units in other tenants are NOT affected.
- Returns correct count.

---

## Task 1 — Fix User Deactivation: Move Supabase Auth Deletion Outside DB Transaction

### Problem

`deactivate_team_user` calls `_delete_auth_user()` (HTTP to Supabase Admin API) inside the SQLAlchemy transaction. Two failure modes:

- **Mode A:** Supabase API down → exception → transaction rolls back → user NOT deactivated. External API failure blocks a security action.
- **Mode B:** Supabase deletion succeeds → DB commit fails → auth account deleted but `is_active` still True. Inconsistent state for up to 1 hour (JWT lifetime).

### Why `is_active = False` Is Sufficient

`get_current_user_roles` in `auth/context.py` already filters `.where(User.is_active == True)`. Setting `is_active = False` immediately blocks the deactivated user from all protected endpoints, even with a valid JWT. Supabase auth deletion is cleanup, not security.

### Changes

**`backend/nexus/app/modules/settings/service.py`:**

1. Remove `await _delete_auth_user(...)` from `deactivate_team_user`.
2. `deactivate_team_user` returns `str` — the `auth_user_id` of the deactivated user.
3. `_delete_auth_user` remains as a standalone async function (unchanged).

**`backend/nexus/app/modules/settings/router.py`:**

4. Add `BackgroundTasks` parameter to `deactivate_endpoint` (same pattern as `invite_endpoint`).
5. Create wrapper function for background execution:

```python
async def _background_delete_auth_user(auth_user_id: str) -> None:
    try:
        await _delete_auth_user(auth_user_id)
    except Exception as exc:
        log.error("settings.supabase_deletion_failed", auth_user_id=auth_user_id, error=str(exc))
```

6. After `deactivate_team_user` returns (DB transaction committed), schedule:
```python
background_tasks.add_task(_background_delete_auth_user, auth_user_id)
```

### Tests

- Deactivation succeeds (`is_active = False`) regardless of `_delete_auth_user` behavior.
- `auth_user_id` returned is correct.

---

## Task 3 — Audit Log Table and Logging Helper

### Part A — Supabase Migration

New file: `backend/supabase/migrations/20260405000001_audit_log.sql`

```sql
CREATE TABLE public.audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES public.clients(id),
    actor_id     UUID REFERENCES public.users(id),
    actor_email  TEXT,
    action       TEXT NOT NULL,
    resource     TEXT NOT NULL,
    resource_id  UUID,
    payload      JSONB,
    ip_address   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_tenant_id_idx ON public.audit_log (tenant_id);
CREATE INDEX audit_log_tenant_action_idx ON public.audit_log (tenant_id, action);
CREATE INDEX audit_log_tenant_created_at_idx ON public.audit_log (tenant_id, created_at DESC);
CREATE INDEX audit_log_resource_idx ON public.audit_log (tenant_id, resource, resource_id);

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.audit_log
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.audit_log
    USING (current_setting('app.bypass_rls', true) = 'true');

COMMENT ON TABLE public.audit_log IS
'Append-only audit trail for all tenant-scoped mutations.
Never update or delete rows. actor_id may be NULL for
system-initiated actions. payload contains before/after state
or relevant context, schema varies per action string.';
```

INSERT-only from application layer. No UPDATE or DELETE policies.

### Part B — SQLAlchemy ORM Model

Add `AuditLog` to `backend/nexus/app/models.py`. Plain data class matching existing model conventions. Maps every column from the DDL.

### Part C — Audit Helper

New file: `backend/nexus/app/modules/audit/service.py`

```python
async def log_event(
    db: AsyncSession,
    *,
    tenant_id: uuid_mod.UUID,
    actor_id: uuid_mod.UUID | None,
    actor_email: str | None,
    action: str,
    resource: str,
    resource_id: uuid_mod.UUID | None = None,
    payload: dict | None = None,
    ip_address: str | None = None,
) -> None:
```

Rules:
- Uses caller's `db` session (same transaction — audit event commits atomically with the mutation).
- Wraps INSERT in try/except. On failure: `log.error("audit.log_event_failed", ...)`. Never re-raises.
- Uses `AuditLog` ORM model, not raw SQL.

### Part D — Action Constants

New file: `backend/nexus/app/modules/audit/actions.py`

Plain string constants, dot-notation namespaced by resource:

```python
# User actions
USER_INVITED = "user.invited"
USER_INVITE_RESENT = "user.invite_resent"
USER_INVITE_REVOKED = "user.invite_revoked"
USER_INVITE_CLAIMED = "user.invite_claimed"
USER_DEACTIVATED = "user.deactivated"

# Org unit actions
ORG_UNIT_CREATED = "org_unit.created"
ORG_UNIT_UPDATED = "org_unit.updated"
ORG_UNIT_DELETED = "org_unit.deleted"
ORG_UNIT_MEMBER_ADDED = "org_unit.member_added"
ORG_UNIT_MEMBER_REMOVED = "org_unit.member_removed"
ORG_UNIT_ROLE_REMOVED = "org_unit.role_removed"

# Client actions
CLIENT_PROVISIONED = "client.provisioned"
CLIENT_ONBOARDING_COMPLETED = "client.onboarding_completed"
```

### Part E — Wire Into Existing Services

Every Phase 1 mutation gets a `log_event` call. Actor info handling:

**Functions that already have actor info:**
- `create_team_invite` has `invited_by` → use as `actor_id`, add `actor_email` kwarg
- `create_org_unit` has `created_by` → use as `actor_id`, add `actor_email` kwarg
- `assign_role` has `assigned_by` → use as `actor_id`, add `actor_email` kwarg
- `delete_org_unit` has `caller_user_id` → use as `actor_id`, add `actor_email` kwarg
- `deactivate_team_user` has `caller_auth_user_id` → add `actor_id` and `actor_email` kwargs
- `provision_client` has `admin_identity` (email) → use as `actor_email`, `actor_id = None` (ProjectX admins don't have `public.users` rows — they authenticate via `is_projectx_admin` JWT claim only, so `actor_id` is legitimately NULL; the `actor_email` from the JWT is the identifying field)

**Functions that need actor info added:**
- `resend_team_invite` → add `actor_id: uuid_mod.UUID | None = None`, `actor_email: str | None = None`
- `revoke_team_invite` → same
- `update_org_unit` → same
- `remove_user_from_unit` → same
- `remove_role_from_user` → same

**Router callers** pass `ctx.user.id` as `actor_id` and `ctx.user.email` as `actor_email`.

**`ip_address`:** Routers that have `request: Request` pass `request.client.host`. Routers that don't: add `request: Request` parameter (standard FastAPI parameter, no cost). Service functions receive `ip_address: str | None` — request objects never enter the service layer.

**Wiring map:**

| Service Function | Action | Resource | resource_id | Payload |
|---|---|---|---|---|
| `create_team_invite` | `user.invited` | `user_invite` | invite.id | `{"invited_email": email}` |
| `resend_team_invite` | `user.invite_resent` | `user_invite` | new_invite.id | `{"invited_email": email, "superseded_invite_id": str(old_id)}` |
| `revoke_team_invite` | `user.invite_revoked` | `user_invite` | invite.id | `{"invited_email": invite.email}` |
| `deactivate_team_user` | `user.deactivated` | `user` | user.id | `{"deactivated_email": user.email, "auth_user_id": auth_user_id}` |
| `complete_invite` (router) | `user.invite_claimed` | `user` | new_user.id | `{"email": user.email, "is_super_admin": bool}` |
| `create_org_unit` | `org_unit.created` | `org_unit` | unit.id | `{"name": name, "unit_type": unit_type, "parent_unit_id": str(...) or None}` |
| `update_org_unit` | `org_unit.updated` | `org_unit` | unit.id | `{"changed": {"field": {"from": old, "to": new}}}` — diff of only mutated fields (see note below) |
| `delete_org_unit` | `org_unit.deleted` | `org_unit` | unit.id | `{"name": unit.name}` |
| `assign_role` | `org_unit.member_added` | `org_unit` | org_unit_id | `{"user_id": str(user_id), "role_id": str(role_id)}` |
| `remove_user_from_unit` | `org_unit.member_removed` | `org_unit` | org_unit_id | `{"user_id": str(user_id), "roles_removed": count}` |
| `remove_role_from_user` | `org_unit.role_removed` | `org_unit` | org_unit_id | `{"user_id": str(user_id), "role_id": str(role_id)}` |
| `provision_client` | `client.provisioned` | `client` | client.id | `{"client_name": name, "admin_email": email, "plan": plan}` |
| `complete_onboarding` (router) | `client.onboarding_completed` | `client` | client.id | `{}` |

**`update_org_unit` payload format:** The audit payload must be a diff of only the fields that were actually mutated. Structure: `{"changed": {"field_name": {"from": old_value, "to": new_value}}}`. Only include fields where the value changed. Do not include unchanged fields. Capture before-values before applying the update, after-values after.

**`complete_invite` audit call placement:** The `USER_INVITE_CLAIMED` audit call lives in `auth/router.py` (the `complete_invite` handler), not in a service function. This is a pragmatic exception — `complete_invite` has no backing service function; its logic is inline SQL in the router. All other audit calls are in service functions. Add a `# TODO: refactor complete_invite logic into auth/service.py so audit call moves to service layer` comment at the call site. Do not refactor now — out of scope.

### Part F — Tests

In `backend/nexus/tests/test_audit.py`:
- `log_event` inserts a row with correct field values.
- `log_event` does NOT raise if the INSERT fails (mock the session to raise).
- `action` and `resource` populated correctly for two different action types.

---

## Files Modified

| File | Changes |
|---|---|
| `backend/nexus/app/modules/org_units/service.py` | Add `nullify_deletable_by_for_user()` |
| `backend/nexus/app/modules/settings/service.py` | Remove `_delete_auth_user` from `deactivate_team_user`, return `auth_user_id`, call `nullify_deletable_by_for_user`, add audit calls to all mutations, add `actor_id`/`actor_email` params where missing |
| `backend/nexus/app/modules/settings/router.py` | Add `BackgroundTasks` to deactivate, add `_background_delete_auth_user` wrapper, import `_delete_auth_user`, add `request: Request` where missing |
| `backend/nexus/app/modules/auth/router.py` | Add audit calls to `complete_invite` and `complete_onboarding` |
| `backend/nexus/app/modules/admin/service.py` | Add audit call to `provision_client`, add `actor_id`/`actor_email` params |
| `backend/nexus/app/modules/org_units/router.py` | Pass `actor_id`, `actor_email`, `ip_address` to all service calls |
| `backend/nexus/app/models.py` | Add `AuditLog` model |
| `backend/nexus/.env.example` | Add `TEST_DATABASE_URL` |
| `backend/nexus/tests/conftest.py` | Rewrite: test engine, session-scoped create_all, per-test rollback fixture, 3 factory helpers |

## Files Created

| File | Purpose |
|---|---|
| `backend/supabase/migrations/20260405000001_audit_log.sql` | Audit log DDL + RLS + indexes |
| `backend/nexus/app/modules/audit/__init__.py` | Module init |
| `backend/nexus/app/modules/audit/service.py` | `log_event()` helper |
| `backend/nexus/app/modules/audit/actions.py` | Canonical action string constants |
| `backend/nexus/tests/test_deactivation.py` | Tests for Tasks 1 and 2 |
| `backend/nexus/tests/test_audit.py` | Tests for Task 3 |

---

## General Rules

- Follow existing code style, import ordering, structlog patterns.
- `tenant_id` on most tables; `client_id` on `organizational_units`. Check `models.py` before writing queries.
- Never call `db.commit()` in service functions. Session dependency handles commit.
- All queries async (`await db.execute(...)`).
- Run `ruff check .` and `ruff format .` before done.
- Update `docs/phase-1-implementation.md` to reflect all three changes.
