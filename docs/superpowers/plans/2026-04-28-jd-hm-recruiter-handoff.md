# JD Stakeholder Handoff (HM → Recruiter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the HM-raises → Recruiter-claims → Recruiter-operates → HM-approves workflow defined in `docs/superpowers/specs/2026-04-28-jd-hm-recruiter-handoff-design.md`.

**Architecture:** Three new states added to the JD state machine (`pending_recruiter`, `pending_hm_approval`, `pending_recruiter_revision`). Six new columns on `job_postings`. Five new endpoints + modified `POST /api/jobs`. New permission-derived authority helper plus a separate name-matched admin-override. AI extraction defers to recruiter claim (cost discipline). Edit-rights matrix enforced server-side via a single helper called from every mutation route.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / asyncpg / Alembic / pytest / Next.js 16 App Router / TypeScript / TanStack Query / vitest.

**Spec:** `docs/superpowers/specs/2026-04-28-jd-hm-recruiter-handoff-design.md`

---

## File Structure

### Backend

| File | Status | Responsibility |
|---|---|---|
| `backend/nexus/migrations/versions/0024_jd_hm_handoff_columns.py` | new | Schema additions on `job_postings` + index + CHECK constraint update |
| `backend/nexus/migrations/versions/0025_reseed_system_role_permissions.py` | new | Idempotent re-seed of system roles' permission lists |
| `backend/nexus/app/models.py` | modify | Add new fields to `JobPosting` ORM class |
| `backend/nexus/app/modules/jd/state_machine.py` | modify | `LEGAL_TRANSITIONS` extension |
| `backend/nexus/app/modules/jd/authz.py` | modify | Add `derive_jd_authority`, `has_admin_override`, `require_edit_rights` |
| `backend/nexus/app/modules/jd/errors.py` | modify | Add `ConflictError` for claim race |
| `backend/nexus/app/modules/jd/schemas.py` | modify | Response model: new optional fields |
| `backend/nexus/app/modules/jd/service.py` | modify | `claim_job_for_recruiter`, branching `create_job_posting`, approval helpers |
| `backend/nexus/app/modules/jd/router.py` | modify | Branch in `POST /api/jobs`; new endpoints; list filters; response payload |
| `backend/nexus/app/main.py` | modify | 409 message map for new states |
| `backend/nexus/app/modules/audit/actions.py` | modify | Four new constants |
| `backend/nexus/app/modules/notifications/service.py` | modify | Two new notification events (claimed, approved/returned/published) |
| `backend/nexus/app/modules/notifications/templates/req_*.html` | new | Email templates (six) |
| `backend/nexus/tests/test_migration_0024.py` | new | Schema migration test |
| `backend/nexus/tests/test_migration_0025.py` | new | Permission seed test |
| `backend/nexus/tests/test_jd_authz.py` | modify | Add `derive_jd_authority`, `has_admin_override`, `require_edit_rights` cases |
| `backend/nexus/tests/test_jd_state_transitions_integration.py` | modify | New transitions |
| `backend/nexus/tests/test_jd_handoff_integration.py` | new | End-to-end HM→Recruiter→HM happy + revision paths |
| `backend/nexus/tests/test_jd_claim_race.py` | new | Atomic claim race coverage |

### Frontend

| File | Status | Responsibility |
|---|---|---|
| `frontend/app/lib/api/jobs.ts` | modify | New endpoints + response types for new fields |
| `frontend/app/lib/hooks/use-jd-authority.ts` | new | Frontend mirror of `derive_jd_authority` + `has_admin_override` |
| `frontend/app/lib/hooks/use-claim-job.ts` | new | Mutation hook for claim |
| `frontend/app/lib/hooks/use-jd-approval.ts` | new | Mutation hooks: send-for-approval, approve, return, resend |
| `frontend/app/app/(dashboard)/jobs/new/page.tsx` | modify | Branch UI on authority — HM raise mode vs recruiter create wizard |
| `frontend/app/app/(dashboard)/jobs/page.tsx` | modify | Tabs: Unclaimed / My active / All; filters wire to API |
| `frontend/app/app/(dashboard)/jobs/[jobId]/review/page.tsx` | modify | HM approval action bar; recruiter read-only banner; revision-notes banner |
| `frontend/app/components/dashboard/jd-panels/ApprovalActions.tsx` | new | Approve / Return / Re-send buttons |
| `frontend/app/components/dashboard/jd-panels/RevisionNotesBanner.tsx` | new | Yellow banner when in `pending_recruiter_revision` |
| `frontend/app/tests/components/ApprovalActions.test.tsx` | new | Component tests |
| `frontend/app/tests/jobs/raise-form.test.tsx` | new | HM raise mode tests |

---

## Phase 1 — Backend foundation (schema + state machine)

### Task 1: Migration 0024 — schema additions on `job_postings`

**Files:**
- Create: `backend/nexus/migrations/versions/0024_jd_hm_handoff_columns.py`

- [ ] **Step 1: Write the migration**

```python
"""jd_hm_handoff_columns

Adds the columns + indexes required for the HM/Recruiter handoff workflow,
and updates ck_job_postings_status to allow the three new states.

Revision ID: 0024_jd_hm_handoff_columns
Revises: 0023_tenant_hard_delete_cascade
Create Date: 2026-04-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0024_jd_hm_handoff_columns"
down_revision = "0023_tenant_hard_delete_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New columns on job_postings.
    op.execute("""
        ALTER TABLE job_postings
          ADD COLUMN created_by_role TEXT NOT NULL DEFAULT 'recruiter'
            CHECK (created_by_role IN ('hm', 'recruiter', 'admin')),
          ADD COLUMN assigned_recruiter_id UUID REFERENCES users(id),
          ADD COLUMN claimed_at TIMESTAMPTZ,
          ADD COLUMN approved_by_hm UUID REFERENCES users(id),
          ADD COLUMN approved_at TIMESTAMPTZ,
          ADD COLUMN revision_notes TEXT
    """)

    # 2. Drop + re-create the status CHECK with the three new values.
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT ck_job_postings_status")
    op.execute("""
        ALTER TABLE job_postings
          ADD CONSTRAINT ck_job_postings_status
          CHECK (status IN (
            'pending_recruiter',
            'draft',
            'signals_extracting',
            'signals_extraction_failed',
            'signals_extracted',
            'signals_confirmed',
            'pipeline_built',
            'pending_hm_approval',
            'pending_recruiter_revision',
            'active',
            'archived'
          ))
    """)

    # 3. Partial index for the unclaimed-queue dashboard query.
    op.execute("""
        CREATE INDEX job_postings_unclaimed_idx
          ON job_postings (org_unit_id)
          WHERE assigned_recruiter_id IS NULL
            AND status = 'pending_recruiter'
    """)

    # 4. Partial index for "my assigned reqs" query.
    op.execute("""
        CREATE INDEX job_postings_assigned_recruiter_idx
          ON job_postings (assigned_recruiter_id, status)
          WHERE assigned_recruiter_id IS NOT NULL
    """)


def downgrade() -> None:
    # Lossy rollback: rows in the new states map back to a legacy state
    # before we drop those values from the CHECK constraint, otherwise
    # the constraint re-add would fail.
    op.execute("""
        UPDATE job_postings SET status = 'draft'
         WHERE status IN ('pending_recruiter', 'pending_recruiter_revision')
    """)
    op.execute("""
        UPDATE job_postings SET status = 'pipeline_built'
         WHERE status = 'pending_hm_approval'
    """)

    op.execute("DROP INDEX IF EXISTS job_postings_assigned_recruiter_idx")
    op.execute("DROP INDEX IF EXISTS job_postings_unclaimed_idx")

    op.execute("ALTER TABLE job_postings DROP CONSTRAINT ck_job_postings_status")
    op.execute("""
        ALTER TABLE job_postings
          ADD CONSTRAINT ck_job_postings_status
          CHECK (status IN ('draft', 'signals_extracting', 'signals_extraction_failed',
                            'signals_extracted', 'signals_confirmed',
                            'pipeline_built', 'active', 'archived'))
    """)

    op.execute("""
        ALTER TABLE job_postings
          DROP COLUMN revision_notes,
          DROP COLUMN approved_at,
          DROP COLUMN approved_by_hm,
          DROP COLUMN claimed_at,
          DROP COLUMN assigned_recruiter_id,
          DROP COLUMN created_by_role
    """)
```

- [ ] **Step 2: Run the migration upward + downward smoke test**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic downgrade -1
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
```

Expected: each command prints "Running upgrade 0023 -> 0024" / "Running downgrade 0024 -> 0023" without errors. The third command re-applies cleanly.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/migrations/versions/0024_jd_hm_handoff_columns.py
git commit -m "feat(jd): migration 0024 — schema for HM/Recruiter handoff"
```

---

### Task 2: Migration 0024 test

**Files:**
- Create: `backend/nexus/tests/test_migration_0024.py`

- [ ] **Step 1: Write the test**

```python
"""Verifies that migration 0024 adds the expected columns + indexes
+ updates the status CHECK constraint to include the new states."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_0024_columns_exist(db):
    cols = await db.execute(text("""
        SELECT column_name, is_nullable, data_type
          FROM information_schema.columns
         WHERE table_name = 'job_postings'
           AND column_name IN (
               'created_by_role', 'assigned_recruiter_id', 'claimed_at',
               'approved_by_hm', 'approved_at', 'revision_notes'
           )
    """))
    rows = {r[0]: (r[1], r[2]) for r in cols.all()}
    assert set(rows.keys()) == {
        "created_by_role", "assigned_recruiter_id", "claimed_at",
        "approved_by_hm", "approved_at", "revision_notes",
    }
    assert rows["created_by_role"][0] == "NO"   # NOT NULL


@pytest.mark.asyncio
async def test_0024_status_check_includes_new_states(db):
    result = await db.execute(text("""
        SELECT pg_get_constraintdef(oid)
          FROM pg_constraint
         WHERE conname = 'ck_job_postings_status'
    """))
    defn = result.scalar()
    assert "pending_recruiter" in defn
    assert "pending_hm_approval" in defn
    assert "pending_recruiter_revision" in defn


@pytest.mark.asyncio
async def test_0024_indexes_exist(db):
    result = await db.execute(text("""
        SELECT indexname FROM pg_indexes
         WHERE tablename = 'job_postings'
           AND indexname IN (
               'job_postings_unclaimed_idx',
               'job_postings_assigned_recruiter_idx'
           )
    """))
    names = {r[0] for r in result.all()}
    assert names == {"job_postings_unclaimed_idx", "job_postings_assigned_recruiter_idx"}
```

- [ ] **Step 2: Run the test**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_migration_0024.py -v
```

Expected: 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_migration_0024.py
git commit -m "test(jd): migration 0024 schema assertions"
```

---

### Task 3: Migration 0025 — re-seed system role permissions

**Files:**
- Create: `backend/nexus/migrations/versions/0025_reseed_system_role_permissions.py`

- [ ] **Step 1: Write the migration**

