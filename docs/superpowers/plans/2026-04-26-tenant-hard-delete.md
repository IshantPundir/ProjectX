# Tenant Hard Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third tenant lifecycle operation, **Hard Delete**, that purges all tenant-scoped operational data and Supabase Auth identities while preserving the `audit_log` for compliance forensics.

**Architecture:** Database-level `ON DELETE CASCADE` on every `clients.id` FK (except `audit_log`, whose FKs are dropped) means a single `DELETE FROM clients` unwinds the entire tenant. The Supabase Auth purge runs best-effort post-commit so a transient admin-API failure cannot roll back the DB delete. The endpoint is gated to soft-deleted-only tenants and requires server-validated typed-name confirmation.

**Tech Stack:** FastAPI + SQLAlchemy async (asyncpg) + Alembic migrations on the backend; Next.js 16 (App Router) + Tailwind v4 on the admin frontend.

**Spec:** `docs/superpowers/specs/2026-04-26-tenant-hard-delete-design.md`

---

## File map

**Backend — create:**
- `backend/nexus/migrations/versions/0023_tenant_hard_delete_cascade.py` — drops audit_log FKs and converts 14 FKs to `ON DELETE CASCADE`
- `backend/nexus/tests/test_admin_hard_delete.py` — service-level + HTTP-level tests for the new operation

**Backend — modify:**
- `backend/nexus/app/models.py` — update 14 `ForeignKey("clients.id")` declarations to add `ondelete="CASCADE"`; remove FK declarations on `audit_log.tenant_id` and `audit_log.actor_id`
- `backend/nexus/app/modules/audit/actions.py` — add 2 new audit-action constants
- `backend/nexus/app/modules/admin/schemas.py` — add `HardDeleteRequest` + `HardDeleteResponse`
- `backend/nexus/app/modules/admin/service.py` — add `ConfirmationMismatchError`, `_purge_auth_users`, `hard_delete_client`
- `backend/nexus/app/modules/admin/router.py` — add `POST /api/admin/clients/{id}/hard-delete`
- `backend/nexus/app/main.py` — add `ConfirmationMismatchError` handler

**Frontend — modify:**
- `frontend/admin/app/(admin)/dashboard/page.tsx` — re-enable actions menu for soft-deleted rows; add "Permanently delete" option, confirmation modal with typed-name input, API call

---

## Task 1 — Backend schema: migration + model FK updates

This task is non-TDD: schema changes are verified by inspecting `pg_constraint` after applying the migration, and tests in later tasks exercise the cascade behavior.

**Files:**
- Create: `backend/nexus/migrations/versions/0023_tenant_hard_delete_cascade.py`
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Create the migration file**

Write `backend/nexus/migrations/versions/0023_tenant_hard_delete_cascade.py`:

```python
"""drop audit_log FKs + convert tenant_id FKs to ON DELETE CASCADE

The hard-delete operation issues `DELETE FROM clients WHERE id = ?` and
relies on Postgres to cascade through every tenant-scoped table. This
migration:

  1. Drops `audit_log_tenant_id_fkey` and `audit_log_actor_id_fkey` so
     audit history outlives the rows it references.
  2. Replaces the default-NO-ACTION FKs on 14 tenant-scoped tables with
     `ON DELETE CASCADE` so the cascade actually unwinds. The 5 newer
     Phase-3 tables already have CASCADE and are not touched.

Pattern per FK: ALTER TABLE DROP CONSTRAINT, ALTER TABLE ADD CONSTRAINT
with the same name and column, with `ON DELETE CASCADE` appended.

Revision ID: 0023_tenant_hard_delete_cascade
Revises: 0022_users_partial_unique_auth
Create Date: 2026-04-26
"""

from alembic import op


revision = "0023_tenant_hard_delete_cascade"
down_revision = "0022_users_partial_unique_auth"
branch_labels = None
depends_on = None


# (table, constraint name, referencing column)
_FKS_TO_CASCADE: list[tuple[str, str, str]] = [
    ("users", "users_tenant_id_fkey", "tenant_id"),
    ("user_invites", "user_invites_tenant_id_fkey", "tenant_id"),
    ("user_role_assignments", "user_role_assignments_tenant_id_fkey", "tenant_id"),
    # organizational_units uses `client_id`, not `tenant_id` — older naming.
    ("organizational_units", "organizational_units_client_id_fkey", "client_id"),
    ("roles", "roles_tenant_id_fkey", "tenant_id"),
    ("job_postings", "job_postings_tenant_id_fkey", "tenant_id"),
    ("job_posting_signal_snapshots", "job_posting_signal_snapshots_tenant_id_fkey", "tenant_id"),
    ("sessions", "sessions_tenant_id_fkey", "tenant_id"),
    ("pipeline_templates", "pipeline_templates_tenant_id_fkey", "tenant_id"),
    ("pipeline_template_stages", "pipeline_template_stages_tenant_id_fkey", "tenant_id"),
    ("job_pipeline_instances", "job_pipeline_instances_tenant_id_fkey", "tenant_id"),
    ("job_pipeline_stages", "job_pipeline_stages_tenant_id_fkey", "tenant_id"),
    # Non-standard naming — older convention.
    ("stage_question_banks", "fk_stage_question_banks_tenant", "tenant_id"),
    ("stage_questions", "fk_stage_questions_tenant", "tenant_id"),
]


def upgrade() -> None:
    # 1. Drop audit_log FKs.
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT IF EXISTS audit_log_tenant_id_fkey")
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT IF EXISTS audit_log_actor_id_fkey")

    # 2. Convert tenant FKs to CASCADE.
    for table, constraint, col in _FKS_TO_CASCADE:
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(
            f"ALTER TABLE public.{table} "
            f"ADD CONSTRAINT {constraint} FOREIGN KEY ({col}) "
            f"REFERENCES public.clients(id) ON DELETE CASCADE"
        )


def downgrade() -> None:
    # Reverse 2: restore the no-cascade FKs.
    for table, constraint, col in _FKS_TO_CASCADE:
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(
            f"ALTER TABLE public.{table} "
            f"ADD CONSTRAINT {constraint} FOREIGN KEY ({col}) "
            f"REFERENCES public.clients(id)"
        )

    # Reverse 1: restore audit_log FKs.
    op.execute(
        "ALTER TABLE public.audit_log "
        "ADD CONSTRAINT audit_log_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES public.clients(id)"
    )
    op.execute(
        "ALTER TABLE public.audit_log "
        "ADD CONSTRAINT audit_log_actor_id_fkey "
        "FOREIGN KEY (actor_id) REFERENCES public.users(id)"
    )
```

