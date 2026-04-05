# Phase 1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the deactivation transaction bug, nullify stale `deletable_by` references, and add an append-only audit log wired into every Phase 1 mutation.

**Architecture:** Four sequential deliverables — test infrastructure, `deletable_by` nullification, deactivation fix, audit log — each building on the previous. Service-layer changes only; no new HTTP endpoints. Audit logging is fire-and-forget (never breaks a business operation).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async (asyncpg), pytest-asyncio, structlog

**Spec:** `docs/superpowers/specs/2026-04-05-phase1-hardening-design.md`

---

## File Map

### Files to Create

| File | Responsibility |
|---|---|
| `backend/supabase/migrations/20260405000001_audit_log.sql` | Audit log DDL, indexes, RLS policies |
| `backend/nexus/app/modules/audit/__init__.py` | Module init (empty) |
| `backend/nexus/app/modules/audit/service.py` | `log_event()` — single audit INSERT helper |
| `backend/nexus/app/modules/audit/actions.py` | Canonical action string constants |
| `backend/nexus/tests/test_deactivation.py` | Tests for Tasks 1 + 2 |
| `backend/nexus/tests/test_audit.py` | Tests for Task 3 |

### Files to Modify

| File | What Changes |
|---|---|
| `backend/nexus/.env.example` | Add `TEST_DATABASE_URL` |
| `backend/nexus/tests/conftest.py` | Rewrite: test engine, create_all, per-test rollback, 3 factory helpers |
| `backend/nexus/app/models.py` | Add `AuditLog` ORM model |
| `backend/nexus/app/modules/org_units/service.py` | Add `nullify_deletable_by_for_user()` |
| `backend/nexus/app/modules/settings/service.py` | Fix deactivation, call nullify, add audit calls, add actor params |
| `backend/nexus/app/modules/settings/router.py` | BackgroundTasks for auth deletion, add `request: Request` where missing, pass actor/ip to services |
| `backend/nexus/app/modules/org_units/router.py` | Add `request: Request` where missing, pass actor/ip to services |
| `backend/nexus/app/modules/auth/router.py` | Add audit calls to `complete_invite` and `complete_onboarding` |
| `backend/nexus/app/modules/admin/service.py` | Add audit call to `provision_client` |
| `backend/nexus/docs/phase-1-implementation.md` | Document all three changes |

---

### Task 1: Test Infrastructure

**Files:**
- Modify: `backend/nexus/.env.example`
- Modify: `backend/nexus/tests/conftest.py`

- [ ] **Step 1: Add TEST_DATABASE_URL to .env.example**

In `backend/nexus/.env.example`, add after the `DATABASE_URL` line:

```
# Test database (separate DB, same PostgreSQL instance)
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/projectx_test
```

- [ ] **Step 2: Create the test database**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose exec -T $(docker compose ps --format '{{.Name}}' | grep supabase-db || echo "supabase_db_nexus") psql -U postgres -c "CREATE DATABASE projectx_test;" 2>/dev/null || echo "DB may already exist"
```

If docker compose doesn't have a direct postgres service, use the Supabase local postgres:
```bash
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -c "CREATE DATABASE projectx_test;" 2>/dev/null || echo "DB exists"
```

- [ ] **Step 3: Rewrite conftest.py**

Replace the entire contents of `backend/nexus/tests/conftest.py` with:

```python
"""Test fixtures — integration tests against a real PostgreSQL database.

Uses connection-level transaction rollback so each test is fully isolated
without needing to truncate tables.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import Base
from app.main import app
from app.models import Client, OrganizationalUnit, User

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/projectx_test",
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy for the whole test session."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _create_tables():
    """Create all tables once at test session start."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db(_create_tables: None):
    """Per-test database session with automatic rollback.

    The session is bound to a connection-level transaction.
    Everything the test does — including flushes and internal commits
    by service functions — is rolled back after the test.
    """
    async with test_engine.connect() as conn:
        txn = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await txn.rollback()


@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP test client for FastAPI (kept for existing tests)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Factory helpers — sensible defaults, tests override only what matters
# ---------------------------------------------------------------------------

_counter = 0


def _next_id() -> int:
    global _counter
    _counter += 1
    return _counter