```python
"""reseed_system_role_permissions

Idempotently UPDATEs the permissions JSONB on every is_system=true role
to the canonical set defined in the JD HM/Recruiter handoff spec
(docs/superpowers/specs/2026-04-28-jd-hm-recruiter-handoff-design.md §3.1).

Tenant-custom roles (is_system=false) are not touched.

Revision ID: 0025_reseed_system_role_permissions
Revises: 0024_jd_hm_handoff_columns
Create Date: 2026-04-28
"""

from alembic import op

revision = "0025_reseed_system_role_permissions"
down_revision = "0024_jd_hm_handoff_columns"
branch_labels = None
depends_on = None


# Canonical permission sets per role, post-spec. Reordered keys for stability.
ADMIN_PERMS = [
    "users.invite_admins", "users.invite_users", "users.deactivate",
    "org_units.create", "org_units.manage",
    "jobs.create", "jobs.manage", "jobs.view",
    "candidates.view", "candidates.evaluate", "candidates.advance",
    "candidates.manage",
    "interviews.schedule", "interviews.conduct",
    "reports.view", "reports.export",
    "settings.client", "settings.integrations",
]

RECRUITER_PERMS = [
    "jobs.create", "jobs.manage", "jobs.view",
    "candidates.view", "candidates.advance", "candidates.manage",
    "interviews.schedule", "reports.view",
]

HIRING_MANAGER_PERMS = [
    "jobs.create", "jobs.view",
    "candidates.view", "candidates.evaluate", "candidates.advance",
    "reports.view", "reports.export",
]

INTERVIEWER_PERMS = [
    "jobs.view",
    "candidates.view", "candidates.evaluate",
    "interviews.conduct",
]

OBSERVER_PERMS = [
    "jobs.view", "candidates.view", "reports.view",
]

LEGACY_ADMIN_PERMS = [
    "users.invite_admins", "users.invite_users", "users.deactivate",
    "org_units.create", "org_units.manage",
    "jobs.create", "jobs.manage",
    "candidates.view", "candidates.evaluate", "candidates.advance",
    "interviews.schedule", "interviews.conduct",
    "reports.view", "reports.export",
    "settings.client", "settings.integrations",
]
LEGACY_RECRUITER_PERMS = [
    "jobs.create", "jobs.manage",
    "candidates.view", "candidates.advance",
    "interviews.schedule", "reports.view",
]
LEGACY_HM_PERMS = [
    "candidates.view", "candidates.evaluate", "candidates.advance",
    "reports.view", "reports.export",
]
LEGACY_INTERVIEWER_PERMS = [
    "interviews.conduct", "candidates.view", "candidates.evaluate",
]
LEGACY_OBSERVER_PERMS = ["candidates.view", "reports.view"]


def _set_perms(name: str, perms: list[str]) -> None:
    perms_json = "[" + ",".join(f'"{p}"' for p in perms) + "]"
    op.execute(f"""
        UPDATE roles
           SET permissions = '{perms_json}'::jsonb
         WHERE name = '{name}'
           AND is_system = TRUE
    """)


def upgrade() -> None:
    _set_perms("Admin", ADMIN_PERMS)
    _set_perms("Recruiter", RECRUITER_PERMS)
    _set_perms("Hiring Manager", HIRING_MANAGER_PERMS)
    _set_perms("Interviewer", INTERVIEWER_PERMS)
    _set_perms("Observer", OBSERVER_PERMS)


def downgrade() -> None:
    _set_perms("Admin", LEGACY_ADMIN_PERMS)
    _set_perms("Recruiter", LEGACY_RECRUITER_PERMS)
    _set_perms("Hiring Manager", LEGACY_HM_PERMS)
    _set_perms("Interviewer", LEGACY_INTERVIEWER_PERMS)
    _set_perms("Observer", LEGACY_OBSERVER_PERMS)
```

- [ ] **Step 2: Run upward + downward smoke**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic downgrade -1
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/migrations/versions/0025_reseed_system_role_permissions.py
git commit -m "feat(jd): migration 0025 — re-seed system role permissions"
```

---

### Task 4: Migration 0025 test

**Files:**
- Create: `backend/nexus/tests/test_migration_0025.py`

- [ ] **Step 1: Write the test**

```python
"""Verifies the canonical permission sets on each system role after migration 0025."""

import pytest
from sqlalchemy import text


EXPECTED = {
    "Admin": {
        "users.invite_admins", "users.invite_users", "users.deactivate",
        "org_units.create", "org_units.manage",
        "jobs.create", "jobs.manage", "jobs.view",
        "candidates.view", "candidates.evaluate", "candidates.advance",
        "candidates.manage",
        "interviews.schedule", "interviews.conduct",
        "reports.view", "reports.export",
        "settings.client", "settings.integrations",
    },
    "Recruiter": {
        "jobs.create", "jobs.manage", "jobs.view",
        "candidates.view", "candidates.advance", "candidates.manage",
        "interviews.schedule", "reports.view",
    },
    "Hiring Manager": {
        "jobs.create", "jobs.view",
        "candidates.view", "candidates.evaluate", "candidates.advance",
        "reports.view", "reports.export",
    },
    "Interviewer": {
        "jobs.view",
        "candidates.view", "candidates.evaluate",
        "interviews.conduct",
    },
    "Observer": {"jobs.view", "candidates.view", "reports.view"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("role_name,expected", list(EXPECTED.items()))
async def test_seed_permissions(db, role_name: str, expected: set[str]):
    result = await db.execute(text(
        "SELECT permissions FROM roles WHERE name = :n AND is_system = TRUE"
    ), {"n": role_name})
    perms = result.scalar()
    assert set(perms) == expected, f"{role_name}: got {set(perms)}, expected {expected}"
```

- [ ] **Step 2: Run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_migration_0025.py -v
```

Expected: 5 PASS (one per parametrize).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_migration_0025.py
git commit -m "test(jd): migration 0025 permission canon"
```

---

### Task 5: Update `JobPosting` ORM model

**Files:**
- Modify: `backend/nexus/app/models.py:167-201`

- [ ] **Step 1: Add the new columns to the ORM**

Insert these after `updated_at` at the end of the existing column declarations:

```python
    # JD HM/Recruiter handoff columns (migration 0024).
    created_by_role: Mapped[str] = mapped_column(
        String, nullable=False, server_default="'recruiter'"
    )
    assigned_recruiter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_hm: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision_notes: Mapped[str | None] = mapped_column(Text)
```

Also update the docstring at the top of the class to reference the new states.

- [ ] **Step 2: Sanity-check the ORM with a quick read query**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "
import asyncio
from sqlalchemy import select
from app.database import async_session_maker
from app.models import JobPosting

async def main():
    async with async_session_maker() as s:
        await s.execute(select(JobPosting).limit(1))
        print('OK')

asyncio.run(main())
"
```

Expected: `OK`. (No SQL errors loading the model.)

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/models.py
git commit -m "feat(jd): add HM/Recruiter handoff fields to JobPosting model"
```

---

### Task 6: Extend the state machine

**Files:**
- Modify: `backend/nexus/app/modules/jd/state_machine.py:23-32`
- Modify: `backend/nexus/app/main.py` (409 message map)

- [ ] **Step 1: Add the new transitions**

Replace `LEGAL_TRANSITIONS` with:

```python
LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "pending_recruiter": {"draft"},
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},
    "signals_extracted": {"signals_confirmed"},
    "signals_confirmed": {"signals_extracted", "pipeline_built"},
    "pipeline_built": {"active", "pending_hm_approval"},
    "pending_hm_approval": {"active", "pending_recruiter_revision"},
    "pending_recruiter_revision": {"pending_hm_approval"},
    "active": set(),
    "archived": set(),
}
```

Update the docstring at the top of the file to mention the new states.

- [ ] **Step 2: Add 409 messages for the new states**

In `app/main.py`, find the `IllegalTransitionError` handler's message map and add:

```python
"pending_recruiter": "JD is awaiting recruiter pickup",
"pending_hm_approval": "JD is awaiting HM approval",
"pending_recruiter_revision": "JD is in revision after HM feedback",
```

(Search for the existing handler — it's in `app/main.py` around the FastAPI exception handler decorators.)

- [ ] **Step 3: Run state machine tests to confirm no regression**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_state_transitions_integration.py -v
```

Expected: existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/jd/state_machine.py backend/nexus/app/main.py
git commit -m "feat(jd): extend state machine with 3 new states + 5 transitions"
```

---

## Phase 2 — Authorization helpers

### Task 7: Add `derive_jd_authority` + `has_admin_override` helpers

**Files:**
- Modify: `backend/nexus/app/modules/jd/authz.py`
- Test: `backend/nexus/tests/test_jd_authz.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_jd_authz.py`:

```python
import uuid
import pytest

from app.modules.auth.context import RoleAssignment, UserContext
from app.modules.jd.authz import derive_jd_authority, has_admin_override
from app.models import OrganizationalUnit, User


def _ctx(user: User, *, super_admin=False, assignments=None) -> UserContext:
    return UserContext(user=user, is_super_admin=super_admin, assignments=assignments or [])


def _ou(uid: uuid.UUID, name: str) -> OrganizationalUnit:
    ou = OrganizationalUnit(id=uid, name=name, unit_type="region", client_id=uuid.uuid4())
    return ou