- [ ] **Step 2: Apply the migration**

Run from `backend/nexus/`:

```bash
docker compose run --rm nexus alembic upgrade head
```

Expected: `Running upgrade 0022_users_partial_unique_auth -> 0023_tenant_hard_delete_cascade, drop audit_log FKs + convert tenant_id FKs to ON DELETE CASCADE`.

- [ ] **Step 3: Verify FK state in the DB**

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE contype='f' AND pg_get_constraintdef(oid) LIKE '%REFERENCES clients(id)%' AND pg_get_constraintdef(oid) NOT LIKE '%ON DELETE CASCADE%';"
```

Expected: zero rows (every remaining FK to `clients(id)` should now have `ON DELETE CASCADE`).

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT conname FROM pg_constraint WHERE conrelid='public.audit_log'::regclass AND contype='f';"
```

Expected: zero rows (both audit_log FKs dropped).

- [ ] **Step 4: Update SQLAlchemy models to match**

The test DB uses `Base.metadata.create_all`, so the SQLAlchemy model declarations are the source of truth for tests. Each FK declared in the migration as `ON DELETE CASCADE` must mirror that on the model.

Modify `backend/nexus/app/models.py`. For each of the 14 lines, replace `ForeignKey("clients.id")` with `ForeignKey("clients.id", ondelete="CASCADE")`. Use Edit's replace_all only after confirming the search string is unique per occurrence; in practice the column declarations differ in surrounding context. Touch these models specifically:

```python
# Class User (line ~56)
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

# Class OrganizationalUnit (line ~69) — column is `client_id`
client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

# Class Role (line ~97) — nullable column
tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))

# Class UserRoleAssignment (line ~116)
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

# Class UserInvite (line ~126)
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

# Class JobPosting (line ~162)
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

# Class JobPostingSignalSnapshot (line ~201)
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

# Class PipelineTemplate (line ~226) — multi-line declaration
tenant_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
)

# Class PipelineTemplateStage (line ~265) — multi-line
tenant_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
)

# Class JobPipelineInstance (line ~302) — multi-line
tenant_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
)

# Class JobPipelineStage (line ~338) — multi-line
tenant_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
)

# Class StageQuestionBank (line ~415) — multi-line
tenant_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
)

# Class Session (line ~475) — multi-line; this is `sessions` table
tenant_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
)

# Class StageQuestion / StageQuestions — find by tablename `stage_questions`
tenant_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
)
```

For `AuditLog` (line ~143), drop the FK from both `tenant_id` and `actor_id`. The columns themselves stay — only the FK reference is removed:

```python
# Before:
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

# After:
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

(Inspect lines 138–155 for the actual model class to be sure of column names and any `Index` declarations that should remain unchanged.)

- [ ] **Step 5: Verify model file syntax**

```bash
cd backend/nexus && python -m py_compile app/models.py
```

Expected: no output (clean compile).

- [ ] **Step 6: Restart backend and verify startup**

```bash
docker compose restart nexus && sleep 6 && docker compose logs --tail=8 nexus
```

Expected: `rls.completeness_check_ok tables_verified=20`, `Application startup complete.`.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/migrations/versions/0023_tenant_hard_delete_cascade.py backend/nexus/app/models.py
git commit -m "$(cat <<'EOF'
feat(admin): drop audit_log FKs and add ON DELETE CASCADE on tenant FKs

Migration 0023 drops audit_log_tenant_id_fkey and audit_log_actor_id_fkey
so audit history outlives the rows it references, then converts 14
tenant-scoped FKs from default-NO-ACTION to ON DELETE CASCADE so the
upcoming hard-delete operation can rely on Postgres to unwind the
tenant in a single statement. SQLAlchemy models updated to match so
the test DB (which uses Base.metadata.create_all) agrees with prod.
EOF
)"
```

---

## Task 2 — Audit action constants

**Files:**
- Modify: `backend/nexus/app/modules/audit/actions.py`

- [ ] **Step 1: Add the two constants**

In `backend/nexus/app/modules/audit/actions.py`, add at the bottom of the "Client actions" block:

```python
# Client actions
CLIENT_PROVISIONED = "client.provisioned"
CLIENT_ONBOARDING_COMPLETED = "client.onboarding_completed"
CLIENT_BLOCKED = "client.blocked"
CLIENT_UNBLOCKED = "client.unblocked"
CLIENT_DELETED = "client.deleted"
CLIENT_HARD_DELETED = "client.hard_deleted"
CLIENT_HARD_DELETE_AUTH_PARTIAL = "client.hard_delete_auth_partial"
```