async def create_test_client(db: AsyncSession, **kwargs) -> Client:
    """Create a Client row with sensible defaults."""
    n = _next_id()
    defaults = {
        "name": f"Test Company {n}",
        "domain": f"test{n}.com",
        "industry": "Technology",
        "plan": "trial",
        "onboarding_complete": False,
    }
    defaults.update(kwargs)
    client = Client(**defaults)
    db.add(client)
    await db.flush()
    return client


async def create_test_user(db: AsyncSession, client_id: uuid.UUID, **kwargs) -> User:
    """Create a User row with sensible defaults."""
    n = _next_id()
    now = datetime.now(timezone.utc)
    defaults = {
        "auth_user_id": uuid.uuid4(),
        "tenant_id": client_id,
        "email": f"user{n}@test.com",
        "full_name": f"Test User {n}",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    await db.flush()
    return user


async def create_test_org_unit(db: AsyncSession, client_id: uuid.UUID, **kwargs) -> OrganizationalUnit:
    """Create an OrganizationalUnit row with sensible defaults."""
    n = _next_id()
    now = datetime.now(timezone.utc)
    defaults = {
        "client_id": client_id,
        "name": f"Test Unit {n}",
        "unit_type": "department",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    unit = OrganizationalUnit(**defaults)
    db.add(unit)
    await db.flush()
    return unit
```

- [ ] **Step 4: Verify test infrastructure works**

Create a minimal smoke test to validate the infrastructure. Create `backend/nexus/tests/test_smoke.py`:

```python
"""Smoke test — validates test infrastructure works."""

import pytest

from tests.conftest import create_test_client, create_test_org_unit, create_test_user


@pytest.mark.asyncio
async def test_factory_helpers_create_rows(db):
    client = await create_test_client(db, name="Acme Corp")
    user = await create_test_user(db, client.id, email="alice@acme.com")
    unit = await create_test_org_unit(db, client.id, name="Engineering")

    assert client.id is not None
    assert user.tenant_id == client.id
    assert unit.client_id == client.id
    assert user.email == "alice@acme.com"
    assert unit.name == "Engineering"
```

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_smoke.py -v
```

Expected: 1 test PASSES.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add .env.example tests/conftest.py tests/test_smoke.py
git commit -m "test: add integration test infrastructure with per-test rollback"
```

---

### Task 2: nullify_deletable_by_for_user

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`
- Create: `backend/nexus/tests/test_deactivation.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/test_deactivation.py`:

```python
"""Tests for user deactivation — deletable_by nullification and auth deletion decoupling."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


@pytest.mark.asyncio
async def test_nullify_deletable_by_for_user_clears_references(db: AsyncSession):
    """Deactivating a user should nullify their deletable_by on all org units in the same tenant."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    unit1 = await create_test_org_unit(db, client.id, deletable_by=user.id)
    unit2 = await create_test_org_unit(db, client.id, deletable_by=user.id)
    unit3 = await create_test_org_unit(db, client.id, deletable_by=None)  # not affected

    count = await nullify_deletable_by_for_user(db, client.id, user.id)

    assert count == 2

    await db.flush()
    for uid in [unit1.id, unit2.id, unit3.id]:
        result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == uid))
        unit = result.scalar_one()
        assert unit.deletable_by is None


@pytest.mark.asyncio
async def test_nullify_deletable_by_does_not_affect_other_tenants(db: AsyncSession):
    """Nullification must be tenant-scoped — other tenants' units are untouched."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

    client_a = await create_test_client(db, name="Tenant A")
    client_b = await create_test_client(db, name="Tenant B")

    user_a = await create_test_user(db, client_a.id)

    # Same user UUID used as deletable_by in a different tenant (unlikely but tests isolation)
    unit_a = await create_test_org_unit(db, client_a.id, deletable_by=user_a.id)
    unit_b = await create_test_org_unit(db, client_b.id, deletable_by=user_a.id)

    count = await nullify_deletable_by_for_user(db, client_a.id, user_a.id)

    assert count == 1

    result_a = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == unit_a.id))
    assert result_a.scalar_one().deletable_by is None

    result_b = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == unit_b.id))
    assert result_b.scalar_one().deletable_by == user_a.id


@pytest.mark.asyncio
async def test_nullify_deletable_by_returns_zero_when_no_matches(db: AsyncSession):
    """Returns 0 when user has no deletable_by references."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

    client = await create_test_client(db)
    user = await create_test_user(db, client.id)
    await create_test_org_unit(db, client.id, deletable_by=None)

    count = await nullify_deletable_by_for_user(db, client.id, user.id)
    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_deactivation.py -v
```

Expected: 3 tests FAIL with `ImportError` (function doesn't exist yet).

- [ ] **Step 3: Implement nullify_deletable_by_for_user**

Add to the end of `backend/nexus/app/modules/org_units/service.py` (before any private helpers if you prefer, but after `remove_role_from_user` is fine):

```python
async def nullify_deletable_by_for_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
) -> int:
    """Set deletable_by = NULL on all org units in this tenant where deletable_by == user_id.

    Used when a user is deactivated. Returns the count of units updated.
    """
    from sqlalchemy import update

    result = await db.execute(
        update(OrganizationalUnit)
        .where(
            OrganizationalUnit.client_id == tenant_id,
            OrganizationalUnit.deletable_by == user_id,
        )
        .values(deletable_by=None)
    )
    return result.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_deactivation.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/org_units/service.py tests/test_deactivation.py
git commit -m "feat: add nullify_deletable_by_for_user for deactivation cleanup"
```

---

### Task 3: Fix Deactivation — Move Auth Deletion to BackgroundTasks

**Files:**
- Modify: `backend/nexus/app/modules/settings/service.py`
- Modify: `backend/nexus/app/modules/settings/router.py`
- Modify: `backend/nexus/tests/test_deactivation.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/test_deactivation.py`:

```python
@pytest.mark.asyncio
async def test_deactivate_team_user_sets_inactive_and_returns_auth_id(db: AsyncSession):
    """deactivate_team_user sets is_active=False and returns auth_user_id."""
    from app.modules.settings.service import deactivate_team_user

    client = await create_test_client(db)
    caller = await create_test_user(db, client.id, email="admin@test.com")
    target = await create_test_user(db, client.id, email="target@test.com")

    auth_user_id = await deactivate_team_user(
        db, client.id, target.id, str(caller.auth_user_id),
    )

    assert auth_user_id == str(target.auth_user_id)

    from sqlalchemy import select
    from app.models import User
    result = await db.execute(select(User).where(User.id == target.id))
    user = result.scalar_one()
    assert user.is_active is False


@pytest.mark.asyncio
async def test_deactivate_team_user_nullifies_deletable_by(db: AsyncSession):
    """deactivate_team_user also nullifies deletable_by references."""
    from app.modules.settings.service import deactivate_team_user

    client = await create_test_client(db)
    caller = await create_test_user(db, client.id, email="admin@test.com")
    target = await create_test_user(db, client.id, email="target@test.com")

    unit = await create_test_org_unit(db, client.id, deletable_by=target.id)

    await deactivate_team_user(db, client.id, target.id, str(caller.auth_user_id))

    result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == unit.id))
    assert result.scalar_one().deletable_by is None


@pytest.mark.asyncio
async def test_deactivate_self_raises(db: AsyncSession):
    """Cannot deactivate your own account."""
    from app.modules.settings.service import deactivate_team_user

    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    with pytest.raises(ValueError, match="Cannot deactivate your own account"):
        await deactivate_team_user(db, client.id, user.id, str(user.auth_user_id))
```

- [ ] **Step 2: Run tests to verify new tests fail**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_deactivation.py::test_deactivate_team_user_sets_inactive_and_returns_auth_id tests/test_deactivation.py::test_deactivate_team_user_nullifies_deletable_by -v
```

Expected: FAIL — `deactivate_team_user` currently returns `None`, not a string, and doesn't call `nullify_deletable_by_for_user`.

- [ ] **Step 3: Update deactivate_team_user in settings/service.py**

Replace the `deactivate_team_user` function in `backend/nexus/app/modules/settings/service.py`:

```python
async def deactivate_team_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    caller_auth_user_id: str,
) -> str:
    """Deactivate a user. Returns auth_user_id for background Supabase cleanup."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

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

    # Nullify deletable_by references
    units_updated = await nullify_deletable_by_for_user(db, tenant_id, user_id)
    if units_updated > 0:
        logger.info(
            "settings.deletable_by_nullified_on_deactivation",
            user_id=str(user_id),
            units_updated=units_updated,
        )

    logger.info("settings.user_deactivated", user_id=str(user_id), email=user.email)

    return str(user.auth_user_id)
```

- [ ] **Step 4: Update deactivate_endpoint in settings/router.py**

Replace the `deactivate_endpoint` function in `backend/nexus/app/modules/settings/router.py`:

```python
async def _background_delete_auth_user(auth_user_id: str) -> None:
    """Background task wrapper — logs errors, never re-raises."""
    try:
        await _delete_auth_user(auth_user_id)
    except Exception as exc:
        logger.error(
            "settings.supabase_deletion_failed",
            auth_user_id=auth_user_id,
            error=str(exc),
        )


@router.post(
    "/deactivate/{user_id}",
    dependencies=[require_super_admin()],
)
async def deactivate_endpoint(
    user_id: str,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Deactivate a user. Super admin only.

    DB deactivation happens in-transaction. Supabase auth account
    deletion is scheduled as a background task after commit.
    """
    try:
        auth_user_id = await deactivate_team_user(
            db, ctx.user.tenant_id, uuid_mod.UUID(user_id), str(ctx.user.auth_user_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(_background_delete_auth_user, auth_user_id)

    return {"status": "deactivated"}
```

Also add the import for `_delete_auth_user` to the router imports:

```python
from app.modules.settings.service import (
    _delete_auth_user,
    create_team_invite,
    deactivate_team_user,
    list_team_members,
    resend_team_invite,
    revoke_team_invite,
)
```

- [ ] **Step 5: Run all deactivation tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_deactivation.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/settings/service.py app/modules/settings/router.py tests/test_deactivation.py
git commit -m "fix: move Supabase auth deletion to background task, wire deletable_by nullification"
```

---

### Task 4: Audit Log — Migration, Model, Helper, Actions

**Files:**
- Create: `backend/supabase/migrations/20260405000001_audit_log.sql`
- Modify: `backend/nexus/app/models.py`
- Create: `backend/nexus/app/modules/audit/__init__.py`
- Create: `backend/nexus/app/modules/audit/service.py`
- Create: `backend/nexus/app/modules/audit/actions.py`
- Create: `backend/nexus/tests/test_audit.py`

- [ ] **Step 1: Create the Supabase migration**

Create `backend/supabase/migrations/20260405000001_audit_log.sql`:

```sql
-- Audit log — append-only trail for all tenant-scoped mutations.

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

- [ ] **Step 2: Add the AuditLog ORM model**

Add to the end of `backend/nexus/app/models.py`:

```python
class AuditLog(Base):
    """Append-only audit trail for tenant-scoped mutations."""
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    actor_email: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 3: Create the audit module**

Create `backend/nexus/app/modules/audit/__init__.py` (empty file).

Create `backend/nexus/app/modules/audit/actions.py`:

```python
"""Canonical audit action string constants.

Convention: resource.verb — lowercase, dot-separated.
All constants are plain strings (not an enum).
"""

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

Create `backend/nexus/app/modules/audit/service.py`:

```python
"""Audit log helper — single INSERT, never raises."""

import uuid as uuid_mod

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

logger = structlog.get_logger()


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
    """Append one audit event. Always call within an existing transaction.

    This function does NOT commit. If it fails, it logs the error and
    returns silently. Audit logging must never break a business operation.
    """
    try:
        entry = AuditLog(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            resource=resource,
            resource_id=resource_id,
            payload=payload,
            ip_address=ip_address,
        )
        db.add(entry)
        await db.flush()
    except Exception as exc:
        logger.error(
            "audit.log_event_failed",
            action=action,
            resource=resource,
            error=str(exc),
        )
```

- [ ] **Step 4: Write audit tests**

Create `backend/nexus/tests/test_audit.py`:

```python
"""Tests for audit log helper."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.modules.audit.actions import ORG_UNIT_CREATED, USER_INVITED
from app.modules.audit.service import log_event
from tests.conftest import create_test_client, create_test_user


@pytest.mark.asyncio
async def test_log_event_inserts_row_with_correct_fields(db: AsyncSession):
    """log_event should insert an AuditLog row with all provided fields."""
    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    resource_id = uuid.uuid4()
    await log_event(
        db,
        tenant_id=client.id,
        actor_id=user.id,
        actor_email=user.email,
        action=USER_INVITED,
        resource="user_invite",
        resource_id=resource_id,
        payload={"invited_email": "new@test.com"},
        ip_address="127.0.0.1",
    )

    result = await db.execute(
        select(AuditLog).where(AuditLog.tenant_id == client.id)
    )
    row = result.scalar_one()

    assert row.actor_id == user.id
    assert row.actor_email == user.email
    assert row.action == "user.invited"
    assert row.resource == "user_invite"
    assert row.resource_id == resource_id
    assert row.payload == {"invited_email": "new@test.com"}
    assert row.ip_address == "127.0.0.1"


@pytest.mark.asyncio
async def test_log_event_does_not_raise_on_failure(db: AsyncSession):
    """log_event must swallow exceptions and log them, never re-raise."""
    client = await create_test_client(db)

    # Pass an invalid tenant_id (references a non-existent client) to trigger FK violation
    fake_tenant = uuid.uuid4()

    # This should NOT raise — it should log the error and return silently
    await log_event(
        db,
        tenant_id=fake_tenant,
        actor_id=None,
        actor_email=None,
        action=USER_INVITED,
        resource="user_invite",
    )

    # Verify no row was inserted for the invalid tenant
    result = await db.execute(
        select(AuditLog).where(AuditLog.tenant_id == fake_tenant)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_log_event_action_and_resource_correct_for_multiple_types(db: AsyncSession):
    """Verify action and resource strings for two different action types."""
    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=user.id,
        actor_email=user.email,
        action=USER_INVITED,
        resource="user_invite",
    )

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=user.id,
        actor_email=user.email,
        action=ORG_UNIT_CREATED,
        resource="org_unit",
    )

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == client.id)
        .order_by(AuditLog.created_at.asc())
    )
    rows = result.scalars().all()
    assert len(rows) == 2
    assert rows[0].action == "user.invited"
    assert rows[0].resource == "user_invite"
    assert rows[1].action == "org_unit.created"
    assert rows[1].resource == "org_unit"
```

- [ ] **Step 5: Run audit tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_audit.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add ../supabase/migrations/20260405000001_audit_log.sql app/models.py app/modules/audit/ tests/test_audit.py
git commit -m "feat: add audit_log table, ORM model, log_event helper, and action constants"
```

---

### Task 5: Wire Audit Calls Into settings/service.py

**Files:**
- Modify: `backend/nexus/app/modules/settings/service.py`
- Modify: `backend/nexus/app/modules/settings/router.py`

- [ ] **Step 1: Add audit calls and actor params to settings/service.py**

Replace the full contents of `backend/nexus/app/modules/settings/service.py` with:

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
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event

logger = structlog.get_logger()


async def create_team_invite(
    *,
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    email: str,
    invited_by: uuid_mod.UUID,
    actor_email: str | None = None,
    ip_address: str | None = None,
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

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=invited_by,
        actor_email=actor_email,
        action=audit_actions.USER_INVITED,
        resource="user_invite",
        resource_id=invite.id,
        payload={"invited_email": email},
        ip_address=ip_address,
    )

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
    *,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
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

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.USER_INVITE_RESENT,
        resource="user_invite",
        resource_id=new_invite.id,
        payload={"invited_email": existing.email, "superseded_invite_id": str(invite_id)},
        ip_address=ip_address,
    )

    logger.info("settings.invite_resent", invite_id=str(new_invite.id), email=new_invite.email)

    return new_invite, raw_token, company.name


async def revoke_team_invite(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    invite_id: uuid_mod.UUID,
    *,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
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

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.USER_INVITE_REVOKED,
        resource="user_invite",
        resource_id=invite.id,
        payload={"invited_email": invite.email},
        ip_address=ip_address,
    )

    logger.info("settings.invite_revoked", invite_id=str(invite_id))


async def deactivate_team_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    caller_auth_user_id: str,
    *,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> str:
    """Deactivate a user. Returns auth_user_id for background Supabase cleanup."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

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

    # Nullify deletable_by references
    units_updated = await nullify_deletable_by_for_user(db, tenant_id, user_id)
    if units_updated > 0:
        logger.info(
            "settings.deletable_by_nullified_on_deactivation",
            user_id=str(user_id),
            units_updated=units_updated,
        )

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.USER_DEACTIVATED,
        resource="user",
        resource_id=user.id,
        payload={"deactivated_email": user.email, "auth_user_id": str(user.auth_user_id)},
        ip_address=ip_address,
    )

    logger.info("settings.user_deactivated", user_id=str(user_id), email=user.email)

    return str(user.auth_user_id)


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

- [ ] **Step 2: Update settings/router.py to pass actor info**

Replace the full contents of `backend/nexus/app/modules/settings/router.py` with:

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
    _delete_auth_user,
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


async def _background_delete_auth_user(auth_user_id: str) -> None:
    """Background task wrapper — logs errors, never re-raises."""
    try:
        await _delete_auth_user(auth_user_id)
    except Exception as exc:
        logger.error(
            "settings.supabase_deletion_failed",
            auth_user_id=auth_user_id,
            error=str(exc),
        )


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
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
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
    request: Request,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResendInviteResponse:
    """Resend an invite. Super admin only."""
    try:
        new_invite, raw_token, company_name = await resend_team_invite(
            db, ctx.user.tenant_id, uuid_mod.UUID(invite_id),
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
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
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Revoke a pending invite. Super admin only."""
    try:
        await revoke_team_invite(
            db, ctx.user.tenant_id, uuid_mod.UUID(invite_id),
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "revoked"}


@router.post(
    "/deactivate/{user_id}",
    dependencies=[require_super_admin()],
)
async def deactivate_endpoint(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Deactivate a user. Super admin only.

    DB deactivation happens in-transaction. Supabase auth account
    deletion is scheduled as a background task after commit.
    """
    try:
        auth_user_id = await deactivate_team_user(
            db, ctx.user.tenant_id, uuid_mod.UUID(user_id), str(ctx.user.auth_user_id),
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(_background_delete_auth_user, auth_user_id)

    return {"status": "deactivated"}
```

- [ ] **Step 3: Run all tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/settings/service.py app/modules/settings/router.py
git commit -m "feat: wire audit log into settings service (invite, resend, revoke, deactivate)"
```

---

### Task 6: Wire Audit Calls Into org_units, auth, and admin

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`
- Modify: `backend/nexus/app/modules/org_units/router.py`
- Modify: `backend/nexus/app/modules/auth/router.py`
- Modify: `backend/nexus/app/modules/admin/service.py`

- [ ] **Step 1: Add audit calls to org_units/service.py**

Add these imports to the top of `backend/nexus/app/modules/org_units/service.py`:

```python
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
```

Then add `actor_email` and `ip_address` params + audit calls to each mutating function. The changes per function:

**`create_org_unit`** — add params `actor_email: str | None = None, ip_address: str | None = None` after `created_by`. Add after the `logger.info` at the end of the function (before `return unit`):

```python
    await log_event(
        db,
        tenant_id=client_id,
        actor_id=created_by,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_CREATED,
        resource="org_unit",
        resource_id=unit.id,
        payload={
            "name": name,
            "unit_type": unit_type,
            "parent_unit_id": str(parent_unit_id) if parent_unit_id else None,
        },
        ip_address=ip_address,
    )
```

**`update_org_unit`** — add params `actor_id: uuid_mod.UUID | None = None, actor_email: str | None = None, ip_address: str | None = None` after `admin_delete_disabled`. Capture before-state at the top, compute diff at the end:

At the very start of the function (before any mutations), add:

```python
    before = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
    }
```

At the end of the function (before `return unit`), add:

```python
    after = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
    }
    changed = {
        k: {"from": before[k], "to": after[k]}
        for k in before
        if before[k] != after[k]
    }
    if changed:
        await log_event(
            db,
            tenant_id=unit.client_id,
            actor_id=actor_id,
            actor_email=actor_email,
            action=audit_actions.ORG_UNIT_UPDATED,
            resource="org_unit",
            resource_id=unit.id,
            payload={"changed": changed},
            ip_address=ip_address,
        )
```

**`assign_role`** — add params `actor_email: str | None = None, ip_address: str | None = None` after `assigned_by`. Add after `logger.info`:

```python
    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=assigned_by,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_MEMBER_ADDED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"user_id": str(user_id), "role_id": str(role_id)},
        ip_address=ip_address,
    )
```

**`remove_user_from_unit`** — add params `actor_id: uuid_mod.UUID | None = None, actor_email: str | None = None, ip_address: str | None = None` after `user_id`. Add after `logger.info`:

```python
    await log_event(
        db,
        tenant_id=assignments[0].tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_MEMBER_REMOVED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"user_id": str(user_id), "roles_removed": len(assignments)},
        ip_address=ip_address,
    )