@pytest.mark.asyncio
async def test_authority_super_admin_returns_recruiter(db):
    user = User(id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    ancestry = [_ou(uuid.uuid4(), "u")]
    assert derive_jd_authority(_ctx(user, super_admin=True), ancestry) == "recruiter"


@pytest.mark.asyncio
async def test_authority_jobs_manage_returns_recruiter(db):
    user = User(id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    unit_id = uuid.uuid4()
    ancestry = [_ou(unit_id, "u")]
    ctx = _ctx(user, assignments=[RoleAssignment(
        org_unit_id=unit_id, org_unit_name="u",
        role_id=uuid.uuid4(), role_name="Recruiter",
        permissions=["jobs.create", "jobs.manage", "jobs.view"],
    )])
    assert derive_jd_authority(ctx, ancestry) == "recruiter"


@pytest.mark.asyncio
async def test_authority_jobs_create_only_returns_hm(db):
    user = User(id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    unit_id = uuid.uuid4()
    ancestry = [_ou(unit_id, "u")]
    ctx = _ctx(user, assignments=[RoleAssignment(
        org_unit_id=unit_id, org_unit_name="u",
        role_id=uuid.uuid4(), role_name="Hiring Manager",
        permissions=["jobs.create", "jobs.view"],
    )])
    assert derive_jd_authority(ctx, ancestry) == "hm"


@pytest.mark.asyncio
async def test_authority_no_jobs_perms_returns_none(db):
    user = User(id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    ancestry = [_ou(uuid.uuid4(), "u")]
    ctx = _ctx(user, assignments=[])
    assert derive_jd_authority(ctx, ancestry) is None


@pytest.mark.asyncio
async def test_admin_override_super_admin(db):
    user = User(id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    ancestry = [_ou(uuid.uuid4(), "u")]
    assert has_admin_override(_ctx(user, super_admin=True), ancestry) is True


@pytest.mark.asyncio
async def test_admin_override_admin_role(db):
    user = User(id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    unit_id = uuid.uuid4()
    ancestry = [_ou(unit_id, "u")]
    ctx = _ctx(user, assignments=[RoleAssignment(
        org_unit_id=unit_id, org_unit_name="u",
        role_id=uuid.uuid4(), role_name="Admin",
        permissions=["jobs.create", "jobs.manage"],
    )])
    assert has_admin_override(ctx, ancestry) is True


@pytest.mark.asyncio
async def test_admin_override_recruiter_does_not_get_override(db):
    user = User(id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    unit_id = uuid.uuid4()
    ancestry = [_ou(unit_id, "u")]
    ctx = _ctx(user, assignments=[RoleAssignment(
        org_unit_id=unit_id, org_unit_name="u",
        role_id=uuid.uuid4(), role_name="Recruiter",
        permissions=["jobs.create", "jobs.manage"],
    )])
    assert has_admin_override(ctx, ancestry) is False
```

- [ ] **Step 2: Run to confirm tests fail with import errors**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_authz.py -v -k "authority or admin_override"
```

Expected: ImportError or NameError on `derive_jd_authority`/`has_admin_override`.

- [ ] **Step 3: Implement the helpers**

Append to `app/modules/jd/authz.py`:

```python
from typing import Literal

JdAuthority = Literal["recruiter", "hm"]


def derive_jd_authority(
    user: UserContext,
    ancestry: list,  # list[OrganizationalUnit]; loose-typed to avoid circular import
) -> JdAuthority | None:
    """Permission-derived authority over a JD anchored at the unit whose
    ancestry is given. Returns:

      'recruiter' if super admin OR jobs.manage in any ancestor
      'hm'        if only jobs.create (no manage) in any ancestor
      None        otherwise (caller has no JD permissions on this branch)

    This is the single source of truth for the "what role is this caller
    acting as on this JD?" question. Routes call this once per request.
    """
    if user.is_super_admin:
        return "recruiter"
    has_manage = any(
        user.has_permission_in_unit(unit.id, "jobs.manage") for unit in ancestry
    )
    if has_manage:
        return "recruiter"
    has_create = any(
        user.has_permission_in_unit(unit.id, "jobs.create") for unit in ancestry
    )
    return "hm" if has_create else None


def has_admin_override(
    user: UserContext,
    ancestry: list,  # list[OrganizationalUnit]
) -> bool:
    """True if the caller is super admin OR holds the system 'Admin' role
    on any unit in the JD's ancestry. Override is *name-matched* — bypassing
    edit locks is a deliberate trust signal, not a permission shorthand.
    Tenant-custom roles with equivalent permissions do NOT get override.
    """
    if user.is_super_admin:
        return True
    return any(
        user.has_role_in_unit(unit.id, "Admin") for unit in ancestry
    )
```

- [ ] **Step 4: Run tests to confirm green**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_authz.py -v -k "authority or admin_override"
```

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/authz.py backend/nexus/tests/test_jd_authz.py
git commit -m "feat(jd): add derive_jd_authority + has_admin_override helpers"
```

---

### Task 8: Add `require_edit_rights` helper

**Files:**
- Modify: `backend/nexus/app/modules/jd/authz.py`
- Test: `backend/nexus/tests/test_jd_authz.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_jd_authz.py`:

```python
from app.modules.jd.authz import require_edit_rights
from app.models import JobPosting
from fastapi import HTTPException


def _job(state: str, *, assigned_recruiter_id=None, created_by=None) -> JobPosting:
    return JobPosting(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        org_unit_id=uuid.uuid4(),
        title="t",
        description_raw="d",
        status=state,
        created_by=created_by or uuid.uuid4(),
        assigned_recruiter_id=assigned_recruiter_id,
    )


def _hm_ctx(user_id):
    user = User(id=user_id, tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    return _ctx(user, assignments=[RoleAssignment(
        org_unit_id=uuid.uuid4(), org_unit_name="u",
        role_id=uuid.uuid4(), role_name="Hiring Manager",
        permissions=["jobs.create", "jobs.view"],
    )])


def _rec_ctx(user_id):
    user = User(id=user_id, tenant_id=uuid.uuid4(), email="x@x", auth_user_id=uuid.uuid4())
    return _ctx(user, assignments=[RoleAssignment(
        org_unit_id=uuid.uuid4(), org_unit_name="u",
        role_id=uuid.uuid4(), role_name="Recruiter",
        permissions=["jobs.create", "jobs.manage", "jobs.view"],
    )])


@pytest.mark.asyncio
async def test_edit_rights_hm_can_edit_brief_in_pending_recruiter(db):
    hm_id = uuid.uuid4()
    job = _job("pending_recruiter", created_by=hm_id)
    require_edit_rights(job, _hm_ctx(hm_id), authority="hm", admin_override=False, artifact="brief")
    # No exception → pass.


@pytest.mark.asyncio
async def test_edit_rights_other_hm_cannot_edit_brief_in_pending_recruiter(db):
    job = _job("pending_recruiter", created_by=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        require_edit_rights(job, _hm_ctx(uuid.uuid4()), authority="hm",
                            admin_override=False, artifact="brief")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_edit_rights_recruiter_cannot_edit_in_pending_hm_approval(db):
    rec_id = uuid.uuid4()
    job = _job("pending_hm_approval", assigned_recruiter_id=rec_id)
    with pytest.raises(HTTPException) as exc:
        require_edit_rights(job, _rec_ctx(rec_id), authority="recruiter",
                            admin_override=False, artifact="signals")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_edit_rights_hm_can_edit_signals_in_pending_hm_approval(db):
    job = _job("pending_hm_approval")
    require_edit_rights(job, _hm_ctx(uuid.uuid4()), authority="hm",
                        admin_override=False, artifact="signals")


@pytest.mark.asyncio
async def test_edit_rights_admin_override_always_passes(db):
    job = _job("pending_hm_approval")
    require_edit_rights(job, _rec_ctx(uuid.uuid4()), authority="recruiter",
                        admin_override=True, artifact="pipeline")


@pytest.mark.asyncio
async def test_edit_rights_recruiter_can_edit_during_revision(db):
    rec_id = uuid.uuid4()
    job = _job("pending_recruiter_revision", assigned_recruiter_id=rec_id)
    require_edit_rights(job, _rec_ctx(rec_id), authority="recruiter",
                        admin_override=False, artifact="question_bank")


@pytest.mark.asyncio
async def test_edit_rights_active_locks_everyone_except_admin(db):
    job = _job("active")
    with pytest.raises(HTTPException):
        require_edit_rights(job, _rec_ctx(uuid.uuid4()), authority="recruiter",
                            admin_override=False, artifact="signals")
```

- [ ] **Step 2: Run to confirm fails**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_authz.py -v -k "edit_rights"
```

Expected: ImportError on `require_edit_rights`.

- [ ] **Step 3: Implement `require_edit_rights`**

Append to `app/modules/jd/authz.py`:

```python
EditableArtifact = Literal["brief", "signals", "pipeline", "question_bank"]


# Edit-rights matrix from spec §7.1. Returns True if (state, authority,
# artifact) is editable. Admin override is checked before this lookup.
_MATRIX: dict[str, dict[str, set[str]]] = {
    "pending_recruiter": {
        "hm_creator": {"brief"},
    },
    "draft": {"recruiter": {"brief", "signals", "pipeline", "question_bank"}},
    "signals_extracting": {"recruiter": {"brief", "signals", "pipeline", "question_bank"}},
    "signals_extraction_failed": {"recruiter": {"brief", "signals", "pipeline", "question_bank"}},
    "signals_extracted": {"recruiter": {"brief", "signals", "pipeline", "question_bank"}},
    "signals_confirmed": {"recruiter": {"brief", "signals", "pipeline", "question_bank"}},
    "pipeline_built": {"recruiter": {"brief", "signals", "pipeline", "question_bank"}},
    "pending_hm_approval": {"hm": {"brief", "signals", "pipeline", "question_bank"}},
    "pending_recruiter_revision": {"recruiter": {"brief", "signals", "pipeline", "question_bank"}},
    "active": {},
    "archived": {},
}


def require_edit_rights(
    job,  # JobPosting; loose-typed to avoid circular import
    user: UserContext,
    *,
    authority: JdAuthority | None,
    admin_override: bool,
    artifact: EditableArtifact,
) -> None:
    """Raises 403 if the caller cannot edit `artifact` on `job` in its
    current state. Admin override short-circuits to allow.

    Special case: the `pending_recruiter` state restricts brief edits to
    the original creator (the HM who raised the req). Other HMs in the
    ancestry cannot edit a sibling HM's draft brief.
    """
    if admin_override:
        return  # full bypass — recorded in audit elsewhere

    state = job.status
    artifacts_for_authority = _MATRIX.get(state, {}).get(authority or "", set())

    # pending_recruiter has the creator-only special case.
    if state == "pending_recruiter" and authority == "hm":
        if user.user.id == job.created_by:
            allowed = _MATRIX[state].get("hm_creator", set())
        else:
            allowed = set()
    else:
        allowed = artifacts_for_authority

    if artifact not in allowed:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail=f"Cannot edit {artifact} in state {state}",
        )
```

- [ ] **Step 4: Run tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_authz.py -v
```

Expected: all PASS (the 7 prior + 7 new).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/authz.py backend/nexus/tests/test_jd_authz.py
git commit -m "feat(jd): add require_edit_rights for the §7.1 matrix"
```

---

## Phase 3 — Errors + audit constants

### Task 9: Add `ConflictError` and audit action constants

**Files:**
- Modify: `backend/nexus/app/modules/jd/errors.py`
- Modify: `backend/nexus/app/modules/audit/actions.py`

- [ ] **Step 1: Add ConflictError**

Append to `backend/nexus/app/modules/jd/errors.py`:

```python
class JdConflictError(Exception):
    """409: state-machine or claim race conflict on a JD operation."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)
```

- [ ] **Step 2: Wire it into `app/main.py`**

Find the existing `IllegalTransitionError` handler in `app/main.py` and add a sibling handler for `JdConflictError`:

```python
@app.exception_handler(JdConflictError)
async def jd_conflict_handler(request: Request, exc: JdConflictError):
    return JSONResponse(status_code=409, content={"detail": exc.detail})
```

(Import the error at the top.)

- [ ] **Step 3: Add audit action constants**

Append to `backend/nexus/app/modules/audit/actions.py`:

```python
# JD lifecycle (HM/Recruiter handoff — spec 2026-04-28).
JOB_POSTING_CLAIMED = "job_posting.claimed"
JOB_POSTING_SENT_FOR_APPROVAL = "job_posting.sent_for_approval"
JOB_POSTING_APPROVED = "job_posting.approved"
JOB_POSTING_RETURNED_TO_RECRUITER = "job_posting.returned_to_recruiter"
```

- [ ] **Step 4: Sanity-check imports**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "
from app.modules.jd.errors import JdConflictError
from app.modules.audit import actions as a
print(JdConflictError, a.JOB_POSTING_CLAIMED, a.JOB_POSTING_APPROVED)
"
```

Expected: prints the symbols.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/errors.py backend/nexus/app/main.py backend/nexus/app/modules/audit/actions.py
git commit -m "feat(jd): JdConflictError + 4 new audit action constants"
```

---

## Phase 4 — Service layer: claim + handoff transitions

### Task 10: `claim_job_for_recruiter` in service

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_jd_claim_race.py`:

```python
"""Verifies atomic first-claim-wins on POST /api/jobs/{id}/claim."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.models import JobPosting
from app.modules.jd.service import claim_job_for_recruiter
from app.modules.jd.errors import JdConflictError
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


@pytest.mark.asyncio
async def test_claim_happy_path(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id, name="U")
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=unit.id,
        title="T", description_raw="raw", created_by=user.id,
        status="pending_recruiter", created_by_role="hm",
    )
    db.add(job); await db.flush()

    claimed = await claim_job_for_recruiter(
        db, job_id=job.id, recruiter_id=user.id, correlation_id="cid",
    )
    assert claimed.assigned_recruiter_id == user.id
    assert claimed.claimed_at is not None
    assert claimed.status == "draft"


@pytest.mark.asyncio
async def test_claim_already_claimed_raises_conflict(db):
    tenant = await create_test_client(db)
    await db.flush()
    user1 = await create_test_user(db, tenant.id, email="u1@x")
    user2 = await create_test_user(db, tenant.id, email="u2@x")
    unit = await create_test_org_unit(db, tenant.id, name="U")
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=unit.id,
        title="T", description_raw="raw", created_by=user1.id,
        status="pending_recruiter", created_by_role="hm",
    )
    db.add(job); await db.flush()

    await claim_job_for_recruiter(db, job_id=job.id, recruiter_id=user1.id, correlation_id="cid")

    with pytest.raises(JdConflictError):
        await claim_job_for_recruiter(db, job_id=job.id, recruiter_id=user2.id, correlation_id="cid")


@pytest.mark.asyncio
async def test_claim_wrong_state_raises_conflict(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id, name="U")
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=unit.id,
        title="T", description_raw="raw", created_by=user.id,
        status="draft", created_by_role="recruiter",
    )
    db.add(job); await db.flush()

    with pytest.raises(JdConflictError):
        await claim_job_for_recruiter(db, job_id=job.id, recruiter_id=user.id, correlation_id="cid")
```

- [ ] **Step 2: Run to confirm import error**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_claim_race.py -v
```

Expected: ImportError on `claim_job_for_recruiter`.

- [ ] **Step 3: Implement `claim_job_for_recruiter`**

Append to `app/modules/jd/service.py`:

```python
from sqlalchemy import update

from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
from app.modules.jd.errors import JdConflictError


async def claim_job_for_recruiter(
    db: AsyncSession,
    *,
    job_id: UUID,
    recruiter_id: UUID,
    correlation_id: str,
) -> JobPosting:
    """Atomically claim a pending_recruiter JD for a recruiter.

    The conditional UPDATE matches at most one row (predicate filters on
    `assigned_recruiter_id IS NULL AND status = 'pending_recruiter'`). If
    the predicate matches, the row is updated and returned. If not, no
    row matches and we raise JdConflictError — covers both "already
    claimed" and "wrong state" via the same race-safe gate.

    On success we transition pending_recruiter → draft and write an
    audit event. The Dramatiq dispatch is wired by the route handler
    via BackgroundTasks (after the transaction commits).
    """
    result = await db.execute(
        update(JobPosting)
        .where(
            JobPosting.id == job_id,
            JobPosting.assigned_recruiter_id.is_(None),
            JobPosting.status == "pending_recruiter",
        )
        .values(assigned_recruiter_id=recruiter_id, claimed_at=func.now())
        .returning(JobPosting)
    )
    job = result.scalar_one_or_none()
    if job is None:
        # Distinguish: does the row exist at all?
        check = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
        existing = check.scalar_one_or_none()
        if existing is None:
            raise JdConflictError("Job not found")
        if existing.assigned_recruiter_id is not None:
            raise JdConflictError("Already claimed by another recruiter")
        raise JdConflictError(f"Cannot claim a JD in state {existing.status}")

    await transition(
        db, job, to_state="draft",
        actor_id=recruiter_id, correlation_id=correlation_id,
    )

    await log_event(
        db,
        tenant_id=job.tenant_id,
        actor_id=recruiter_id,
        actor_email=None,
        action=audit_actions.JOB_POSTING_CLAIMED,
        resource="job_posting",
        resource_id=job.id,
        payload={"recruiter_id": str(recruiter_id), "correlation_id": correlation_id},
    )
    return job
```

- [ ] **Step 4: Run tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_claim_race.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/service.py backend/nexus/tests/test_jd_claim_race.py
git commit -m "feat(jd): claim_job_for_recruiter with atomic conditional UPDATE"
```

---

### Task 11: Service helpers for send-for-approval / approve / return / resend

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/nexus/tests/test_jd_state_transitions_integration.py` (a new section):

```python
import pytest
from app.modules.jd.service import (
    send_for_hm_approval,
    approve_by_hm,
    return_to_recruiter,
    resend_for_approval,
)
from app.models import JobPosting


@pytest.mark.asyncio
async def test_send_for_hm_approval_transitions(db, _seeded_job_pipeline_built):
    job = _seeded_job_pipeline_built
    await send_for_hm_approval(db, job=job, actor_id=job.assigned_recruiter_id, correlation_id="cid")
    assert job.status == "pending_hm_approval"


@pytest.mark.asyncio
async def test_approve_by_hm_stamps_and_transitions(db, _seeded_job_pending_hm_approval):
    job = _seeded_job_pending_hm_approval
    hm_id = uuid.uuid4()
    await approve_by_hm(db, job=job, hm_user_id=hm_id, correlation_id="cid")
    assert job.status == "active"
    assert job.approved_by_hm == hm_id
    assert job.approved_at is not None


@pytest.mark.asyncio
async def test_return_to_recruiter_records_notes(db, _seeded_job_pending_hm_approval):
    job = _seeded_job_pending_hm_approval
    await return_to_recruiter(
        db, job=job, hm_user_id=uuid.uuid4(),
        notes="Needs payments-domain experience.",
        correlation_id="cid",
    )
    assert job.status == "pending_recruiter_revision"
    assert "payments" in job.revision_notes


@pytest.mark.asyncio
async def test_resend_clears_notes_and_transitions(db, _seeded_job_pending_revision):
    job = _seeded_job_pending_revision
    await resend_for_approval(db, job=job, actor_id=job.assigned_recruiter_id, correlation_id="cid")
    assert job.status == "pending_hm_approval"
    assert job.revision_notes is None
```

(Assumes pytest fixtures `_seeded_job_pipeline_built`, `_seeded_job_pending_hm_approval`, `_seeded_job_pending_revision` exist or are added to `tests/conftest.py`. Add them in step 2 if missing.)

- [ ] **Step 2: Add or verify fixtures in `tests/conftest.py`**

If the fixtures don't exist, add at the bottom of `tests/conftest.py`:

```python
@pytest.fixture
async def _seeded_job_pipeline_built(db):
    from app.models import JobPosting
    from tests.conftest import create_test_client, create_test_org_unit, create_test_user

    tenant = await create_test_client(db); await db.flush()
    creator = await create_test_user(db, tenant.id, email="hm@x")
    recruiter = await create_test_user(db, tenant.id, email="rec@x")
    unit = await create_test_org_unit(db, tenant.id, name="U"); await db.flush()
    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=unit.id,
        title="T", description_raw="raw",
        created_by=creator.id, created_by_role="hm",
        assigned_recruiter_id=recruiter.id,
        status="pipeline_built",
    )
    db.add(job); await db.flush()
    return job


@pytest.fixture
async def _seeded_job_pending_hm_approval(db, _seeded_job_pipeline_built):
    _seeded_job_pipeline_built.status = "pending_hm_approval"
    await db.flush()
    return _seeded_job_pipeline_built


@pytest.fixture
async def _seeded_job_pending_revision(db, _seeded_job_pending_hm_approval):
    _seeded_job_pending_hm_approval.status = "pending_recruiter_revision"
    _seeded_job_pending_hm_approval.revision_notes = "Needs payments-domain experience."
    await db.flush()
    return _seeded_job_pending_hm_approval
```

- [ ] **Step 3: Run to confirm import error**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_state_transitions_integration.py -v -k "approval or return or resend"
```

Expected: ImportError.

- [ ] **Step 4: Implement the helpers**

Append to `app/modules/jd/service.py`:

```python
async def send_for_hm_approval(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
    correlation_id: str,
) -> None:
    """Transition pipeline_built → pending_hm_approval."""
    await transition(db, job, to_state="pending_hm_approval",
                     actor_id=actor_id, correlation_id=correlation_id)
    await log_event(
        db, tenant_id=job.tenant_id, actor_id=actor_id, actor_email=None,
        action=audit_actions.JOB_POSTING_SENT_FOR_APPROVAL,
        resource="job_posting", resource_id=job.id,
        payload={"sent_by": str(actor_id), "correlation_id": correlation_id},
    )


async def approve_by_hm(
    db: AsyncSession,
    *,
    job: JobPosting,
    hm_user_id: UUID,
    correlation_id: str,
) -> None:
    """Stamp HM-approval columns and transition pending_hm_approval → active."""
    job.approved_by_hm = hm_user_id
    job.approved_at = datetime.now(UTC)
    await transition(db, job, to_state="active",
                     actor_id=hm_user_id, correlation_id=correlation_id)
    await log_event(
        db, tenant_id=job.tenant_id, actor_id=hm_user_id, actor_email=None,
        action=audit_actions.JOB_POSTING_APPROVED,
        resource="job_posting", resource_id=job.id,
        payload={
            "approved_by_hm": str(hm_user_id),
            "approved_at": job.approved_at.isoformat(),
            "correlation_id": correlation_id,
        },
    )


async def return_to_recruiter(
    db: AsyncSession,
    *,
    job: JobPosting,
    hm_user_id: UUID,
    notes: str,
    correlation_id: str,
) -> None:
    """Record notes and transition pending_hm_approval → pending_recruiter_revision."""
    job.revision_notes = notes
    await transition(db, job, to_state="pending_recruiter_revision",
                     actor_id=hm_user_id, correlation_id=correlation_id)
    await log_event(
        db, tenant_id=job.tenant_id, actor_id=hm_user_id, actor_email=None,
        action=audit_actions.JOB_POSTING_RETURNED_TO_RECRUITER,
        resource="job_posting", resource_id=job.id,
        payload={"returned_by": str(hm_user_id), "notes": notes,
                 "correlation_id": correlation_id},
    )


async def resend_for_approval(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
    correlation_id: str,
) -> None:
    """Clear revision notes and transition pending_recruiter_revision → pending_hm_approval."""
    job.revision_notes = None
    await transition(db, job, to_state="pending_hm_approval",
                     actor_id=actor_id, correlation_id=correlation_id)
    await log_event(
        db, tenant_id=job.tenant_id, actor_id=actor_id, actor_email=None,
        action=audit_actions.JOB_POSTING_SENT_FOR_APPROVAL,
        resource="job_posting", resource_id=job.id,
        payload={"sent_by": str(actor_id), "resend": True,
                 "correlation_id": correlation_id},
    )
```

(`from datetime import UTC, datetime` is already imported.)

- [ ] **Step 5: Run tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_state_transitions_integration.py -v -k "approval or return or resend"
```

Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/jd/service.py backend/nexus/tests/test_jd_state_transitions_integration.py backend/nexus/tests/conftest.py
git commit -m "feat(jd): handoff service helpers (send/approve/return/resend)"
```

---

### Task 12: Branch `create_job_posting` on caller authority

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`
- Modify: `backend/nexus/app/modules/jd/router.py:296-355`

- [ ] **Step 1: Modify `create_job_posting` to accept `created_by_role` + initial state**

Find `create_job_posting` in `service.py` (around line 139) and replace its signature and body to accept and store `created_by_role` and initial `status`:

```python
async def create_job_posting(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    created_by: UUID,
    org_unit_id: UUID,
    title: str,
    description_raw: str,
    project_scope_raw: str | None = None,
    target_headcount: int | None = None,
    deadline: date | None = None,
    employment_type: str | None = None,
    work_arrangement: str | None = None,
    location: str | None = None,
    salary_range_min: int | None = None,
    salary_range_max: int | None = None,
    salary_currency: str | None = None,
    travel_required: str | None = None,
    start_date_pref: str | None = None,
    correlation_id: str,
    # NEW PARAMS:
    created_by_role: str = "recruiter",
    initial_status: str = "draft",
    assigned_recruiter_id: UUID | None = None,
    claimed_at_now: bool = True,
) -> JobPosting:
    # ... existing company-profile-ancestry validation stays as-is ...

    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title=title,
        description_raw=description_raw,
        project_scope_raw=project_scope_raw,
        status=initial_status,
        created_by=created_by,
        created_by_role=created_by_role,
        target_headcount=target_headcount,
        deadline=deadline,
        employment_type=employment_type,
        work_arrangement=work_arrangement,
        location=location,
        salary_range_min=salary_range_min,
        salary_range_max=salary_range_max,
        salary_currency=salary_currency,
        travel_required=travel_required,
        start_date_pref=start_date_pref,
        assigned_recruiter_id=assigned_recruiter_id,
        claimed_at=func.now() if (assigned_recruiter_id and claimed_at_now) else None,
    )
    db.add(job)
    await db.flush()

    # Existing audit hook stays; add created_by_role to payload.
    # ... existing audit log_event call, with payload={"created_by_role": created_by_role, ...}
    return job
```

- [ ] **Step 2: Modify `POST /api/jobs` route to branch on authority**

Replace the route handler `create_job` in `app/modules/jd/router.py` (around line 297):

```python
from app.modules.jd.authz import derive_jd_authority, has_admin_override


@router.post("", status_code=201, response_model=JobPostingWithSnapshot)
async def create_job(
    body: JobPostingCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    correlation_id = _get_correlation_id(request)
    ancestry = await get_org_unit_ancestry(db, body.org_unit_id)
    authority = derive_jd_authority(user, ancestry)
    if authority is None:
        raise HTTPException(
            status_code=403,
            detail="Missing jobs.create or jobs.manage in ancestry",
        )

    if authority == "recruiter":
        # Admin override implies recruiter authority anyway; created_by_role
        # is 'admin' if the caller has the override path, else 'recruiter'.
        created_by_role = "admin" if has_admin_override(user, ancestry) else "recruiter"
        job = await create_job_posting(
            db,
            tenant_id=user.user.tenant_id,
            created_by=user.user.id,
            org_unit_id=body.org_unit_id,
            title=body.title,
            description_raw=body.description_raw,
            project_scope_raw=body.project_scope_raw,
            target_headcount=body.target_headcount,
            deadline=body.deadline,
            employment_type=body.employment_type,
            work_arrangement=body.work_arrangement,
            location=body.location,
            salary_range_min=body.salary_range_min,
            salary_range_max=body.salary_range_max,
            salary_currency=body.salary_currency,
            travel_required=body.travel_required,
            start_date_pref=body.start_date_pref,
            correlation_id=correlation_id,
            created_by_role=created_by_role,
            initial_status="draft",
            assigned_recruiter_id=user.user.id,
            claimed_at_now=True,
        )
        background_tasks.add_task(
            _safe_dispatch_extraction,
            job_posting_id=str(job.id),
            tenant_id=str(user.user.tenant_id),
            correlation_id=correlation_id,
        )
    else:
        # 'hm' authority — JD lands in pending_recruiter, no Dramatiq dispatch.
        job = await create_job_posting(
            db,
            tenant_id=user.user.tenant_id,
            created_by=user.user.id,
            org_unit_id=body.org_unit_id,
            title=body.title,
            description_raw=body.description_raw,
            project_scope_raw=body.project_scope_raw,
            target_headcount=body.target_headcount,
            deadline=body.deadline,
            employment_type=body.employment_type,
            work_arrangement=body.work_arrangement,
            location=body.location,
            salary_range_min=body.salary_range_min,
            salary_range_max=body.salary_range_max,
            salary_currency=body.salary_currency,
            travel_required=body.travel_required,
            start_date_pref=body.start_date_pref,
            correlation_id=correlation_id,
            created_by_role="hm",
            initial_status="pending_recruiter",
            assigned_recruiter_id=None,
            claimed_at_now=False,
        )

    return JobPostingWithSnapshot(
        # ... existing payload assembly ...
    )
```

- [ ] **Step 3: Run JD router tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_router.py -v
```

Expected: existing tests PASS (the existing recruiter path is unchanged behaviorally).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/jd/service.py backend/nexus/app/modules/jd/router.py
git commit -m "feat(jd): branch POST /api/jobs on caller authority"
```

---

## Phase 5 — New endpoints

### Task 13: `POST /api/jobs/{id}/claim` route

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py`

- [ ] **Step 1: Implement the route**

Append after the `create_job` route in `router.py`:

```python
@router.post("/{job_id}/claim", status_code=200, response_model=JobPostingWithSnapshot)
async def claim_job(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    correlation_id = _get_correlation_id(request)
    # Load job to compute ancestry (existence + tenant scope checked by RLS).
    job_lookup = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job_row = job_lookup.scalar_one_or_none()
    if job_row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    ancestry = await get_org_unit_ancestry(db, job_row.org_unit_id)
    authority = derive_jd_authority(user, ancestry)
    if authority != "recruiter":
        raise HTTPException(status_code=403, detail="Missing jobs.manage in ancestry")

    job = await claim_job_for_recruiter(
        db, job_id=job_id, recruiter_id=user.user.id, correlation_id=correlation_id,
    )
    background_tasks.add_task(
        _safe_dispatch_extraction,
        job_posting_id=str(job.id),
        tenant_id=str(user.user.tenant_id),
        correlation_id=correlation_id,
    )
    # Re-use existing helper to assemble the full snapshot response.
    return await _job_to_full_response(db, job)
```

(If `_job_to_full_response` doesn't exist, refactor the existing `create_job`'s response-assembly into one. Search router.py for its inline payload construction.)

- [ ] **Step 2: Add an integration test**

Create `backend/nexus/tests/test_jd_handoff_integration.py`:

```python
"""End-to-end HM → claim → approve happy path + revision loop."""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_claim_endpoint_happy_path(authed_hm_client, authed_recruiter_client, recruiters_unit_id):
    # HM raises a req.
    resp = await authed_hm_client.post("/api/jobs", json={
        "org_unit_id": str(recruiters_unit_id),
        "title": "Senior Backend Engineer",
        "description_raw": "We need a backend engineer with payments experience.",
    })
    assert resp.status_code == 201
    job_id = resp.json()["id"]
    assert resp.json()["status"] == "pending_recruiter"

    # Recruiter claims.
    claim = await authed_recruiter_client.post(f"/api/jobs/{job_id}/claim")
    assert claim.status_code == 200
    body = claim.json()
    assert body["status"] == "draft"
    assert body["assigned_recruiter_id"] is not None
```

(Assumes test fixtures `authed_hm_client`, `authed_recruiter_client`, `recruiters_unit_id` — add them to `tests/conftest.py` if not present, or refactor existing JWT-mocking fixtures in this style.)

- [ ] **Step 3: Run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_handoff_integration.py -v -k "claim_endpoint"
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_handoff_integration.py
git commit -m "feat(jd): POST /api/jobs/{id}/claim endpoint"
```

---

### Task 14: `POST /api/jobs/{id}/send-for-approval`, `/approve`, `/return-to-recruiter`, `/resend-for-approval`

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py`
- Modify: `backend/nexus/app/modules/jd/schemas.py`

- [ ] **Step 1: Add request schema for return-to-recruiter**

Append to `app/modules/jd/schemas.py`:

```python
class ReturnToRecruiterRequest(BaseModel):
    notes: str = Field(..., min_length=1, max_length=2000)
```

- [ ] **Step 2: Implement the four routes**

Append to `app/modules/jd/router.py`:

```python
from app.modules.jd.service import (
    send_for_hm_approval,
    approve_by_hm,
    return_to_recruiter,
    resend_for_approval,
)
from app.modules.jd.schemas import ReturnToRecruiterRequest


@router.post("/{job_id}/send-for-approval", status_code=200, response_model=JobPostingWithSnapshot)
async def send_for_approval(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    correlation_id = _get_correlation_id(request)
    job = await _load_job(db, job_id)
    ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
    authority = derive_jd_authority(user, ancestry)
    override = has_admin_override(user, ancestry)
    if authority != "recruiter" and not override:
        raise HTTPException(status_code=403, detail="Recruiter authority required")
    # Assigned recruiter or override only.
    if not override and job.assigned_recruiter_id != user.user.id:
        raise HTTPException(status_code=403, detail="Not the assigned recruiter")

    await send_for_hm_approval(db, job=job, actor_id=user.user.id, correlation_id=correlation_id)
    return await _job_to_full_response(db, job)


@router.post("/{job_id}/approve", status_code=200, response_model=JobPostingWithSnapshot)
async def approve(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    correlation_id = _get_correlation_id(request)
    job = await _load_job(db, job_id)
    ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
    authority = derive_jd_authority(user, ancestry)
    override = has_admin_override(user, ancestry)
    if authority != "hm" and not override:
        raise HTTPException(status_code=403, detail="HM authority required")

    await approve_by_hm(db, job=job, hm_user_id=user.user.id, correlation_id=correlation_id)
    return await _job_to_full_response(db, job)


@router.post("/{job_id}/return-to-recruiter", status_code=200, response_model=JobPostingWithSnapshot)
async def return_to_recr(
    job_id: UUID,
    body: ReturnToRecruiterRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    correlation_id = _get_correlation_id(request)
    job = await _load_job(db, job_id)
    ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
    authority = derive_jd_authority(user, ancestry)
    override = has_admin_override(user, ancestry)
    if authority != "hm" and not override:
        raise HTTPException(status_code=403, detail="HM authority required")

    await return_to_recruiter(
        db, job=job, hm_user_id=user.user.id,
        notes=body.notes, correlation_id=correlation_id,
    )
    return await _job_to_full_response(db, job)


@router.post("/{job_id}/resend-for-approval", status_code=200, response_model=JobPostingWithSnapshot)
async def resend(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    correlation_id = _get_correlation_id(request)
    job = await _load_job(db, job_id)
    ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
    authority = derive_jd_authority(user, ancestry)
    override = has_admin_override(user, ancestry)
    if authority != "recruiter" and not override:
        raise HTTPException(status_code=403, detail="Recruiter authority required")
    if not override and job.assigned_recruiter_id != user.user.id:
        raise HTTPException(status_code=403, detail="Not the assigned recruiter")

    await resend_for_approval(db, job=job, actor_id=user.user.id, correlation_id=correlation_id)
    return await _job_to_full_response(db, job)


async def _load_job(db: AsyncSession, job_id: UUID) -> JobPosting:
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
```

- [ ] **Step 3: Add an end-to-end happy-path integration test**

Append to `tests/test_jd_handoff_integration.py`:

```python
@pytest.mark.asyncio
async def test_full_hm_to_approve_loop(authed_hm_client, authed_recruiter_client, recruiters_unit_id, db):
    # 1. HM raises.
    resp = await authed_hm_client.post("/api/jobs", json={
        "org_unit_id": str(recruiters_unit_id),
        "title": "Senior Backend Engineer",
        "description_raw": "Brief.",
    })
    job_id = resp.json()["id"]

    # 2. Recruiter claims.
    await authed_recruiter_client.post(f"/api/jobs/{job_id}/claim")

    # 3. Force-state-machine the JD to pipeline_built (skipping AI for test speed).
    from app.models import JobPosting
    from sqlalchemy import update
    await db.execute(update(JobPosting).where(JobPosting.id == job_id).values(status="pipeline_built"))
    await db.commit()

    # 4. Recruiter sends for approval.
    r = await authed_recruiter_client.post(f"/api/jobs/{job_id}/send-for-approval")
    assert r.status_code == 200
    assert r.json()["status"] == "pending_hm_approval"

    # 5. HM approves.
    a = await authed_hm_client.post(f"/api/jobs/{job_id}/approve")
    assert a.status_code == 200
    assert a.json()["status"] == "active"
    assert a.json()["approved_by_hm"] is not None


@pytest.mark.asyncio
async def test_return_then_resend_loop(authed_hm_client, authed_recruiter_client, recruiters_unit_id, db):
    # 1. HM raises.
    raise_resp = await authed_hm_client.post("/api/jobs", json={
        "org_unit_id": str(recruiters_unit_id),
        "title": "Engineer",
        "description_raw": "Brief.",
    })
    job_id = raise_resp.json()["id"]

    # 2. Recruiter claims.
    await authed_recruiter_client.post(f"/api/jobs/{job_id}/claim")

    # 3. Force pipeline_built (skip AI for test speed).
    from app.models import JobPosting
    from sqlalchemy import update
    await db.execute(update(JobPosting).where(JobPosting.id == job_id).values(status="pipeline_built"))
    await db.commit()

    # 4. Recruiter sends for approval.
    await authed_recruiter_client.post(f"/api/jobs/{job_id}/send-for-approval")

    # 5. HM returns with notes.
    ret = await authed_hm_client.post(
        f"/api/jobs/{job_id}/return-to-recruiter",
        json={"notes": "Needs payments-domain experience."},
    )
    assert ret.status_code == 200
    body = ret.json()
    assert body["status"] == "pending_recruiter_revision"
    assert "payments" in body["revision_notes"]

    # 6. Recruiter re-sends.
    re = await authed_recruiter_client.post(f"/api/jobs/{job_id}/resend-for-approval")
    assert re.status_code == 200
    body = re.json()
    assert body["status"] == "pending_hm_approval"
    assert body["revision_notes"] is None
```

- [ ] **Step 4: Run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_handoff_integration.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/router.py backend/nexus/app/modules/jd/schemas.py backend/nexus/tests/test_jd_handoff_integration.py
git commit -m "feat(jd): send/approve/return/resend approval endpoints"
```

---

## Phase 6 — List filters + response schema

### Task 15: Extend list filters and response schema

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py:230-280` (the list endpoint)
- Modify: `backend/nexus/app/modules/jd/schemas.py:171-225`

- [ ] **Step 1: Add new fields to response schemas**

Find `JobPostingSummary` and `JobPostingWithSnapshot` in `schemas.py` and add:

```python
    created_by_role: str = "recruiter"
    assigned_recruiter_id: UUID | None = None
    assigned_recruiter_email: str | None = None
    claimed_at: datetime | None = None
    approved_by_hm: UUID | None = None
    approved_by_hm_email: str | None = None
    approved_at: datetime | None = None
    revision_notes: str | None = None
```

- [ ] **Step 2: Update `enrich_job_summaries` to populate the new fields**

Find `enrich_job_summaries` in `service.py` and extend the user_emails join to also resolve `assigned_recruiter_id` and `approved_by_hm` emails. Pass the new fields through to `_job_to_summary`.

- [ ] **Step 3: Add list query filters**

Modify the `list_jobs` route in `router.py` to accept query params:

```python
@router.get("", response_model=list[JobPostingSummary])
async def list_jobs(
    request: Request,
    status: str | None = None,
    unclaimed: bool = False,
    assigned_to: str | None = None,   # 'me' or a user_id
    created_by: str | None = None,    # 'me' or a user_id
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[JobPostingSummary]:
    stmt = select(JobPosting)
    if status:
        stmt = stmt.where(JobPosting.status == status)
    if unclaimed:
        stmt = stmt.where(
            JobPosting.assigned_recruiter_id.is_(None),
            JobPosting.status == "pending_recruiter",
        )
    if assigned_to == "me":
        stmt = stmt.where(JobPosting.assigned_recruiter_id == user.user.id)
    elif assigned_to:
        stmt = stmt.where(JobPosting.assigned_recruiter_id == UUID(assigned_to))
    if created_by == "me":
        stmt = stmt.where(JobPosting.created_by == user.user.id)
    elif created_by:
        stmt = stmt.where(JobPosting.created_by == UUID(created_by))

    # Ancestry-based visibility filter — keep existing logic.
    visible_unit_ids = await _visible_unit_ids(user, "jobs.view")
    stmt = stmt.where(JobPosting.org_unit_id.in_(visible_unit_ids))
    stmt = stmt.order_by(JobPosting.created_at.desc())

    result = await db.execute(stmt)
    jobs = list(result.scalars().all())
    return await enrich_job_summaries(jobs, db)
```

- [ ] **Step 4: Run JD router tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_router.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/router.py backend/nexus/app/modules/jd/schemas.py backend/nexus/app/modules/jd/service.py
git commit -m "feat(jd): list filters (unclaimed/assigned_to/created_by) + new response fields"
```

---

## Phase 7 — Edit-rights enforcement on existing mutation routes

### Task 16: Apply `require_edit_rights` to signal mutation routes

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py` (signal save / re-enrich / confirm)

- [ ] **Step 1: Wrap each signal-mutating route with the helper**

Find each route in `router.py` that touches signals (search for `save_signals`, `confirm_signals`, the re-enrich trigger). Inside each, after loading `job` and computing `ancestry`, add:

```python
    authority = derive_jd_authority(user, ancestry)
    override = has_admin_override(user, ancestry)
    require_edit_rights(job, user, authority=authority, admin_override=override, artifact="signals")
```

- [ ] **Step 2: Run signal-related tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_signals.py tests/test_jd_signals.py -v
```

Expected: existing tests PASS (recruiter authority on draft / signals_extracted etc. is allowed by the matrix).

- [ ] **Step 3: Add HM-edit-during-pending-hm-approval test**

Append to `test_jd_handoff_integration.py`:

```python
@pytest.mark.asyncio
async def test_hm_can_edit_signals_in_pending_hm_approval(authed_hm_client, _seeded_job_pending_hm_approval):
    job_id = _seeded_job_pending_hm_approval.id
    resp = await authed_hm_client.post(f"/api/jobs/{job_id}/signals", json={
        "signals": [{"text": "x", "weight": 3, "source": "manual"}],
        "expected_version": 1,
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recruiter_cannot_edit_signals_during_hm_approval(
    authed_recruiter_client, _seeded_job_pending_hm_approval
):
    job_id = _seeded_job_pending_hm_approval.id
    resp = await authed_recruiter_client.post(f"/api/jobs/{job_id}/signals", json={
        "signals": [{"text": "x", "weight": 3, "source": "manual"}],
        "expected_version": 1,
    })
    assert resp.status_code == 403
```

- [ ] **Step 4: Run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_handoff_integration.py -v -k "edit_signals"
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_handoff_integration.py
git commit -m "feat(jd): enforce edit-rights matrix on signal mutation routes"
```

---

### Task 17: Apply `require_edit_rights` to pipeline mutation routes

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/router.py`

- [ ] **Step 1: Wrap pipeline-instance mutation routes**

Identify the routes that mutate a pipeline instance (search `pipelines/router.py` for `instance` mutation handlers). For each, after loading the `JobPosting` for the instance, add the same `require_edit_rights(..., artifact="pipeline")` block.

The existing `require_template_access` doesn't help here — that's for templates. Instance mutations need the JD-state matrix.

- [ ] **Step 2: Run pipeline tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_pipelines_service.py tests/test_pipelines_router.py -v
```

Expected: existing tests PASS.

- [ ] **Step 3: Add a regression test that recruiter cannot reorder pipeline stages during pending_hm_approval**

Add to `test_jd_handoff_integration.py`:

```python
@pytest.mark.asyncio
async def test_recruiter_cannot_reorder_pipeline_during_hm_approval(
    authed_recruiter_client, _seeded_job_pending_hm_approval
):
    job_id = _seeded_job_pending_hm_approval.id
    resp = await authed_recruiter_client.post(
        f"/api/jobs/{job_id}/pipeline/reorder",
        json={"stage_ids": []},
    )
    assert resp.status_code == 403
```

- [ ] **Step 4: Run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_handoff_integration.py -v -k "pipeline"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/pipelines/router.py backend/nexus/tests/test_jd_handoff_integration.py
git commit -m "feat(jd): enforce edit-rights matrix on pipeline mutation routes"
```

---

### Task 18: Apply `require_edit_rights` to question-bank mutation routes

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py`

- [ ] **Step 1: Wrap QB mutation routes**

Same pattern as Task 16/17. Each route that mutates a question bank or question gets the edit-rights check with `artifact="question_bank"`.

- [ ] **Step 2: Run QB tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_question_banks_router.py tests/test_question_banks_service.py -v
```

Expected: existing tests PASS.

- [ ] **Step 3: Add a regression test**

```python
@pytest.mark.asyncio
async def test_hm_can_edit_question_bank_during_hm_approval(
    authed_hm_client, _seeded_job_with_qb_pending_hm_approval, db
):
    """Resolve the JD's first stage's question bank, then assert the HM
    can append a question while in pending_hm_approval."""
    job = _seeded_job_with_qb_pending_hm_approval
    # Pull the first stage_question_bank for this job's pipeline instance.
    from app.models import JobPipelineInstance, JobPipelineStage, StageQuestionBank
    instance_q = await db.execute(
        select(JobPipelineInstance).where(JobPipelineInstance.job_posting_id == job.id)
    )
    instance = instance_q.scalar_one()
    stage_q = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.instance_id == instance.id).limit(1)
    )
    stage = stage_q.scalar_one()
    bank_q = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
    )
    bank = bank_q.scalar_one()

    resp = await authed_hm_client.post(
        f"/api/jobs/{job.id}/banks/{bank.id}/questions",
        json={"text": "Tell me about a time you debugged a payments outage.",
              "kind": "behavioral", "difficulty": 3},
    )
    assert resp.status_code in (200, 201)


@pytest.mark.asyncio
async def test_recruiter_cannot_edit_question_bank_during_hm_approval(
    authed_recruiter_client, _seeded_job_with_qb_pending_hm_approval, db
):
    """Symmetric negative case — recruiter is locked out during HM review."""
    job = _seeded_job_with_qb_pending_hm_approval
    from app.models import JobPipelineInstance, JobPipelineStage, StageQuestionBank
    instance_q = await db.execute(
        select(JobPipelineInstance).where(JobPipelineInstance.job_posting_id == job.id)
    )
    instance = instance_q.scalar_one()
    stage_q = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.instance_id == instance.id).limit(1)
    )
    stage = stage_q.scalar_one()
    bank_q = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
    )
    bank = bank_q.scalar_one()

    resp = await authed_recruiter_client.post(
        f"/api/jobs/{job.id}/banks/{bank.id}/questions",
        json={"text": "x", "kind": "behavioral", "difficulty": 3},
    )
    assert resp.status_code == 403
```

(`_seeded_job_with_qb_pending_hm_approval` is a fixture that builds on `_seeded_job_pending_hm_approval` and additionally seeds a pipeline instance + at least one stage + one stage_question_bank. Add it to `tests/conftest.py` alongside the others.)

- [ ] **Step 4: Run + commit**

```bash
git add backend/nexus/app/modules/question_bank/router.py backend/nexus/tests/test_jd_handoff_integration.py
git commit -m "feat(jd): enforce edit-rights matrix on question-bank routes"
```

---

## Phase 8 — Notifications

### Task 19: Wire HM-handoff notification events

**Files:**
- Modify: `backend/nexus/app/modules/notifications/service.py`
- Create: `backend/nexus/app/modules/notifications/templates/req_raised.html`
- Create: `backend/nexus/app/modules/notifications/templates/req_claimed.html`
- Create: `backend/nexus/app/modules/notifications/templates/req_sent_for_approval.html`
- Create: `backend/nexus/app/modules/notifications/templates/req_approved.html`
- Create: `backend/nexus/app/modules/notifications/templates/req_returned_to_recruiter.html`
- Create: `backend/nexus/app/modules/notifications/templates/req_published.html`

- [ ] **Step 1: Add the templates**

Each template is a minimal subject + HTML body following the existing `team_invite.html` pattern. The body has the JD title, the actor name, and a link to the JD detail page (use `settings.frontend_base_url`). Keep them under 30 lines each.

- [ ] **Step 2: Define dispatcher functions in `notifications/service.py`**

Append:

```python
async def notify_req_raised(
    *, db: AsyncSession, job: JobPosting, recipients: list[User], capped_at: int = 20
) -> None:
    for u in recipients[:capped_at]:
        html = render_template("req_raised.html", job=job, recipient=u)
        await send_email(
            to=u.email,
            subject=f"New requisition in your queue: {job.title}",
            html=html,
        )


async def notify_req_claimed(*, db: AsyncSession, job: JobPosting, hm: User, recruiter: User) -> None:
    html = render_template("req_claimed.html", job=job, recipient=hm, recruiter=recruiter)
    await send_email(to=hm.email, subject=f"{recruiter.email} accepted your req: {job.title}", html=html)


async def notify_req_sent_for_approval(*, db: AsyncSession, job: JobPosting, hm: User, recruiter: User) -> None:
    html = render_template("req_sent_for_approval.html", job=job, recipient=hm, recruiter=recruiter)
    await send_email(to=hm.email, subject=f"Approval requested: {job.title}", html=html)


async def notify_req_approved(*, db: AsyncSession, job: JobPosting, hm: User, recruiter: User) -> None:
    html = render_template("req_approved.html", job=job, recipient=recruiter, hm=hm)
    await send_email(to=recruiter.email, subject=f"{hm.email} approved: {job.title}", html=html)


async def notify_req_returned_to_recruiter(
    *, db: AsyncSession, job: JobPosting, hm: User, recruiter: User
) -> None:
    html = render_template("req_returned_to_recruiter.html", job=job, recipient=recruiter, hm=hm)
    await send_email(to=recruiter.email, subject=f"Changes requested: {job.title}", html=html)


async def notify_req_published(*, db: AsyncSession, job: JobPosting, hm: User) -> None:
    html = render_template("req_published.html", job=job, recipient=hm)
    await send_email(to=hm.email, subject=f"Now live: {job.title}", html=html)
```

- [ ] **Step 3: Wire into JD router handlers**

In each of the new endpoint handlers (`create_job` HM path, `claim_job`, `send_for_approval`, `approve`, `return_to_recr`, `resend`), add a `BackgroundTasks` dispatch after the response payload is computed. Use the existing `BackgroundTasks` parameter pattern.

For `req.raised`: the recipient list is computed by querying users with `jobs.manage` on the JD's ancestry (cap at 20). Helper:

```python
async def _resolve_req_raised_recipients(
    db: AsyncSession, job: JobPosting, ancestry: list[OrganizationalUnit], cap: int = 20
) -> list[User]:
    """Return active users with jobs.manage permission anywhere in the
    JD's ancestry, capped at `cap` by created_at ascending (longest-tenured first)."""
    from app.models import Role, User, UserRoleAssignment
    ancestry_ids = [u.id for u in ancestry]
    result = await db.execute(
        select(User)
        .distinct()
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .join(Role, UserRoleAssignment.role_id == Role.id)
        .where(
            UserRoleAssignment.org_unit_id.in_(ancestry_ids),
            Role.permissions.op("@>")(['jobs.manage']),  # JSONB contains
            User.is_active == True,
        )
        .order_by(User.created_at.asc())
        .limit(cap)
    )
    return list(result.scalars().all())
```

- [ ] **Step 4: Add a smoke test**

```python
@pytest.mark.asyncio
async def test_hm_create_dispatches_req_raised_notification(monkeypatch, authed_hm_client, recruiters_unit_id):
    sent = []
    async def fake_send(**kw):
        sent.append(kw)
    monkeypatch.setattr("app.modules.notifications.service.send_email", fake_send)

    await authed_hm_client.post("/api/jobs", json={
        "org_unit_id": str(recruiters_unit_id),
        "title": "X",
        "description_raw": "raw",
    })
    assert any("requisition in your queue" in s["subject"] for s in sent)
```

- [ ] **Step 5: Run + commit**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_jd_handoff_integration.py -v
git add backend/nexus/app/modules/notifications/ backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_handoff_integration.py
git commit -m "feat(jd): wire 6 HM/Recruiter handoff notification events"
```

---

## Phase 9 — Frontend

### Task 20: Frontend authority hook + API client extensions

**Files:**
- Create: `frontend/app/lib/hooks/use-jd-authority.ts`
- Modify: `frontend/app/lib/api/jobs.ts`

- [ ] **Step 1: Add API client methods**

Append to `lib/api/jobs.ts`:

```typescript
export interface JobPostingDetail extends JobPostingSummary {
  // existing fields…
  created_by_role: 'hm' | 'recruiter' | 'admin'
  assigned_recruiter_id: string | null
  assigned_recruiter_email: string | null
  claimed_at: string | null
  approved_by_hm: string | null
  approved_by_hm_email: string | null
  approved_at: string | null
  revision_notes: string | null
}

export const jobsApi = {
  // existing entries…
  claim: (token: string, jobId: string) =>
    apiFetch<JobPostingDetail>(`/api/jobs/${jobId}/claim`, { method: 'POST', token }),
  sendForApproval: (token: string, jobId: string) =>
    apiFetch<JobPostingDetail>(`/api/jobs/${jobId}/send-for-approval`, { method: 'POST', token }),
  approve: (token: string, jobId: string) =>
    apiFetch<JobPostingDetail>(`/api/jobs/${jobId}/approve`, { method: 'POST', token }),
  returnToRecruiter: (token: string, jobId: string, notes: string) =>
    apiFetch<JobPostingDetail>(`/api/jobs/${jobId}/return-to-recruiter`, {
      method: 'POST', token, body: JSON.stringify({ notes }),
    }),
  resend: (token: string, jobId: string) =>
    apiFetch<JobPostingDetail>(`/api/jobs/${jobId}/resend-for-approval`, { method: 'POST', token }),
}
```

- [ ] **Step 2: Add the authority hook**

Create `lib/hooks/use-jd-authority.ts`:

```typescript
'use client'

import type { MeResponse } from '@/lib/api/auth'
import type { OrgUnit } from '@/lib/api/org-units'

export type JdAuthority = 'recruiter' | 'hm' | null

/**
 * Frontend mirror of derive_jd_authority(). Computes the caller's
 * authority on a JD anchored at the given ancestry. Pure function —
 * no API calls.
 */
export function deriveJdAuthority(
  me: MeResponse | null | undefined,
  ancestry: OrgUnit[],
): JdAuthority {
  if (!me) return null
  if (me.is_super_admin) return 'recruiter'
  const ancestryIds = new Set(ancestry.map((u) => u.id))
  const hasManage = me.assignments.some(
    (a) => ancestryIds.has(a.org_unit_id) && a.permissions.includes('jobs.manage'),
  )
  if (hasManage) return 'recruiter'
  const hasCreate = me.assignments.some(
    (a) => ancestryIds.has(a.org_unit_id) && a.permissions.includes('jobs.create'),
  )
  return hasCreate ? 'hm' : null
}

export function hasAdminOverride(
  me: MeResponse | null | undefined,
  ancestry: OrgUnit[],
): boolean {
  if (!me) return false
  if (me.is_super_admin) return true
  const ancestryIds = new Set(ancestry.map((u) => u.id))
  return me.assignments.some(
    (a) => ancestryIds.has(a.org_unit_id) && a.role_name === 'Admin',
  )
}
```

- [ ] **Step 3: Add unit tests**

Create `frontend/app/tests/hooks/use-jd-authority.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { deriveJdAuthority, hasAdminOverride } from '@/lib/hooks/use-jd-authority'

const me = (overrides: Partial<any> = {}): any => ({
  user_id: 'u', email: 'x@x', full_name: null, tenant_id: 't',
  client_name: 'c', is_super_admin: false, onboarding_complete: true,
  has_org_units: true, assignments: [],
  ...overrides,
})
const ou = (id: string) => ({ id, name: id, parent_unit_id: null } as any)

describe('deriveJdAuthority', () => {
  it('returns null when user has no permissions on ancestry', () => {
    expect(deriveJdAuthority(me(), [ou('a')])).toBeNull()
  })

  it('returns recruiter for super admin', () => {
    expect(deriveJdAuthority(me({ is_super_admin: true }), [ou('a')])).toBe('recruiter')
  })

  it('returns recruiter when jobs.manage in ancestry', () => {
    const u = me({
      assignments: [{ org_unit_id: 'a', org_unit_name: 'a', role_name: 'Recruiter',
                      permissions: ['jobs.create', 'jobs.manage'] }],
    })
    expect(deriveJdAuthority(u, [ou('a')])).toBe('recruiter')
  })

  it('returns hm when only jobs.create in ancestry', () => {
    const u = me({
      assignments: [{ org_unit_id: 'a', org_unit_name: 'a', role_name: 'Hiring Manager',
                      permissions: ['jobs.create'] }],
    })
    expect(deriveJdAuthority(u, [ou('a')])).toBe('hm')
  })
})

describe('hasAdminOverride', () => {
  it('true for super admin', () => {
    expect(hasAdminOverride(me({ is_super_admin: true }), [ou('a')])).toBe(true)
  })

  it('true for Admin role on ancestry', () => {
    const u = me({
      assignments: [{ org_unit_id: 'a', org_unit_name: 'a', role_name: 'Admin',
                      permissions: ['jobs.manage'] }],
    })
    expect(hasAdminOverride(u, [ou('a')])).toBe(true)
  })

  it('false for Recruiter even with same perms', () => {
    const u = me({
      assignments: [{ org_unit_id: 'a', org_unit_name: 'a', role_name: 'Recruiter',
                      permissions: ['jobs.manage'] }],
    })
    expect(hasAdminOverride(u, [ou('a')])).toBe(false)
  })
})
```

- [ ] **Step 4: Run tests**

```bash
cd frontend/app && npm run test -- tests/hooks/use-jd-authority.test.ts
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/hooks/use-jd-authority.ts frontend/app/lib/api/jobs.ts frontend/app/tests/hooks/use-jd-authority.test.ts
git commit -m "feat(jd): frontend authority hook + jobs API client"
```

---

### Task 21: Mutation hooks for the lifecycle

**Files:**
- Create: `frontend/app/lib/hooks/use-claim-job.ts`
- Create: `frontend/app/lib/hooks/use-jd-approval.ts`

- [ ] **Step 1: Implement the hooks**

Each follows the existing pattern in `lib/hooks/use-assign-role.ts`: a `useMutation` that calls a method on `jobsApi` with a fresh Supabase token, then invalidates the relevant TanStack Query cache key.

```typescript
// use-claim-job.ts
'use client'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useClaimJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (jobId: string) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.claim(token, jobId)
    },
    onSuccess: (_, jobId) => {
      void qc.invalidateQueries({ queryKey: ['jobs', jobId] })
      void qc.invalidateQueries({ queryKey: ['jobs-list'] })
    },
  })
}
```

```typescript
// use-jd-approval.ts
'use client'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

function _invalidate(qc: ReturnType<typeof useQueryClient>, jobId: string) {
  void qc.invalidateQueries({ queryKey: ['jobs', jobId] })
  void qc.invalidateQueries({ queryKey: ['jobs-list'] })
}

export function useSendForApproval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (jobId: string) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.sendForApproval(token, jobId)
    },
    onSuccess: (_, jobId) => _invalidate(qc, jobId),
  })
}

export function useApprove() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (jobId: string) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.approve(token, jobId)
    },
    onSuccess: (_, jobId) => _invalidate(qc, jobId),
  })
}