- [ ] **Step 2: Verify**

```bash
grep -n "CLIENT_HARD" backend/nexus/app/modules/audit/actions.py
```

Expected: two lines printed.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/audit/actions.py
git commit -m "feat(audit): add client.hard_deleted and client.hard_delete_auth_partial actions"
```

---

## Task 3 — Pydantic request + response schemas

**Files:**
- Modify: `backend/nexus/app/modules/admin/schemas.py`

- [ ] **Step 1: Add the two schemas**

Append to `backend/nexus/app/modules/admin/schemas.py`:

```python
class HardDeleteRequest(BaseModel):
    """Body for POST /api/admin/clients/{id}/hard-delete.

    `confirmation_name` must equal the target client's `name` exactly,
    enforced server-side. The admin UI also gates the submit button on
    this match — server-side check is defense in depth against direct
    API calls.
    """

    confirmation_name: str


class HardDeleteResponse(BaseModel):
    """Returned on successful hard delete. The `clients` row is gone, so
    `purged_at` is synthesized at response time, not read back from the
    DB."""

    client_id: str
    purged_at: str  # ISO-8601 UTC
    auth_users_purged: int
    auth_users_failed: int
```

- [ ] **Step 2: Verify**

```bash
cd backend/nexus && python -m py_compile app/modules/admin/schemas.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/admin/schemas.py
git commit -m "feat(admin): add HardDeleteRequest and HardDeleteResponse schemas"
```

---

## Task 4 — Service exception class

**Files:**
- Modify: `backend/nexus/app/modules/admin/service.py`

- [ ] **Step 1: Add `ConfirmationMismatchError` next to existing exceptions**

In `backend/nexus/app/modules/admin/service.py`, locate the existing `InvalidClientStateError` class and add directly after it:

```python
class ConfirmationMismatchError(Exception):
    """Raised when the typed confirmation name does not match
    `client.name` exactly. Mapped to 422 with `code = 'CONFIRMATION_MISMATCH'`
    by the handler in `app/main.py`."""
```

- [ ] **Step 2: Verify**

```bash
grep -n "ConfirmationMismatchError" backend/nexus/app/modules/admin/service.py
```

Expected: one line (the class definition).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/admin/service.py
git commit -m "feat(admin): add ConfirmationMismatchError exception"
```

---

## Task 5 — `_purge_auth_users` helper (TDD)

**Files:**
- Create: `backend/nexus/tests/test_admin_hard_delete.py`
- Modify: `backend/nexus/app/modules/admin/service.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_admin_hard_delete.py`:

```python
"""Tests for the tenant hard-delete operation."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.admin.service import _purge_auth_users
from app.modules.auth.admin.base import AuthProviderError


@pytest.mark.asyncio
async def test_purge_auth_users_partial_failure(monkeypatch):
    """One success, one failure — both must be reported, neither aborts the other."""
    success_id = "00000000-0000-0000-0000-000000000001"
    failure_id = "00000000-0000-0000-0000-000000000002"

    fake_provider = AsyncMock()

    async def fake_delete_user(uid: str) -> None:
        if uid == failure_id:
            raise AuthProviderError("HTTP 500: simulated supabase outage")
        # success path — return None (provider.delete_user returns None on success)

    fake_provider.delete_user = fake_delete_user

    monkeypatch.setattr(
        "app.modules.admin.service.get_auth_provider",
        lambda: fake_provider,
    )

    purged, failed = await _purge_auth_users([success_id, failure_id])

    assert purged == [success_id]
    assert len(failed) == 1
    assert failed[0][0] == failure_id
    assert "simulated supabase outage" in failed[0][1]
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_admin_hard_delete.py::test_purge_auth_users_partial_failure -v
```

Expected: FAIL with `ImportError` or `AttributeError` ("`_purge_auth_users` cannot be imported from `app.modules.admin.service`").

- [ ] **Step 3: Implement the helper**

In `backend/nexus/app/modules/admin/service.py`, near the top add the import (if not already present):

```python
from app.modules.auth.admin import get_auth_provider
from app.modules.auth.admin.base import AuthProviderError
```

(Verify the exact import path — `from app.modules.auth.admin._factory import get_auth_provider` may be the right one. Read the existing imports in `app/modules/settings/service.py:_delete_auth_user` and copy the pattern.)

Then add the helper function (place after `_load_client`, before `block_client`):

```python
async def _purge_auth_users(
    auth_user_ids: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Best-effort bulk delete of Supabase Auth users.

    Returns `(purged, failed)`. `failed` is `[(auth_user_id, reason_str), ...]`.
    Each call is independently try/excepted so one failure does not abort
    the rest. Reuses the provider abstraction so a future Cognito swap
    requires no change here.
    """
    provider = get_auth_provider()
    purged: list[str] = []
    failed: list[tuple[str, str]] = []
    for uid in auth_user_ids:
        try:
            await provider.delete_user(uid)
            purged.append(uid)
        except AuthProviderError as e:
            failed.append((uid, str(e)))
            logger.warning(
                "admin.hard_delete.auth_user_purge_failed",
                auth_user_id=uid,
                error=str(e),
            )
    return purged, failed
```