```

**`delete_org_unit`** — add params `actor_email: str | None = None, ip_address: str | None = None` after `caller_has_admin_role`. Add before `await db.delete(unit)`:

```python
    await log_event(
        db,
        tenant_id=unit.client_id,
        actor_id=caller_user_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_DELETED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"name": unit.name},
        ip_address=ip_address,
    )
```

**`remove_role_from_user`** — add params `actor_id: uuid_mod.UUID | None = None, actor_email: str | None = None, ip_address: str | None = None` after `role_id`. Add after `logger.info`:

```python
    await log_event(
        db,
        tenant_id=assignment.tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_ROLE_REMOVED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"user_id": str(user_id), "role_id": str(role_id)},
        ip_address=ip_address,
    )
```

- [ ] **Step 2: Update org_units/router.py to pass actor info**

Add `Request` import (already imported from fastapi) and add `request: Request` parameter to every mutating endpoint. Pass `actor_id`, `actor_email`, and `ip_address` to each service call.

For `create_unit` — add `request: Request` param, pass to service:
```python
async def create_unit(
    data: CreateOrgUnitRequest,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
```
And in the service call:
```python
        unit = await create_org_unit(
            db, ctx.user.tenant_id, data.name, data.unit_type, parent_id,
            created_by=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
```

Apply the same pattern to `update_unit`, `delete_unit`, `assign_member_role`, `remove_member`, and `remove_member_role` — add `request: Request` parameter and pass `actor_id=ctx.user.id`, `actor_email=ctx.user.email`, `ip_address=request.client.host if request.client else None` to the service call.

For `delete_unit`:
```python
        await delete_org_unit(
            db,
            org_unit_id=uid,
            caller_user_id=ctx.user.id,
            is_super_admin=ctx.is_super_admin,
            caller_has_admin_role=ctx.has_role_in_unit(uid, "Admin"),
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
```

For `assign_member_role`:
```python
        await assign_role(
            db, org_unit_id=uid,
            user_id=uuid_mod.UUID(data.user_id),
            role_id=uuid_mod.UUID(data.role_id),
            tenant_id=ctx.user.tenant_id,
            assigned_by=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
```

For `remove_member`:
```python
        count = await remove_user_from_unit(
            db, uid, uuid_mod.UUID(user_id),
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
```

For `remove_member_role`:
```python
        await remove_role_from_user(
            db, uid, uuid_mod.UUID(user_id), uuid_mod.UUID(role_id),
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
```

For `update_unit`:
```python
        unit = await update_org_unit(
            db, unit, data.name, data.unit_type,
            deletable_by=data.deletable_by,
            set_deletable_by=set_deletable_by,
            admin_delete_disabled=data.admin_delete_disabled,
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
```

- [ ] **Step 3: Add audit call to auth/router.py — complete_invite**

In `backend/nexus/app/modules/auth/router.py`, add imports:

```python
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
```

In the `complete_invite` function, add after `logger.info("auth.invite_completed", ...)` (before the return):

```python
    # TODO: refactor complete_invite logic into auth/service.py so audit call moves to service layer
    await log_event(
        db,
        tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
        actor_id=user.id,
        actor_email=oauth_email,
        action=audit_actions.USER_INVITE_CLAIMED,
        resource="user",
        resource_id=user.id,
        payload={"email": oauth_email, "is_super_admin": is_super_admin},
        ip_address=request.client.host if request.client else None,
    )
```

- [ ] **Step 4: Add audit call to auth/router.py — complete_onboarding**

In the `complete_onboarding` function, add `request: Request` as a parameter:

```python
async def complete_onboarding(
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_bypass_db),
) -> dict[str, str]:
```

After `client.onboarding_complete = True` (before the return):

```python
    await log_event(
        db,
        tenant_id=ctx.user.tenant_id,
        actor_id=ctx.user.id,
        actor_email=ctx.user.email,
        action=audit_actions.CLIENT_ONBOARDING_COMPLETED,
        resource="client",
        resource_id=ctx.user.tenant_id,
        payload={},
        ip_address=request.client.host if request.client else None,
    )
```

- [ ] **Step 5: Add audit call to admin/service.py — provision_client**

In `backend/nexus/app/modules/admin/service.py`, add imports:

```python
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
```

Add `actor_id: uuid_mod.UUID | None = None` parameter to `provision_client` (import `uuid as uuid_mod` if not already imported):

```python
async def provision_client(
    *,
    db: AsyncSession,
    client_name: str,
    admin_email: str,
    domain: str = "",
    industry: str = "",
    plan: str = "trial",
    admin_identity: str,
    actor_id: uuid_mod.UUID | None = None,
    ip_address: str | None = None,
) -> tuple[Client, UserInvite, str]:
```

Add `import uuid as uuid_mod` to the top of the file. After `logger.info("admin.client_provisioned", ...)`:

```python
    await log_event(
        db,
        tenant_id=client.id,
        actor_id=actor_id,
        actor_email=admin_identity,
        action=audit_actions.CLIENT_PROVISIONED,
        resource="client",
        resource_id=client.id,
        payload={"client_name": client_name, "admin_email": admin_email, "plan": plan},
        ip_address=ip_address,
    )
```

Update the admin router caller to pass the new params. In `backend/nexus/app/modules/admin/router.py`, the `provision_client_endpoint` already has `request: Request`. Update the service call:

```python
    client, invite, invite_url = await provision_client(
        db=db,
        client_name=data.company_name,
        admin_email=data.admin_email,
        domain=data.domain or "",
        industry=data.industry or "",
        plan=data.plan or "trial",
        admin_identity=request.state.token_payload.email,
        ip_address=request.client.host if request.client else None,
    )
```

- [ ] **Step 6: Run all tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 7: Run ruff**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && ruff check . && ruff format .
```

Fix any issues found.

- [ ] **Step 8: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/org_units/service.py app/modules/org_units/router.py app/modules/auth/router.py app/modules/admin/service.py app/modules/admin/router.py
git commit -m "feat: wire audit log into org_units, auth, and admin services"
```

---

### Task 7: Update Documentation

**Files:**
- Modify: `docs/phase-1-implementation.md`

- [ ] **Step 1: Update docs/phase-1-implementation.md**

Add the following to the Database Schema section (after the `user_invites` table):

```markdown
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
```

In the "Team Invite System" → "Deactivation cascade" section, update to reflect the corrected flow:

```markdown
**Deactivation cascade:**
1. `user.is_active = false` (immediate — blocks all authenticated endpoints)
2. All `user_invites` for that email → `status = 'revoked'`
3. All `organizational_units` where `deletable_by = user.id` → `deletable_by = NULL`
4. Audit log entry recorded (action: `user.deactivated`)
5. HTTP DELETE to Supabase Admin API scheduled as a **background task** (best-effort cleanup, not a security boundary)
```

Add to the "Known Gaps" section:

```markdown
| `complete_invite` inline in router | Business logic (invite claiming, user creation) lives in `auth/router.py` instead of a service function. Audit call is a pragmatic exception. | Low (flagged with TODO) |
```

- [ ] **Step 2: Commit**

```bash
git add docs/phase-1-implementation.md
git commit -m "docs: update Phase 1 docs with audit log table and corrected deactivation flow"
```

---

### Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v --tb=short
```

Expected: All tests PASS.

- [ ] **Step 2: Run linting**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && ruff check . && ruff format --check .
```

Expected: No errors.

- [ ] **Step 3: Verify no uncommitted changes**

```bash
git status
```

Expected: Clean working tree.