export function useReturnToRecruiter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ jobId, notes }: { jobId: string; notes: string }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.returnToRecruiter(token, jobId, notes)
    },
    onSuccess: (_, { jobId }) => _invalidate(qc, jobId),
  })
}

export function useResend() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (jobId: string) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.resend(token, jobId)
    },
    onSuccess: (_, jobId) => _invalidate(qc, jobId),
  })
}
```

- [ ] **Step 2: Sanity-check by importing in a scratch file**

```bash
cd frontend/app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/lib/hooks/use-claim-job.ts frontend/app/lib/hooks/use-jd-approval.ts
git commit -m "feat(jd): TanStack mutation hooks for claim + approval lifecycle"
```

---

### Task 22: HM "Raise a req" form (auto-detect mode in `/jobs/new`)

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/new/page.tsx`
- Test: `frontend/app/tests/jobs/raise-form.test.tsx`

- [ ] **Step 1: Branch the page on caller authority**

In `jobs/new/page.tsx`:
- Use `useMe()` and `useOrgUnits()`. For the user-selected target org unit, compute `ancestry` via the existing tree walk.
- Call `deriveJdAuthority(me, ancestry)`.
- If `'recruiter'`: render the existing recruiter create wizard (no change).
- If `'hm'`: render a simpler form with title, target unit, brief (raw description), optional project scope, optional headcount/deadline.
- On submit: same `POST /api/jobs` either way — the backend branches on caller authority server-side.