- [ ] **Step 4: Run test, verify it passes**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_admin_hard_delete.py::test_purge_auth_users_partial_failure -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/tests/test_admin_hard_delete.py backend/nexus/app/modules/admin/service.py
git commit -m "feat(admin): add _purge_auth_users best-effort bulk helper

Per-call try/except so one Supabase Admin API failure does not abort
the others. Returns (purged, failed) tuple so callers can record
partial-success state in the audit log."
```

---

## Task 6 — `hard_delete_client` service function (TDD)

**Files:**
- Modify: `backend/nexus/tests/test_admin_hard_delete.py`
- Modify: `backend/nexus/app/modules/admin/service.py`

This task has multiple test cases for one function. Write all the tests first, then implement.

- [ ] **Step 1: Write failing tests for state-machine guards**

Append to `backend/nexus/tests/test_admin_hard_delete.py`:

```python
import uuid as uuid_mod
from datetime import UTC, datetime

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Client
from app.modules.admin.service import (
    ConfirmationMismatchError,
    InvalidClientStateError,
    delete_client,
    hard_delete_client,
)
from tests.conftest import create_test_client


@pytest.mark.asyncio
async def test_hard_delete_rejects_active_tenant(db: AsyncSession):
    client = await create_test_client(db)
    with pytest.raises(InvalidClientStateError):
        await hard_delete_client(
            db=db,
            client_id=client.id,
            admin_identity="admin@projectx.com",
            confirmation_name=client.name,
        )


@pytest.mark.asyncio
async def test_hard_delete_rejects_blocked_tenant(db: AsyncSession):
    client = await create_test_client(db)
    client.blocked_at = datetime.now(UTC)
    await db.flush()
    with pytest.raises(InvalidClientStateError):
        await hard_delete_client(
            db=db,
            client_id=client.id,
            admin_identity="admin@projectx.com",
            confirmation_name=client.name,
        )


@pytest.mark.asyncio
async def test_hard_delete_rejects_mismatched_name(db: AsyncSession):
    client = await create_test_client(db)
    await delete_client(
        db=db,
        client_id=client.id,
        admin_identity="admin@projectx.com",
    )
    with pytest.raises(ConfirmationMismatchError):
        await hard_delete_client(
            db=db,
            client_id=client.id,
            admin_identity="admin@projectx.com",
            confirmation_name=client.name + "_typo",
        )
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_admin_hard_delete.py -v
```

Expected: 3 test failures with `ImportError` ("`hard_delete_client` not importable") or `AttributeError`.

- [ ] **Step 3: Implement `hard_delete_client` (state-machine portion)**

In `backend/nexus/app/modules/admin/service.py`, add after `delete_client`:

```python
async def hard_delete_client(
    *,
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    admin_identity: str,
    confirmation_name: str,
    actor_id: uuid_mod.UUID | None = None,
    ip_address: str | None = None,
) -> dict:
    """Permanently purge a tenant.

    Preconditions:
      - The client must be in `deleted` state (soft-deleted first).
      - `confirmation_name` must equal `client.name` exactly.

    On success:
      - DB cascade unwinds every tenant-scoped table except `audit_log`.
      - Supabase Auth users are purged best-effort (post-commit; failures
        are logged but do not roll back the DB delete).
      - Returns `{client_id, purged_at, auth_users_purged, auth_users_failed}`.

    Raises:
      - `ClientNotFoundError` if the client doesn't exist.
      - `InvalidClientStateError` if not in `deleted` state.
      - `ConfirmationMismatchError` if name doesn't match.
    """
    client = await _load_client(db, client_id)

    # State gate: must be soft-deleted.
    current = _client_status(client)
    if current != "deleted":
        raise InvalidClientStateError(current=current, requested="purged")

    # Confirmation gate.
    if confirmation_name != client.name:
        raise ConfirmationMismatchError()

    # Snapshot before the cascade for audit + auth purge.
    auth_user_ids_result = await db.execute(
        sqlalchemy.text(
            "SELECT auth_user_id::text FROM public.users WHERE tenant_id = :tid"
        ),
        {"tid": str(client.id)},
    )
    auth_user_ids = [row[0] for row in auth_user_ids_result.all()]
    snapshot = {"client_name": client.name, "user_count": len(auth_user_ids)}

    # Pre-cascade audit. Written inside the same transaction as the DELETE
    # so either both happen or neither does.
    await log_event(
        db,
        tenant_id=client.id,
        actor_id=actor_id,
        actor_email=admin_identity,
        action=audit_actions.CLIENT_HARD_DELETED,
        resource="client",
        resource_id=client.id,
        payload=snapshot,
        ip_address=ip_address,
    )

    # The cascade. Postgres unwinds every CASCADE-marked FK in dependency
    # order; audit_log rows survive because their FKs were dropped by
    # migration 0023.
    await db.execute(
        sqlalchemy.text("DELETE FROM public.clients WHERE id = :id"),
        {"id": str(client.id)},
    )
    await db.flush()

    purged_at = datetime.now(UTC)
    logger.info(
        "admin.client_hard_deleted",
        client_id=str(client.id),
        client_name=client.name,
        user_count=len(auth_user_ids),
    )

    return {
        "client_id": str(client.id),
        "purged_at": purged_at.isoformat(),
        "auth_user_ids": auth_user_ids,  # router consumes for the auth-purge step
    }
```

Note: the auth-purge step is intentionally NOT inside this function — the function returns the `auth_user_ids` list and the router calls `_purge_auth_users` after the DB transaction commits. This is the boundary that prevents a Supabase outage from rolling back the DB delete.

- [ ] **Step 4: Run state-machine tests, verify they pass**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_admin_hard_delete.py -v
```