- [ ] **Step 2: Add a vitest covering the HM mode**

```typescript
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { renderWithProviders } from '../_utils/render'
import NewJobPage from '@/app/(dashboard)/jobs/new/page'

describe('NewJobPage in HM mode', () => {
  it('renders the simpler form for an HM user', async () => {
    // Mock useMe to return an HM-only user.
    // Mock useOrgUnits to return a tree.
    renderWithProviders(<NewJobPage />)
    // Assert: form has "Raw description" textarea, no signal-extraction step.
    expect(await screen.findByLabelText(/raw description/i)).toBeInTheDocument()
    expect(screen.queryByText(/build pipeline/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run lint + tests**

```bash
cd frontend/app && npm run test -- tests/jobs/raise-form.test.tsx && npm run lint
```

Expected: 1 PASS, lint baseline.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/jobs/new/page.tsx frontend/app/tests/jobs/raise-form.test.tsx
git commit -m "feat(jd): HM raise-a-req form — auto-detect mode in /jobs/new"
```

---

### Task 23: Recruiter unclaimed-queue + tabs on `/jobs`

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/page.tsx`

- [ ] **Step 1: Add tabs**

Three tabs on the jobs list page:
- **Unclaimed** — `?unclaimed=true&status=pending_recruiter` — visible only when `deriveJdAuthority` is `'recruiter'` for at least one of the user's units.
- **My active reqs** — `?assigned_to=me`.
- **All** — no filter (default).

Each row in the **Unclaimed** tab has an "Accept" button that calls `useClaimJob()`. On success, the row disappears (cache invalidation) and a toast appears: "Claimed — JD is now in draft."

- [ ] **Step 2: Add a smoke test**

Test that an HM-only user does not see the **Unclaimed** tab.

- [ ] **Step 3: Run + commit**

```bash
git add frontend/app/app/\(dashboard\)/jobs/page.tsx
git commit -m "feat(jd): unclaimed-queue tab + Accept on /jobs list"
```

---

### Task 24: HM approval review page

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/review/page.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/ApprovalActions.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/RevisionNotesBanner.tsx`