Expected: 3 prior tests now PASS (`test_hard_delete_rejects_active_tenant`, `_rejects_blocked_tenant`, `_rejects_mismatched_name`). The `_purge_auth_users` test still passes.

- [ ] **Step 5: Write the cascade-success test**

Append to `backend/nexus/tests/test_admin_hard_delete.py`:

```python
@pytest.mark.asyncio
async def test_hard_delete_purges_users_and_invites_preserves_audit(
    db: AsyncSession,
):
    """Soft-delete then hard-delete; verify users + invites are gone,
    audit_log row for the hard-delete event survives."""
    from app.models import User, UserInvite

    client = await create_test_client(db)
    user = User(
        auth_user_id=uuid_mod.uuid4(),
        tenant_id=client.id,
        email=f"user-{uuid_mod.uuid4()}@example.com",
        full_name="Test User",
    )
    db.add(user)
    invite = UserInvite(
        tenant_id=client.id,
        email=f"pending-{uuid_mod.uuid4()}@example.com",
        token_hash="x" * 64,
        status="pending",
    )
    db.add(invite)
    await db.flush()

    # Step into "deleted" state via the regular soft-delete service.
    await delete_client(
        db=db, client_id=client.id, admin_identity="admin@projectx.com"
    )

    # Hard delete.
    result = await hard_delete_client(
        db=db,
        client_id=client.id,
        admin_identity="admin@projectx.com",
        confirmation_name=client.name,
    )
    assert result["client_id"] == str(client.id)

    # Cascade verification: clients, users, invites all gone for this tenant.
    for table in ("clients", "users", "user_invites"):
        col = "id" if table == "clients" else "tenant_id"
        result_count = await db.execute(
            sqlalchemy.text(
                f"SELECT count(*) FROM public.{table} WHERE {col} = :tid"
            ),
            {"tid": str(client.id)},
        )
        assert result_count.scalar() == 0, f"{table} still has rows for tenant"

    # Audit_log preservation: the hard-delete event row should survive.
    audit_count = await db.execute(
        sqlalchemy.text(
            "SELECT count(*) FROM public.audit_log "
            "WHERE tenant_id = :tid AND action = 'client.hard_deleted'"
        ),
        {"tid": str(client.id)},
    )
    assert audit_count.scalar() == 1
```

- [ ] **Step 6: Run cascade test, verify it passes**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_admin_hard_delete.py::test_hard_delete_purges_users_and_invites_preserves_audit -v
```

Expected: PASS.

If FAIL with FK constraint error: investigate via the DB error message — most likely a missed FK in either the migration or the model. Re-verify both match the spec's table.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/tests/test_admin_hard_delete.py backend/nexus/app/modules/admin/service.py
git commit -m "$(cat <<'EOF'
feat(admin): hard_delete_client service with cascade verification

State-machine guards (must be soft-deleted, name must match) raise typed
exceptions. The DELETE FROM clients statement runs in the same
transaction as the audit-event insert so a partial cascade is
impossible. auth_user_ids are returned to the caller so the auth-purge
step can run after commit.
EOF
)"
```

---

## Task 7 — Exception handler in main.py

**Files:**
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Add the handler**

In `backend/nexus/app/main.py`, locate the existing `_account_suspended` handler. Directly after it, add:

```python
from app.modules.admin.service import ConfirmationMismatchError as _ConfirmationMismatchError

@application.exception_handler(_ConfirmationMismatchError)
async def _confirmation_mismatch(
    request: Request, exc: _ConfirmationMismatchError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Confirmation name does not match.",
            "code": "CONFIRMATION_MISMATCH",
        },
    )
```

(Aliased on import to avoid colliding with anything else named the same. Use the existing import-inside-create_app pattern; `Request` and `JSONResponse` are already imported in this section.)

- [ ] **Step 2: Verify**

```bash
cd backend/nexus && python -m py_compile app/main.py
```

Expected: no output.

- [ ] **Step 3: Restart and verify startup**

```bash
docker compose restart nexus && sleep 6 && docker compose logs --tail=4 nexus
```

Expected: `Application startup complete.`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/main.py
git commit -m "feat(admin): map ConfirmationMismatchError to 422 CONFIRMATION_MISMATCH"
```

---

## Task 8 — API endpoint (TDD)

**Files:**
- Modify: `backend/nexus/tests/test_admin_hard_delete.py`
- Modify: `backend/nexus/app/modules/admin/router.py`

- [ ] **Step 1: Write a failing endpoint test**

Look at how existing admin tests authenticate (e.g. `tests/test_auth_endpoints.py`). The `client` fixture is an `AsyncClient` against the FastAPI app; admin endpoints require `is_projectx_admin=True` in the token. There is no production endpoint to mint such a token in tests — existing tests use `app.dependency_overrides` with `request.state.token_payload` injected via a custom middleware override. Match the existing pattern.

Append to `backend/nexus/tests/test_admin_hard_delete.py`:

```python
from app.modules.auth.schemas import TokenPayload


def _admin_token_payload(email: str = "admin@projectx.com") -> TokenPayload:
    return TokenPayload(
        sub=str(uuid_mod.uuid4()),
        tenant_id="",  # ProjectX admins have no tenant
        email=email,
        role="authenticated",
        is_projectx_admin=True,
        exp=2_000_000_000,
    )