- [ ] **Step 1: Create `ApprovalActions.tsx`**

Renders two buttons (Approve, Return to recruiter) with a textarea modal for return-notes. Calls `useApprove` and `useReturnToRecruiter`. Visible only when:
- Job status is `pending_hm_approval`
- AND `deriveJdAuthority(me, ancestry) === 'hm'` OR `hasAdminOverride(me, ancestry)`

Also renders a "Re-send for approval" button when status is `pending_recruiter_revision` and authority is recruiter (or override).

For recruiters who self-publish: renders a "Send for HM approval" button on `pipeline_built` (alternative to the existing "Publish" button). Both call distinct mutations.

- [ ] **Step 2: Create `RevisionNotesBanner.tsx`**

Yellow banner shown at the top of the review page when status is `pending_recruiter_revision`. Reads `revision_notes` from the job. Plain text rendering (`whitespace-pre-wrap`); no `dangerouslySetInnerHTML`.

- [ ] **Step 3: Wire into review page**

In `jobs/[jobId]/review/page.tsx`, mount `RevisionNotesBanner` at the top and `ApprovalActions` in the action bar.

The page also needs to mirror the edit-rights matrix:
- If the JD is in `pending_hm_approval` and the caller is the recruiter, all editable controls render `disabled` with a banner: "Awaiting HM approval — controls are read-only until they approve or return."
- If the JD is in `pending_recruiter_revision` and the caller is the HM, same lockout but with banner: "Awaiting recruiter revision."