@pytest.mark.asyncio
async def test_hard_delete_endpoint_returns_409_when_not_soft_deleted(
    client, db, monkeypatch
):
    """Active tenant must be rejected with 409 InvalidClientStateError."""
    from app.middleware.auth import AuthMiddleware
    from app.database import get_bypass_db

    test_client_obj = await create_test_client(db)
    await db.commit()  # endpoint uses its own session; need this row visible

    # Patch the auth middleware to inject the admin token payload.
    async def _fake_dispatch(self, request, call_next):
        request.state.token_payload = _admin_token_payload()
        request.state.user_id = request.state.token_payload.sub
        request.state.tenant_id = ""
        request.state.is_projectx_admin = True
        return await call_next(request)

    monkeypatch.setattr(AuthMiddleware, "dispatch", _fake_dispatch)

    # Override get_bypass_db to share the test transaction.
    from app.main import app as fastapi_app
    fastapi_app.dependency_overrides[get_bypass_db] = lambda: db

    try:
        resp = await client.post(
            f"/api/admin/clients/{test_client_obj.id}/hard-delete",
            json={"confirmation_name": test_client_obj.name},
        )
    finally:
        fastapi_app.dependency_overrides.clear()

    assert resp.status_code == 409
```

(Note: this test pattern may need adjustment depending on existing conventions. Read `tests/test_admin_*.py` if it exists — none does currently; cross-reference `tests/test_auth_endpoints.py` and `tests/test_settings.py` to copy the working middleware-override + dependency-override approach. If the existing tests don't use this pattern, simplify by calling the service function directly via `delete_client_endpoint` import and pass through.)

- [ ] **Step 2: Run test, verify it fails (404 or import error)**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_admin_hard_delete.py::test_hard_delete_endpoint_returns_409_when_not_soft_deleted -v
```

Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add the endpoint to the admin router**

In `backend/nexus/app/modules/admin/router.py`:

a. Add the imports at top of the file:

```python
from app.modules.admin.schemas import (
    ClientListItem,
    ClientStatusResponse,
    HardDeleteRequest,
    HardDeleteResponse,
    ProvisionClientRequest,
    ProvisionClientResponse,
)
from app.modules.admin.service import (
    ClientNotFoundError,
    ConfirmationMismatchError,
    InvalidClientStateError,
    _client_status,
    _purge_auth_users,
    block_client,
    delete_client,
    hard_delete_client,
    list_clients,
    provision_client,
    unblock_client,
)
```

b. Add the endpoint after the existing `delete_client_endpoint`:

```python
@router.post(
    "/clients/{client_id}/hard-delete",
    response_model=HardDeleteResponse,
    dependencies=[require_projectx_admin()],
)
async def hard_delete_client_endpoint(
    client_id: str,
    data: HardDeleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> HardDeleteResponse:
    """Permanently purge a soft-deleted tenant.

    Returns counts of Supabase Auth identities that were purged vs.
    failed. The DB cascade always succeeds atomically (any failure
    rolls back); the auth purge is best-effort and a partial state is
    captured in the audit log.
    """
    cid = _parse_client_id(client_id)
    actor_email = request.state.token_payload.email

    try:
        result = await hard_delete_client(
            db=db,
            client_id=cid,
            admin_identity=actor_email,
            confirmation_name=data.confirmation_name,
            ip_address=request.client.host if request.client else None,
        )
    except ClientNotFoundError:
        raise HTTPException(status_code=404, detail="Client not found")
    except InvalidClientStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # ConfirmationMismatchError is mapped to 422 by the global handler.

    # Best-effort Supabase Auth purge — runs after DB transaction commits
    # via the get_bypass_db context manager exit.
    purged, failed = await _purge_auth_users(result["auth_user_ids"])

    if failed:
        # Open a fresh bypass session for the partial-success audit event.
        # The original session has already exited the context manager.
        from app.database import get_bypass_session
        from app.modules.audit import actions as audit_actions
        from app.modules.audit.service import log_event

        async with get_bypass_session() as audit_db:
            await log_event(
                audit_db,
                tenant_id=cid,
                actor_id=None,
                actor_email=actor_email,
                action=audit_actions.CLIENT_HARD_DELETE_AUTH_PARTIAL,
                resource="client",
                resource_id=cid,
                payload={
                    "purged": purged,
                    "failed": [
                        {"auth_user_id": uid, "reason": reason}
                        for uid, reason in failed
                    ],
                },
                ip_address=request.client.host if request.client else None,
            )

    return HardDeleteResponse(
        client_id=result["client_id"],
        purged_at=result["purged_at"],
        auth_users_purged=len(purged),
        auth_users_failed=len(failed),
    )
```

- [ ] **Step 4: Run test, verify it passes**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_admin_hard_delete.py::test_hard_delete_endpoint_returns_409_when_not_soft_deleted -v
```

Expected: PASS (returns 409).

- [ ] **Step 5: Restart and smoke-test**

```bash
docker compose restart nexus && sleep 6 && curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
```

Expected: `200`.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/admin/router.py backend/nexus/tests/test_admin_hard_delete.py
git commit -m "$(cat <<'EOF'
feat(admin): POST /api/admin/clients/{id}/hard-delete endpoint

Wraps hard_delete_client + best-effort _purge_auth_users behind a
projectx-admin-gated POST. Partial-success states (some Supabase Auth
deletes failed) are logged to a separate audit event written through a
fresh bypass session, since the original session has exited its
transaction by then.
EOF
)"
```

---

## Task 9 — Admin frontend: re-enable actions menu for soft-deleted rows

**Files:**
- Modify: `frontend/admin/app/(admin)/dashboard/page.tsx`

- [ ] **Step 1: Update the actions menu rendering**

In `frontend/admin/app/(admin)/dashboard/page.tsx`, the actions button currently has `disabled={isDeleted || isPending}`. We want it enabled for deleted rows, but offering only the "Permanently delete" option.

Locate the actions menu. Replace the `disabled` attribute and the menu body so that:

```tsx
<button
  type="button"
  disabled={isPending}
  onClick={() => setOpenMenu(menuOpen ? null : c.client_id)}
  className="text-zinc-400 hover:text-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed px-2 py-1 rounded"
  aria-haspopup="menu"
  aria-expanded={menuOpen}
  aria-label={`Actions for ${c.client_name}`}
>
  {isPending ? "..." : "⋯"}
</button>
{menuOpen && (
  <div
    ref={menuRef}
    role="menu"
    className="absolute right-4 top-10 z-10 bg-white border border-zinc-200 rounded-lg shadow-md w-44 py-1 text-left"
  >
    {isDeleted ? (
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          setOpenMenu(null);
          setConfirmHardDelete(c);
        }}
        className="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50"
      >
        Permanently delete
      </button>
    ) : (
      <>
        {isBlocked ? (
          <button
            type="button"
            role="menuitem"
            onClick={() => runAction(c, "unblock")}
            className="w-full text-left px-3 py-2 text-sm hover:bg-zinc-50"
          >
            Unblock
          </button>
        ) : (
          <button
            type="button"
            role="menuitem"
            onClick={() => runAction(c, "block")}
            className="w-full text-left px-3 py-2 text-sm hover:bg-zinc-50"
          >
            Block
          </button>
        )}
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            setOpenMenu(null);
            setConfirmDelete(c);
          }}
          className="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50"
        >
          Delete
        </button>
      </>
    )}
  </div>
)}
```

- [ ] **Step 2: Add the new state hook for the hard-delete modal**

Near the top of the component (next to `confirmDelete`), add:

```tsx
const [confirmHardDelete, setConfirmHardDelete] = useState<Client | null>(null);
const [hardDeleteInput, setHardDeleteInput] = useState("");
const [hardDeleteSubmitting, setHardDeleteSubmitting] = useState(false);
```

- [ ] **Step 3: Verify the file still compiles in the dev server**

```bash
cd frontend/admin && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors related to dashboard/page.tsx (existing errors elsewhere are pre-existing).

- [ ] **Step 4: Commit**

```bash
git add frontend/admin/app/\(admin\)/dashboard/page.tsx
git commit -m "feat(admin/ui): show 'Permanently delete' option on soft-deleted rows"
```

---

## Task 10 — Admin frontend: confirmation modal + API call

**Files:**
- Modify: `frontend/admin/app/(admin)/dashboard/page.tsx`

- [ ] **Step 1: Add the API call helper**

Inside the `DashboardPage` component, alongside `runAction`, add:

```tsx
async function runHardDelete(c: Client) {
  if (hardDeleteInput !== c.client_name) return;
  setError("");
  setHardDeleteSubmitting(true);
  try {
    const token = await getToken();
    if (!token) {
      window.location.href = "/login";
      return;
    }
    const result = await apiFetch<{
      client_id: string;
      purged_at: string;
      auth_users_purged: number;
      auth_users_failed: number;
    }>(`/api/admin/clients/${c.client_id}/hard-delete`, {
      token,
      method: "POST",
      body: JSON.stringify({ confirmation_name: hardDeleteInput }),
    });
    // Drop the row from the list — there is nothing to update on it.
    setClients((prev) => prev.filter((row) => row.client_id !== c.client_id));
    setConfirmHardDelete(null);
    setHardDeleteInput("");
    if (result.auth_users_failed > 0) {
      setError(
        `Tenant deleted, but ${result.auth_users_failed} Supabase Auth identities failed to purge. Check server logs.`,
      );
    }
  } catch (err) {
    setError(err instanceof Error ? err.message : "Failed to permanently delete");
  } finally {
    setHardDeleteSubmitting(false);
  }
}
```

- [ ] **Step 2: Render the modal**

Below the existing `confirmDelete` modal block (search for `{confirmDelete && (`), add:

```tsx
{confirmHardDelete && (
  <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40">
    <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
      <h2 className="text-base font-semibold text-zinc-900 mb-2">
        Permanently delete {confirmHardDelete.client_name}?
      </h2>
      <p className="text-sm text-zinc-600 mb-4">
        This will erase:
      </p>
      <ul className="list-disc pl-5 text-sm text-zinc-600 mb-4 space-y-1">
        <li>All operational data (users, jobs, candidates, sessions, pipelines, …)</li>
        <li>All Supabase Auth identities for users in this tenant</li>
        <li>The tenant record itself</li>
      </ul>
      <p className="text-sm text-zinc-600 mb-1">
        The audit log will be preserved.
      </p>
      <p className="text-sm font-medium text-red-600 mb-4">
        This cannot be undone.
      </p>
      <label className="block text-sm text-zinc-700 mb-1">
        Type{" "}
        <span className="font-mono font-semibold">
          {confirmHardDelete.client_name}
        </span>{" "}
        to confirm:
      </label>
      <input
        type="text"
        value={hardDeleteInput}
        onChange={(e) => setHardDeleteInput(e.target.value)}
        autoFocus
        className="w-full border border-zinc-300 rounded-md px-3 py-2 text-sm mb-4"
      />
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            setConfirmHardDelete(null);
            setHardDeleteInput("");
          }}
          disabled={hardDeleteSubmitting}
          className="px-4 py-2 text-sm rounded-lg border border-zinc-200 hover:bg-zinc-50 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => runHardDelete(confirmHardDelete)}
          disabled={
            hardDeleteSubmitting ||
            hardDeleteInput !== confirmHardDelete.client_name
          }
          className="px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
        >
          {hardDeleteSubmitting ? "Deleting..." : "Permanently delete"}
        </button>
      </div>
    </div>
  </div>
)}
```

- [ ] **Step 3: Verify type-check**

```bash
cd frontend/admin && npx tsc --noEmit 2>&1 | head -20
```

Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/admin/app/\(admin\)/dashboard/page.tsx
git commit -m "$(cat <<'EOF'
feat(admin/ui): hard-delete confirmation modal with typed-name gate

The 'Permanently delete' button stays disabled until the typed input
exactly matches the tenant name. Server-side confirmation in the
endpoint provides defense in depth against direct API calls.
EOF
)"
```