- [ ] **Step 4: Add component tests**

`tests/components/ApprovalActions.test.tsx`:

```typescript
// Tests:
//  - HM in pending_hm_approval: Approve + Return buttons render
//  - Recruiter in pending_hm_approval: no buttons render (read-only)
//  - Admin override: buttons render even though authority is recruiter
//  - Click Return → modal opens → submit calls useReturnToRecruiter with notes
```

- [ ] **Step 5: Run all FE tests + lint**

```bash
cd frontend/app && npm run test && npm run lint
```

Expected: all PASS, lint baseline.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/app/\(dashboard\)/jobs/\[jobId\]/review/page.tsx frontend/app/components/dashboard/jd-panels/ frontend/app/tests/components/ApprovalActions.test.tsx
git commit -m "feat(jd): HM approval review surface with edit-mode mirror"
```

---

## Phase 10 — Final verification

### Task 25: Full integration sweep

- [ ] **Step 1: Full backend test run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest -v
```

Expected: all PASS.

- [ ] **Step 2: Full frontend test + lint + type-check**

```bash
cd frontend/app && npm run test && npm run lint && npx tsc --noEmit
```

Expected: all PASS, lint at baseline.

- [ ] **Step 3: Manual end-to-end smoke**

Bring up the dev stack:

```bash
docker compose -f backend/nexus/docker-compose.yml up -d
cd frontend/app && npm run dev
```