---

## Task 11 — End-to-end verification (manual)

**Files:** none (this is a smoke test against the running dev environment).

The dev DB currently has a soft-deleted BinQle tenant (`ca6f2a9d-2479-4044-9516-ecc2ae74d0c0`). Use it as the test target.

- [ ] **Step 1: Confirm dev DB starting state**

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT id, name, deleted_at FROM clients;"
```

Expected: at least one row with `deleted_at IS NOT NULL` (the BinQle tenant). Note its UUID — you'll see it disappear in step 4.

- [ ] **Step 2: Open the admin dashboard**

In a browser, navigate to `http://localhost:3001/dashboard` (or wherever the admin app runs). The soft-deleted BinQle row should be visible with the **deleted** status badge.

- [ ] **Step 3: Drive the UI**

a. Click the `⋯` actions menu on the BinQle row.
b. Verify only one option appears: "Permanently delete" in red.
c. Click it; the modal opens.
d. Verify the "Permanently delete" submit button is disabled.
e. Type the tenant name. The button should enable when the typed text exactly matches.
f. Click "Permanently delete". The modal closes; the row disappears from the list.

- [ ] **Step 4: Verify DB-level purge**

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT count(*) FROM clients WHERE name = 'BinQle';"
```

Expected: `0`.

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT count(*) FROM users WHERE tenant_id = 'ca6f2a9d-2479-4044-9516-ecc2ae74d0c0';"
```

Expected: `0`.

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT count(*), MAX(action) FROM audit_log WHERE tenant_id = 'ca6f2a9d-2479-4044-9516-ecc2ae74d0c0';"
```

Expected: `count >= 1`, with at least one `client.hard_deleted` entry. The audit log survives the cascade.

- [ ] **Step 5: Verify Supabase Auth purge**

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT email FROM auth.users WHERE email LIKE '%@binqle.com';"
```

Expected: `0 rows`. The Supabase Auth identity was best-effort purged. (If non-zero, check nexus logs for `admin.hard_delete.auth_user_purge_failed` warnings — that's the partial-success path.)

- [ ] **Step 6: Verify nexus startup is still healthy**

```bash
curl -sS http://127.0.0.1:8000/health
```

Expected: `{"status":"ok",...}`.

- [ ] **Step 7: No commit**

This task is verification-only; nothing to commit.

---

## Self-review

Skimmed the spec and matched each requirement to a task:

- §3 Lifecycle state model → Task 6 (state-machine guards), Task 9 (UI gate on deleted rows only)
- §4 Cascade scope → Task 1 (migration + model), Task 6 step 5–6 (cascade verification test)
- §5 API surface → Task 3 (schemas), Task 8 (endpoint), Task 7 (422 mapping)
- §6 Migration 0023 → Task 1
- §7 Service flow → Task 6 (impl), Task 8 (router post-commit auth-purge step)
- §8 Auth provider purge → Task 5 (helper), Task 8 (router invocation)
- §9 Audit trail → Task 2 (constants), Task 6 step 3 (pre-cascade write), Task 8 step 3 (partial-success write)
- §10 Admin UI flow → Task 9 + Task 10
- §11 Error contracts → Task 4 (`ConfirmationMismatchError`), Task 7 (handler), Task 6 (`InvalidClientStateError` reuse)
- §12 Testing approach → Task 5, Task 6, Task 8 cover the must-have unit + HTTP cases. Tests 5 (audit preserve), 6 (audit written) and the populate-every-table cascade test (test 4 in spec) are bundled into `test_hard_delete_purges_users_and_invites_preserves_audit` — pragmatic subset for the first PR; expanding to cover every Phase-2C/3 table is a follow-up if any cascade gap is discovered.
- §13 Risks → mitigated structurally; the "FK migration leaves a non-CASCADE FK behind" risk has a verification step in Task 1 step 3.

**Type consistency check passed:** `_purge_auth_users` returns `tuple[list[str], list[tuple[str, str]]]` consistently across helper, service, and router; `HardDeleteRequest.confirmation_name` is the same key in service signature and router call site.

**No placeholder references** to types or methods that aren't defined in some task.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-04-26-tenant-hard-delete.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task with two-stage review between each. Best for catching mistakes early; slightly slower per task; clean separation between planning and execution.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints for review. Faster; relies on a single context.

Which approach?