Walk the flow described in `docs/superpowers/specs/2026-04-28-jd-hm-recruiter-handoff-design.md` Stage 5–10:

1. Log in as priya@binqle.com (HM on Recruiters team — assign via /settings/org-units first if needed).
2. Navigate to `/jobs/new`. Confirm the HM raise form renders.
3. Submit a brief. Confirm response shows `status: pending_recruiter`.
4. Log out, log in as sushant@binqle.com (Recruiter on Bangalore).
5. Navigate to `/jobs?unclaimed=true`. Confirm the req appears.
6. Click Accept. Confirm transition to draft, AI extraction starts (SSE updates).
7. Through the existing review wizard: confirm signals, build pipeline, generate question bank.
8. Click "Send for HM approval". Confirm transition to pending_hm_approval.
9. Log in as priya. Navigate to the JD. Confirm Approve + Return buttons render. Edit a signal weight. Click Approve. Confirm transition to active.

- [ ] **Step 4: Commit if any tweaks were needed**

```bash
git status  # if any incidental fixes
git add -p
git commit -m "chore(jd): final tweaks from end-to-end smoke"
```

---

## Self-review checklist (run before declaring done)

- [ ] All spec sections (§1–§16) have at least one task implementing them. Specifically: §3 (perms) → Task 3–4; §4 (state machine) → Task 6; §5 (schema) → Task 1; §6 (API) → Task 12–14; §7 (edit rights) → Task 8 + 16–18; §9 (audit) → Task 9 + 11; §9.2 (notifications) → Task 19; §10 (frontend) → Task 20–24.
- [ ] No "TODO" / "TBD" / "implement later" strings in the plan.
- [ ] No type/method-name drift between tasks (e.g., `derive_jd_authority` is used identically in Task 7, 12, 13, 14, 16, 17, 18).
- [ ] Every task ends with a commit step.
- [ ] Migration A (Task 1) runs before any code that references the new columns (Task 5+).
- [ ] Migration B (Task 3) runs before any test that asserts new permissions (Task 4 + integration tests).

---

## Out of scope (per spec §14)

These are intentionally NOT in this plan and should not creep in:

- Per-unit explicit recruiter assignment.
- Tenant-level "require HM approval before publish" toggle.
- Round-robin or capacity-based recruiter auto-assignment.
- Mid-revision AI re-extraction.
- HM cancellation of `pending_recruiter` reqs.
- Recruiter "Decline" of unclaimed reqs.
- HM editing JD post-publish (re-approval cycle).
- Restrict HM approval to the originating HM only.
- Multi-recruiter co-ownership.
- Multi-revision history.
- Mobile responsive design for the new HM raise form.
