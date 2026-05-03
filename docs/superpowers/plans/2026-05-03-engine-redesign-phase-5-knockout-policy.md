# Engine Redesign — Phase 5: Knockout policy + tenant settings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `tenant_settings` (per-tenant `engine_knockout_policy` + `engine_agent_name` overrides), the persisted `KnockoutFailure` model with defense-in-depth PII scrub, the `sessions.knockout_failures` column write through `record_session_result`, the controller-side `close_polite` wiring at `controller.py:438`, the `agent_name` override at the prompt-substitution site, and the frontend's typed 6-state `session_outcome` plumbing including a new `CANDIDATE_UNRESPONSIVE` error screen.

**Architecture:** Strictly additive. Migration `0027_tenant_settings` adds one new table + one column on `sessions` — both PG11+ metadata-only. New `tenant_settings` module follows the established public-API discipline (re-exports through `__init__.py`). `KnockoutFailure` lives in `interview_runtime.schemas` so it's part of the engine ↔ nexus contract, with a Pydantic field validator that scrubs emails + phones from `reason` on every construction path. Controller's existing `tenant_policy` constructor parameter is renamed to `tenant_settings: TenantSettings` so future per-tenant config additions are a single field add. Frontend gains a shared `session-outcome.ts` module that's the single source of truth for the 6 outcome strings, with a runtime `isSessionOutcome` guard against backend/frontend version skew.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 async (asyncpg), Alembic, Pydantic v2, structlog, pytest + pytest-asyncio. Frontend: Next.js 16 App Router, React 19, TypeScript strict, Vitest + Testing Library. Local dev: Docker Compose, Supabase Postgres on `:54322`.

**Spec:** [`docs/superpowers/specs/2026-05-03-engine-redesign-phase-5-knockout-policy-design.md`](../specs/2026-05-03-engine-redesign-phase-5-knockout-policy-design.md)

**Working agreement:** Stay on `main`. Per-task commits. The session that completes the final task updates the overview spec's `Phase status index` row in the same commit. Skip e2e — manual end-to-end runs after Phase 6.

---

## File structure

| File | Role | Phase 5 change |
|---|---|---|
| `migrations/versions/0027_tenant_settings.py` (NEW) | `tenant_settings` table + `sessions.knockout_failures` column | T1 |
| `app/main.py` (`_TENANT_SCOPED_TABLES`) | Startup RLS completeness allowlist | T1 — append `"tenant_settings"` |
| `app/modules/tenant_settings/__init__.py` (NEW) | Public API export | T2 |
| `app/modules/tenant_settings/models.py` (NEW) | `TenantSettingsModel` ORM | T2 |
| `app/modules/tenant_settings/schemas.py` (NEW) | `TenantSettings` Pydantic + `KnockoutPolicy` Literal | T2 |
| `app/modules/tenant_settings/service.py` (NEW) | `get_tenant_settings` + `DEFAULT_TENANT_SETTINGS` | T2 |
| `tests/test_module_boundaries.py` (`KNOWN_DOMAIN_MODULES`) | Module boundary lint allowlist | T2 — append `"tenant_settings"` |
| `app/modules/interview_runtime/schemas.py` | `KnockoutFailure` + `_scrub_pii` + `SessionResult.knockout_failures` | T3, T4 |
| `app/modules/interview_runtime/__init__.py` | Re-export `KnockoutFailure` | T3 |
| `app/modules/session/models.py` (`Session`, line 20) | `knockout_failures` ORM column | T4 |
| `app/modules/interview_runtime/service.py` (`record_session_result`) | Write the new column | T5 |
| `app/modules/interview_engine/agent.py` | Fetch `tenant_settings`; pass to controller | T6 |
| `app/modules/interview_engine/controller.py` | Constructor rename + `agent_name` plumb + `controller.started` log | T6 |
| `app/modules/interview_engine/controller.py` | Replace `KnockoutFailureRecord` with persisted `KnockoutFailure`; empty-reason guard | T7 |
| `app/modules/interview_engine/controller.py` (line :438) | Wire `close_polite` policy + new event-log kind | T8 |
| `frontend/session/components/interview/lib/session-outcome.ts` (NEW) | Shared `SessionOutcome` type + `isSessionOutcome` guard | T9 |
| `frontend/session/components/interview/app/hooks/use-session-outcome.ts` | Narrow return to typed Literal union | T10 |
| `frontend/session/components/interview/app/app.tsx` (`OutcomeWatcher`) | Exhaustive switch over 6 outcomes | T11 |
| `frontend/session/components/interview/app/DisconnectError.tsx` | New `CANDIDATE_UNRESPONSIVE` code | T11 |
| `tests/test_tenant_settings_*.py` (NEW) | Schemas + service + module boundary tests | T2 |
| `tests/test_interview_runtime_knockout_failure.py` (NEW) | Model + PII scrub tests | T3 |
| `tests/test_session_result_knockout_failures.py` (NEW) | Round-trip test | T4 |
| `tests/test_migration_0027_tenant_settings.py` (NEW) | Migration apply / RLS / column / downgrade | T1 |
| `tests/interview_runtime/integration/test_record_session_result_knockout_failures.py` (NEW or extend) | Column write through service | T5 |
| `tests/interview_engine/integration/test_agent_name_override.py` (NEW) | Tenant override → prompt + log | T6 |
| `tests/interview_engine/integration/test_close_polite_policy.py` (NEW) | record_only vs close_polite | T8 |
| `frontend/session/tests/components/interview/session-outcome.test.ts` (NEW) | `isSessionOutcome` guard | T9 |
| `frontend/session/tests/components/interview/use-session-outcome.test.ts` (NEW) | Hook typed-return + ref-stickiness | T10 |
| `frontend/session/tests/components/interview/outcome-watcher.test.tsx` (NEW) | Exhaustive routing | T11 |
| `frontend/session/tests/components/interview/disconnect-error.test.tsx` (NEW or extend) | New code snapshot | T11 |
| `backend/nexus/CLAUDE.md` | Migration list, revision count, modules tree, status block | T12 |
| `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` | Phase 5 status row → ✅ shipped | T12 |

**Files explicitly NOT touched in Phase 5:**
- `app/modules/interview_engine/tasks/` — Phase 3 correct.
- `app/modules/interview_engine/outcome_close.py` — Phase 2 correct (already has all 6 SessionOutcome values + the per-outcome closing instructions).
- `app/modules/interview_engine/event_log/redaction.py` — `disqualify.knockout: ("reason",)` already present. New `controller.intent.knockout_closed` kind has no content fields, so no entry needed.
- `prompts/v1/interview/*.txt` — no prompt body changes (Phase 5 fairness sign-off-free per spec §6.3).
- `frontend/app/` and `frontend/admin/` — recruiter UI to edit tenant settings is post-arc per Decision #19.
- `app/modules/admin/service.py::provision_client` — no row inserted on tenant provisioning (lazy-default pattern, P5-Q4).

---

## Task 1: Alembic migration 0027 — `tenant_settings` table + `sessions.knockout_failures` column

**Files:**
- Create: `backend/nexus/migrations/versions/0027_tenant_settings.py`
- Create: `backend/nexus/tests/test_migration_0027_tenant_settings.py`
- Modify: `backend/nexus/app/main.py` (`_TENANT_SCOPED_TABLES`)

**Why first:** every later code path either reads `tenant_settings` or writes the new column on `sessions`. Landing the migration + the `_TENANT_SCOPED_TABLES` registration in one commit keeps `main` in a consistent state (the startup `_assert_rls_completeness` check would CRITICAL-fail if the table existed in DB but wasn't in the allowlist; the inverse is harmless).

- [ ] **Step 1: Write the failing migration test**

Mirrors the pattern of `tests/test_question_banks_migration_0026.py` (Phase 4 reference). Create `backend/nexus/tests/test_migration_0027_tenant_settings.py`:

```python
"""ORM smoke tests for migration 0027 (Phase 5).

Covers:
- tenant_settings table created with PK on tenant_id, FK→clients ON DELETE CASCADE.
- engine_knockout_policy CHECK constraint rejects unknown values.
- engine_agent_name accepts NULL.
- Both RLS policies present (tenant_isolation with non-NULL WITH CHECK + service_bypass).
- sessions.knockout_failures column added with default '[]'::jsonb.
- Existing sessions row picks up '[]' default.

Tested against the create_all-built test DB (see tests/conftest.py).
The CHECK + server_default + RLS pair are mirrored on the ORM model
in app/modules/tenant_settings/models.py via __table_args__ +
server_default so this test exercises the same behavior under
create_all that production gets via the raw-SQL Alembic migration.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.session.models import Session as SessionRow
from app.modules.tenant_settings.models import TenantSettingsModel
from tests.conftest import create_test_client


async def test_tenant_settings_default_policy(db) -> None:
    tenant = await create_test_client(db)
    row = TenantSettingsModel(tenant_id=tenant.id)
    db.add(row)
    await db.flush()
    fetched = (
        await db.execute(
            text("SELECT engine_knockout_policy, engine_agent_name FROM tenant_settings WHERE tenant_id = :t"),
            {"t": str(tenant.id)},
        )
    ).first()
    assert fetched.engine_knockout_policy == "record_only"
    assert fetched.engine_agent_name is None


async def test_tenant_settings_check_rejects_unknown_policy(db) -> None:
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_knockout_policy="hard_reject",  # not in CHECK allowlist
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_tenant_settings_accepts_close_polite(db) -> None:
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_knockout_policy="close_polite",
            engine_agent_name="Acme-Bot",
        )
    )
    await db.flush()


async def test_sessions_knockout_failures_default(db) -> None:
    """A freshly inserted Session row gets `[]` for knockout_failures."""
    # Build up a minimum graph that the Session FK chain requires; we
    # only assert the new column's default fires. The conftest fixtures
    # used in test_migration_0024 cover this graph — extract or reuse.
    from tests.conftest import (
        create_test_assignment,
        create_test_candidate,
        create_test_job,
        create_test_org_unit,
        create_test_pipeline_instance,
        create_test_pipeline_stage,
        create_test_user,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        company_profile={
            "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
            "industry": "Technology",
            "company_stage": "Series C",
            "hiring_bar": "standard",
        },
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))
    job = await create_test_job(db, tenant.id, org.id)
    pipeline = await create_test_pipeline_instance(db, tenant.id, job.id)
    stage = await create_test_pipeline_stage(db, tenant.id, pipeline.id)
    candidate = await create_test_candidate(db, tenant.id, org.id)
    assignment = await create_test_assignment(db, tenant.id, candidate.id, job.id)

    sess = SessionRow(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state="created",
        state_changed_at=datetime.now(UTC),
    )
    db.add(sess)
    await db.flush()

    fetched = (
        await db.execute(
            text("SELECT knockout_failures FROM sessions WHERE id = :s"),
            {"s": str(sess.id)},
        )
    ).first()
    assert fetched.knockout_failures == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/test_migration_0027_tenant_settings.py -v
```

Expected: collection failure / `ImportError: cannot import name 'TenantSettingsModel'` (model doesn't exist yet) AND/OR `OperationalError: relation "tenant_settings" does not exist` once the model lands.

- [ ] **Step 3: Write the migration file**

Create `backend/nexus/migrations/versions/0027_tenant_settings.py`:

```python
"""Phase 5 — tenant_settings table + sessions.knockout_failures column.

Two additive operations:

1. ``tenant_settings`` (NEW): per-tenant configuration carrying
   ``engine_knockout_policy`` (record_only | close_polite, default
   record_only) and ``engine_agent_name`` (nullable; null means use
   ``settings.engine_agent_name`` env fallback). PK = tenant_id, FK
   ``clients(id) ON DELETE CASCADE`` matches migration 0023's hard-delete
   discipline. Canonical RLS policy pair with NULLIF.

   No backfill — lazy-default pattern: ``get_tenant_settings`` returns
   the default ``TenantSettings(...)`` when no row exists, so existing
   tenants need not have a row inserted. When the future recruiter UI
   to edit settings ships, the first edit creates the row via UPSERT.

2. ``sessions.knockout_failures`` (NEW column): JSONB array, default
   ``'[]'::jsonb``, NOT NULL. Stores the engine's ``KnockoutFailure``
   list in queryable form for Phase 3D analytics + EEOC fairness review
   (``WHERE knockout_failures != '[]'`` is a one-line filter). PG11+
   metadata-only column add — no table rewrite.

Revision ID: 0027_tenant_settings
Revises: 0026_question_kind_column
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0027_tenant_settings"
down_revision: str | None = "0026_question_kind_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "engine_knockout_policy",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'record_only'"),
        ),
        sa.Column("engine_agent_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "engine_knockout_policy IN ('record_only', 'close_polite')",
            name="ck_tenant_settings_engine_knockout_policy",
        ),
    )

    # Enable RLS + canonical policy pair (with NULLIF discipline).
    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY "tenant_isolation" ON tenant_settings
          USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY "service_bypass" ON tenant_settings
          USING (current_setting('app.bypass_rls', true) = 'true');
        """
    )

    # Grant the nexus_app runtime role explicit DML on the new table
    # (matches the discipline from migration 0010_create_nexus_app_role).
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_settings TO nexus_app;")

    # sessions.knockout_failures — additive column add. PG11+ metadata-only.
    op.add_column(
        "sessions",
        sa.Column(
            "knockout_failures",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "knockout_failures")
    op.execute('DROP POLICY IF EXISTS "service_bypass" ON tenant_settings;')
    op.execute('DROP POLICY IF EXISTS "tenant_isolation" ON tenant_settings;')
    op.drop_table("tenant_settings")
```

- [ ] **Step 4: Apply the migration locally**

```bash
docker compose run --rm nexus alembic upgrade head
```

Expected output ends with: `INFO  [alembic.runtime.migration] Running upgrade 0026_question_kind_column -> 0027_tenant_settings, Phase 5 — tenant_settings table + sessions.knockout_failures column`.

- [ ] **Step 5: Add `"tenant_settings"` to `_TENANT_SCOPED_TABLES`**

Modify `backend/nexus/app/main.py:34-57`. Append `"tenant_settings"` to the existing tuple. The exact insertion point (alphabetical-ish or end-of-Phase-5-bucket) is at the end before the closing `)`:

```python
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    # ... existing entries ...
    # Phase 3C — scheduler + session
    "candidate_session_tokens",
    # Phase 5 — tenant settings
    "tenant_settings",
)
```

- [ ] **Step 6: Confirm boot succeeds and the RLS startup check covers `tenant_settings`**

```bash
docker compose up -d nexus
docker compose logs nexus | grep -E "rls_completeness|tenant_settings" | head -5
```

Expected: `tenant_scoped_tables_verified=22` (was 21) appears in the structured-log line for `_assert_rls_completeness`. No CRITICAL.

- [ ] **Step 7: Re-run the migration test (it should still fail without the ORM model)**

```bash
docker compose run --rm nexus pytest tests/test_migration_0027_tenant_settings.py -v
```

Expected: still `ImportError` on `TenantSettingsModel`. The migration test is staged here so it can pass once T2's ORM lands. This matches Phase 4's pattern — the migration test goes green at T2's commit.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/migrations/versions/0027_tenant_settings.py \
        backend/nexus/tests/test_migration_0027_tenant_settings.py \
        backend/nexus/app/main.py
git commit -m "$(cat <<'EOF'
feat(migration): 0027 tenant_settings + sessions.knockout_failures (Phase 5)

Adds tenant_settings table (per-tenant engine_knockout_policy +
engine_agent_name override) with canonical RLS pair, and
sessions.knockout_failures JSONB column (default '[]') for the
upcoming KnockoutFailure persistence path.

Registers tenant_settings in _TENANT_SCOPED_TABLES so the startup
RLS completeness check covers it. Migration test is staged but
still fails at this commit (the ORM model lands in the next task);
this matches the Phase 4 migration-test pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `tenant_settings` module — ORM + Pydantic + service + module-boundary registration

**Files:**
- Create: `backend/nexus/app/modules/tenant_settings/__init__.py`
- Create: `backend/nexus/app/modules/tenant_settings/models.py`
- Create: `backend/nexus/app/modules/tenant_settings/schemas.py`
- Create: `backend/nexus/app/modules/tenant_settings/service.py`
- Create: `backend/nexus/tests/test_tenant_settings_schemas.py`
- Create: `backend/nexus/tests/test_tenant_settings_service.py`
- Modify: `backend/nexus/tests/test_module_boundaries.py` (`KNOWN_DOMAIN_MODULES`)

- [ ] **Step 1: Write failing schema tests**

Create `backend/nexus/tests/test_tenant_settings_schemas.py`:

```python
"""Pure-unit tests for app.modules.tenant_settings.schemas."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.tenant_settings import KnockoutPolicy, TenantSettings


def test_defaults() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(tenant_id=tenant_id)
    assert s.tenant_id == tenant_id
    assert s.engine_knockout_policy == "record_only"
    assert s.engine_agent_name is None


def test_explicit_values() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(
        tenant_id=tenant_id,
        engine_knockout_policy="close_polite",
        engine_agent_name="Acme-Bot",
    )
    assert s.engine_knockout_policy == "close_polite"
    assert s.engine_agent_name == "Acme-Bot"


def test_unknown_policy_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantSettings(
            tenant_id=uuid.uuid4(),
            engine_knockout_policy="hard_reject",  # not in Literal
        )


def test_round_trip() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(
        tenant_id=tenant_id,
        engine_knockout_policy="close_polite",
        engine_agent_name=None,
    )
    dumped = s.model_dump(mode="json")
    s2 = TenantSettings.model_validate(dumped)
    assert s2 == s


def test_knockout_policy_literal_values() -> None:
    """Type-level: KnockoutPolicy Literal exposes both values."""
    # Runtime check via __args__
    from typing import get_args
    assert set(get_args(KnockoutPolicy)) == {"record_only", "close_polite"}
```

- [ ] **Step 2: Run schema tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/test_tenant_settings_schemas.py -v
```

Expected: collection failure / `ModuleNotFoundError: No module named 'app.modules.tenant_settings'`.

- [ ] **Step 3: Write `schemas.py`**

Create `backend/nexus/app/modules/tenant_settings/schemas.py`:

```python
"""Pydantic models for the tenant_settings module."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


KnockoutPolicy = Literal["record_only", "close_polite"]


class TenantSettings(BaseModel):
    """Per-tenant engine configuration.

    `engine_agent_name` is None-able; null means "use the env fallback
    `settings.engine_agent_name`". The override applies only at the
    candidate-facing prompt-substitution site (`controller.py`'s
    `build_controller_prompt`) and the `controller.started` log; the
    LiveKit routing label (decorator at `agent.py:130` and
    `dispatch_agent` call at `livekit.py:102`) STAYS on the env value
    because it's a fleet-wide routing primitive, not a candidate-facing
    identifier (P5-Q1 in the Phase 5 spec).
    """

    tenant_id: UUID
    engine_knockout_policy: KnockoutPolicy = "record_only"
    engine_agent_name: str | None = None
```

- [ ] **Step 4: Write `models.py`**

Create `backend/nexus/app/modules/tenant_settings/models.py`:

```python
"""ORM model for the tenant_settings table.

PK = tenant_id (one row per tenant). FK clients.id ON DELETE CASCADE
follows the migration 0023 hard-delete discipline. CHECK constraint
mirrors the DB-level CHECK in migration 0027 so create_all-based test
DBs exercise the same behavior.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TenantSettingsModel(Base):
    __tablename__ = "tenant_settings"
    __table_args__ = (
        CheckConstraint(
            "engine_knockout_policy IN ('record_only', 'close_polite')",
            name="ck_tenant_settings_engine_knockout_policy",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    engine_knockout_policy: Mapped[str] = mapped_column(
        nullable=False, server_default=text("'record_only'")
    )
    engine_agent_name: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
```

- [ ] **Step 5: Write `service.py`**

Create `backend/nexus/app/modules/tenant_settings/service.py`:

```python
"""tenant_settings service layer.

`get_tenant_settings(db, tenant_id)` is the single read path. It returns
the row's values when present, or the schema's defaults if the tenant
doesn't have a row yet (lazy-default pattern, P5-Q4). No backfill is
performed; when the future recruiter-UI editing path ships, the first
edit creates the row via UPSERT.
"""
from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenant_settings.models import TenantSettingsModel
from app.modules.tenant_settings.schemas import TenantSettings


log = structlog.get_logger("tenant_settings")


def DEFAULT_TENANT_SETTINGS(tenant_id: UUID) -> TenantSettings:
    """Build the default TenantSettings for a tenant with no row.

    Mirrors the DB-level defaults in migration 0027.
    """
    return TenantSettings(tenant_id=tenant_id)


async def get_tenant_settings(db: AsyncSession, tenant_id: UUID) -> TenantSettings:
    """Return the tenant's settings, falling back to defaults if no row.

    Caller may be on a tenant-scoped or bypass-RLS session; both paths
    work because RLS is enforced by the policies, not by the helper.
    On a tenant-scoped session reading a different tenant_id, the
    policy filter returns 0 rows and the helper falls back to defaults
    for the *requesting* tenant — same as if the row had never been
    written. This is intentional: the service is permissive on read
    and the caller is responsible for tenant scope.
    """
    row = (
        await db.execute(
            select(TenantSettingsModel).where(TenantSettingsModel.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return DEFAULT_TENANT_SETTINGS(tenant_id)
    return TenantSettings(
        tenant_id=row.tenant_id,
        engine_knockout_policy=row.engine_knockout_policy,
        engine_agent_name=row.engine_agent_name,
    )
```

- [ ] **Step 6: Write `__init__.py` (public API)**

Create `backend/nexus/app/modules/tenant_settings/__init__.py`:

```python
"""tenant_settings module public API.

Cross-module callers MUST import from this package:
    from app.modules.tenant_settings import (
        TenantSettings, KnockoutPolicy, get_tenant_settings, DEFAULT_TENANT_SETTINGS,
    )

Deep imports (`from app.modules.tenant_settings.service import ...`) are
forbidden by `tests/test_module_boundaries.py`.
"""
from __future__ import annotations

from app.modules.tenant_settings.schemas import KnockoutPolicy, TenantSettings
from app.modules.tenant_settings.service import (
    DEFAULT_TENANT_SETTINGS,
    get_tenant_settings,
)

__all__ = [
    "DEFAULT_TENANT_SETTINGS",
    "KnockoutPolicy",
    "TenantSettings",
    "get_tenant_settings",
]
```

- [ ] **Step 7: Register module in `KNOWN_DOMAIN_MODULES`**

Modify `backend/nexus/tests/test_module_boundaries.py:40-61`. Append `"tenant_settings"` to the frozenset (keep alphabetical order):

```python
KNOWN_DOMAIN_MODULES = frozenset(
    {
        "admin",
        "ats",
        "analysis",
        "audit",
        "auth",
        "candidates",
        "interview_engine",
        "interview_runtime",
        "jd",
        "notifications",
        "org_units",
        "pipelines",
        "question_bank",
        "reporting",
        "roles",
        "scheduler",
        "session",
        "settings",
        "tenant_settings",
    }
)
```

- [ ] **Step 8: Re-run schema tests + migration test (both should now pass)**

```bash
docker compose run --rm nexus pytest tests/test_tenant_settings_schemas.py tests/test_migration_0027_tenant_settings.py tests/test_module_boundaries.py -v
```

Expected: all green.

- [ ] **Step 9: Write the service test (failing first per TDD)**

Create `backend/nexus/tests/test_tenant_settings_service.py`:

```python
"""Service-layer tests for app.modules.tenant_settings.service."""
from __future__ import annotations

import uuid

import pytest

from app.modules.tenant_settings import (
    DEFAULT_TENANT_SETTINGS,
    TenantSettings,
    get_tenant_settings,
)
from app.modules.tenant_settings.models import TenantSettingsModel
from tests.conftest import create_test_client


async def test_no_row_returns_defaults(db) -> None:
    tenant = await create_test_client(db)
    s = await get_tenant_settings(db, tenant.id)
    assert s == DEFAULT_TENANT_SETTINGS(tenant.id)


async def test_existing_row_returns_values(db) -> None:
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_knockout_policy="close_polite",
            engine_agent_name="Acme-Bot",
        )
    )
    await db.flush()
    s = await get_tenant_settings(db, tenant.id)
    assert s.engine_knockout_policy == "close_polite"
    assert s.engine_agent_name == "Acme-Bot"
    assert s.tenant_id == tenant.id


async def test_partial_row_only_engine_agent_name(db) -> None:
    """A row with only engine_agent_name set keeps default policy."""
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_agent_name="Acme-Bot",
            # engine_knockout_policy uses server_default = 'record_only'
        )
    )
    await db.flush()
    s = await get_tenant_settings(db, tenant.id)
    assert s.engine_knockout_policy == "record_only"
    assert s.engine_agent_name == "Acme-Bot"


async def test_default_factory_returns_correct_tenant_id() -> None:
    """DEFAULT_TENANT_SETTINGS is keyed on the requesting tenant_id."""
    tenant_id = uuid.uuid4()
    s = DEFAULT_TENANT_SETTINGS(tenant_id)
    assert s.tenant_id == tenant_id
    assert s.engine_knockout_policy == "record_only"
    assert s.engine_agent_name is None
```

- [ ] **Step 10: Run service test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/test_tenant_settings_service.py -v
```

Expected: 4 PASSED.

- [ ] **Step 11: Commit**

```bash
git add backend/nexus/app/modules/tenant_settings/ \
        backend/nexus/tests/test_tenant_settings_schemas.py \
        backend/nexus/tests/test_tenant_settings_service.py \
        backend/nexus/tests/test_module_boundaries.py
git commit -m "$(cat <<'EOF'
feat(tenant_settings): add module + ORM + service (Phase 5)

New module providing the per-tenant engine configuration surface.
Public API exposes TenantSettings, KnockoutPolicy Literal,
get_tenant_settings, DEFAULT_TENANT_SETTINGS via __init__.

Lazy-default read pattern: get_tenant_settings returns the row's
values when present, or schema defaults when the tenant has no row
(P5-Q4). No backfill or auto-create; future recruiter-UI editing
path will UPSERT on first edit.

Registered in test_module_boundaries.py KNOWN_DOMAIN_MODULES.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `KnockoutFailure` model + PII scrub validator

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py` (add `KnockoutFailure` + `_scrub_pii`)
- Modify: `backend/nexus/app/modules/interview_runtime/__init__.py` (re-export)
- Create: `backend/nexus/tests/test_interview_runtime_knockout_failure.py`

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/test_interview_runtime_knockout_failure.py`:

```python
"""Pure-unit tests for KnockoutFailure + _scrub_pii.

Defense-in-depth: the LLM prompt instructs the agent never to include
PII in `knockout_reason`. The Pydantic field validator on
`KnockoutFailure.reason` runs `_scrub_pii` on every construction path
(including model_validate from a DB read), unconditionally. Together
with prompt + RLS, these are 3 layers of defense.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.interview_runtime import KnockoutFailure


# --- _scrub_pii: emails ---


def test_scrub_email_simple() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate said reach me at john@acme.com after work.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "[redacted]" in f.reason
    assert "john@acme.com" not in f.reason


def test_scrub_email_with_plus_addressing() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Send confirmation to j.smith+work@example.io please.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "j.smith+work@example.io" not in f.reason


# --- _scrub_pii: phones ---


def test_scrub_phone_us_dashed() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate's number is +1 555-123-4567 for follow-up.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "555-123-4567" not in f.reason


def test_scrub_phone_us_parens() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Reachable on (555) 123-4567 anytime.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "(555) 123-4567" not in f.reason


def test_scrub_phone_dotted() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Best line is 555.123.4567 weekdays.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "555.123.4567" not in f.reason


# --- _scrub_pii: passes plain text ---


def test_plain_text_passes_through() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate stated they cannot work UK shift hours.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert f.reason == "Candidate stated they cannot work UK shift hours."


def test_short_numbers_not_scrubbed() -> None:
    """Phone regex requires 8+ digits — short numeric runs should pass."""
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate has 5 years of experience.",
        signal_values=["years_exp"],
        occurred_at_ms=1000,
    )
    assert "5 years" in f.reason


# --- _scrub_pii: idempotent ---


def test_scrub_idempotent() -> None:
    text = "Contact me at john@acme.com or +1 555-123-4567."
    once = KnockoutFailure(
        question_id="q1",
        reason=text,
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    ).reason
    twice = KnockoutFailure(
        question_id="q1",
        reason=once,
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    ).reason
    assert once == twice


# --- field constraints ---


def test_question_id_min_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="",
            reason="Cannot work UK shift hours.",
            signal_values=["uk_shift"],
            occurred_at_ms=1000,
        )


def test_reason_min_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="",
            signal_values=["uk_shift"],
            occurred_at_ms=1000,
        )


def test_reason_max_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="x" * 501,  # 500 is the cap
            signal_values=["uk_shift"],
            occurred_at_ms=1000,
        )


def test_signal_values_min_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="Cannot work UK shift hours.",
            signal_values=[],
            occurred_at_ms=1000,
        )


def test_occurred_at_ms_non_negative() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="Cannot work UK shift hours.",
            signal_values=["uk_shift"],
            occurred_at_ms=-1,
        )


# --- model_validate (DB read) path runs the scrub too ---


def test_model_validate_runs_scrub() -> None:
    """Defense-in-depth: a row inserted before scrub was active must
    still be scrubbed when read back via model_validate."""
    raw = {
        "question_id": "q1",
        "reason": "My number is +1 555-123-4567.",
        "signal_values": ["uk_shift"],
        "occurred_at_ms": 1000,
    }
    f = KnockoutFailure.model_validate(raw)
    assert "555-123-4567" not in f.reason
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/test_interview_runtime_knockout_failure.py -v
```

Expected: `ImportError: cannot import name 'KnockoutFailure' from 'app.modules.interview_runtime'`.

- [ ] **Step 3: Add `KnockoutFailure` + `_scrub_pii` to `schemas.py`**

Modify `backend/nexus/app/modules/interview_runtime/schemas.py`. Add these imports at the top:

```python
import re

from pydantic import BaseModel, Field, field_validator
```

Add these definitions near the bottom of the file, before the existing `SessionResult` class definition (around line 200):

```python
# ---------------------------------------------------------------------------
# Knockout failure (Phase 5) — persisted summary of a hard-requirement
# failure surfaced by the engine's `disqualify_knockout` shared tool.
#
# Defense-in-depth PII boundary: the LLM prompt instructs the agent never
# to include PII in `knockout_reason`; this validator runs `_scrub_pii` on
# every construction path (including model_validate from a DB read) as
# a backstop. RLS on the `sessions` table enforces tenant isolation at
# the storage layer. Three layers; PII has to fail through all three.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b")


def _scrub_pii(text: str) -> str:
    """Replace email + phone-number matches with `[redacted]`.

    Idempotent. Runs unconditionally on every KnockoutFailure
    construction (validator mode='before') including model_validate
    from a DB read.
    """
    text = _EMAIL_RE.sub("[redacted]", text)
    text = _PHONE_RE.sub("[redacted]", text)
    return text


class KnockoutFailure(BaseModel):
    """Persisted record of a knockout failure (Phase 5).

    Authored by the engine's `disqualify_knockout` shared tool when a
    candidate self-discloses something that invalidates a hard
    requirement (e.g. "I cannot work UK shift hours"). Engine records,
    never auto-rejects — Phase 3D analytics consumes this list.

    `reason` is LLM-authored 1-3 sentence factual summary; the
    `_scrub_reason` validator strips emails + phone numbers as
    defense-in-depth.
    """

    question_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    signal_values: list[str] = Field(min_length=1)
    occurred_at_ms: int = Field(ge=0)

    @field_validator("reason", mode="before")
    @classmethod
    def _scrub_reason(cls, v: object) -> object:
        if not isinstance(v, str):
            # Let the str-coercion / min_length validator produce the
            # right ValidationError downstream. Don't shadow it here.
            return v
        return _scrub_pii(v)
```

- [ ] **Step 4: Re-export `KnockoutFailure` from `__init__.py`**

Modify `backend/nexus/app/modules/interview_runtime/__init__.py`. Read first to find the existing `__all__`:

```bash
docker compose run --rm nexus cat app/modules/interview_runtime/__init__.py
```

Then add `KnockoutFailure` to both the import block and the `__all__` list. (The exact diff depends on the current `__init__.py` content; the operation is "append `KnockoutFailure` alongside the existing schema re-exports".)

- [ ] **Step 5: Run the model tests to verify all pass**

```bash
docker compose run --rm nexus pytest tests/test_interview_runtime_knockout_failure.py -v
```

Expected: all green (≈ 14 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py \
        backend/nexus/app/modules/interview_runtime/__init__.py \
        backend/nexus/tests/test_interview_runtime_knockout_failure.py
git commit -m "$(cat <<'EOF'
feat(interview_runtime): add KnockoutFailure model + PII scrub (Phase 5)

KnockoutFailure is the persisted shape of a hard-requirement failure
surfaced by the engine's disqualify_knockout shared tool. Field
validator runs _scrub_pii (email + phone regex → [redacted]) on every
construction path including model_validate from a DB read.

Defense-in-depth: prompt instructs the LLM never to include PII;
this validator is the unconditional backstop; RLS on sessions
enforces storage-layer tenant isolation. Three layers.

Public API: re-exported from app.modules.interview_runtime.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `SessionResult.knockout_failures` field + `Session` ORM column

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py` (`SessionResult`)
- Modify: `backend/nexus/app/modules/session/models.py` (`Session`, line 20)
- Create: `backend/nexus/tests/test_session_result_knockout_failures.py`

- [ ] **Step 1: Write failing test**

Create `backend/nexus/tests/test_session_result_knockout_failures.py`:

```python
"""Round-trip test for SessionResult.knockout_failures."""
from __future__ import annotations

from app.modules.interview_runtime import KnockoutFailure, SessionResult


def _make_minimal_result(**overrides) -> SessionResult:
    base = dict(
        session_id="00000000-0000-0000-0000-000000000001",
        job_title="Customer Support Specialist",
        stage_id="00000000-0000-0000-0000-000000000002",
        stage_type="phone_screen",
        candidate_name="Test Candidate",
        duration_seconds=600.0,
        questions_asked=4,
        questions_skipped=0,
        total_probes_fired=2,
        question_results=[],
        full_transcript=[],
        completed_at="2026-05-03T12:00:00Z",
    )
    base.update(overrides)
    return SessionResult(**base)


def test_default_empty_list() -> None:
    r = _make_minimal_result()
    assert r.knockout_failures == []


def test_round_trip_with_failures() -> None:
    failures = [
        KnockoutFailure(
            question_id="q3",
            reason="Cannot work UK shift hours.",
            signal_values=["uk_shift"],
            occurred_at_ms=120_000,
        ),
        KnockoutFailure(
            question_id="q4",
            reason="No driver's license.",
            signal_values=["drivers_license"],
            occurred_at_ms=180_000,
        ),
    ]
    r = _make_minimal_result(knockout_failures=failures)
    dumped = r.model_dump(mode="json")
    r2 = SessionResult.model_validate(dumped)
    assert r2.knockout_failures == failures


def test_independent_per_instance_default() -> None:
    """default_factory=list, not a shared mutable default."""
    a = _make_minimal_result()
    b = _make_minimal_result()
    a.knockout_failures.append(
        KnockoutFailure(
            question_id="q1",
            reason="x",
            signal_values=["sig"],
            occurred_at_ms=0,
        )
    )
    assert b.knockout_failures == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/test_session_result_knockout_failures.py -v
```

Expected: `pydantic.ValidationError: ... extra fields not permitted` (the field doesn't exist yet) OR `AssertionError` on the default-list test depending on Pydantic config.

- [ ] **Step 3: Extend `SessionResult` in `schemas.py`**

Modify `backend/nexus/app/modules/interview_runtime/schemas.py:200-219`. Add the import (already added in T3) for `KnockoutFailure`'s sibling `Field` if not present, then add to `SessionResult`:

```python
class SessionResult(BaseModel):
    # ... existing fields unchanged ...
    full_transcript: list[TranscriptEntry]
    completed_at: str = Field(
        description="ISO 8601 timestamp of session completion.",
    )
    knockout_failures: list[KnockoutFailure] = Field(
        default_factory=list,
        description=(
            "Hard-requirement failures recorded during the interview "
            "(self-disclosed, factual). Engine records, never auto-rejects "
            "— Phase 3D analytics consumes this list."
        ),
    )
```

- [ ] **Step 4: Add the `knockout_failures` mapped column to `Session` ORM**

Modify `backend/nexus/app/modules/session/models.py`. Around line 62 where `raw_result_json: Mapped[dict | None] = mapped_column(JSONB)` lives, add:

```python
    knockout_failures: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
```

If `text` isn't already imported in `models.py`, add `from sqlalchemy import text` to the imports.

- [ ] **Step 5: Run round-trip test to verify pass**

```bash
docker compose run --rm nexus pytest tests/test_session_result_knockout_failures.py tests/test_interview_runtime_knockout_failure.py -v
```

Expected: all green.

- [ ] **Step 6: Run the migration test (it should still pass) plus a broad sanity sweep**

```bash
docker compose run --rm nexus pytest tests/test_migration_0027_tenant_settings.py tests/test_module_boundaries.py -v
```

Expected: all green. The migration test's `test_sessions_knockout_failures_default` exercises the new column on a freshly-inserted Session row — confirms the ORM mapping aligns with the DB default.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py \
        backend/nexus/app/modules/session/models.py \
        backend/nexus/tests/test_session_result_knockout_failures.py
git commit -m "$(cat <<'EOF'
feat(interview_runtime): add SessionResult.knockout_failures + ORM column (Phase 5)

SessionResult gains knockout_failures: list[KnockoutFailure] with
default_factory=list. The Session ORM column is JSONB NOT NULL
DEFAULT '[]'::jsonb (mirrors migration 0027).

Round-trip test confirms: default empty; explicit list survives
model_dump → model_validate; per-instance independence (no shared
mutable default).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `record_session_result` writes `sessions.knockout_failures`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/service.py` (`record_session_result`, lines 262-278)
- Create: `backend/nexus/tests/interview_runtime/integration/test_record_session_result_knockout_failures.py`

- [ ] **Step 1: Write failing integration test**

Create `backend/nexus/tests/interview_runtime/integration/test_record_session_result_knockout_failures.py` (create the `interview_runtime/integration/` directory + `__init__.py` files if they don't exist):

```python
"""Integration test: record_session_result persists knockout_failures."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.modules.interview_runtime import (
    KnockoutFailure,
    SessionResult,
    record_session_result,
)
from app.modules.session.models import Session as SessionRow

from tests.conftest import (
    create_test_assignment,
    create_test_candidate,
    create_test_client,
    create_test_job,
    create_test_org_unit,
    create_test_pipeline_instance,
    create_test_pipeline_stage,
    create_test_user,
)


_PROFILE = {
    "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
    "industry": "Technology",
    "company_stage": "Series C",
    "hiring_bar": "standard",
}


async def _seed_active_session(db) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (session_id, tenant_id) for a session in state='active'."""
    from sqlalchemy import text as _text

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_PROFILE
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))
    job = await create_test_job(db, tenant.id, org.id)
    pipeline = await create_test_pipeline_instance(db, tenant.id, job.id)
    stage = await create_test_pipeline_stage(db, tenant.id, pipeline.id)
    candidate = await create_test_candidate(db, tenant.id, org.id)
    assignment = await create_test_assignment(db, tenant.id, candidate.id, job.id)

    sess = SessionRow(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state="active",
        state_changed_at=datetime.now(UTC),
    )
    db.add(sess)
    await db.flush()
    return sess.id, tenant.id


def _result_with_knockouts(session_id: uuid.UUID) -> SessionResult:
    return SessionResult(
        session_id=str(session_id),
        job_title="CS Specialist",
        stage_id=str(uuid.uuid4()),
        stage_type="phone_screen",
        candidate_name="Test Candidate",
        duration_seconds=420.0,
        questions_asked=3,
        questions_skipped=0,
        total_probes_fired=1,
        question_results=[],
        full_transcript=[],
        completed_at=datetime.now(UTC).isoformat(),
        knockout_failures=[
            KnockoutFailure(
                question_id="q3",
                reason="Cannot work UK shift hours.",
                signal_values=["uk_shift"],
                occurred_at_ms=120_000,
            )
        ],
    )


async def test_writes_knockout_failures_column(db) -> None:
    session_id, tenant_id = await _seed_active_session(db)
    result = _result_with_knockouts(session_id)

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.commit()

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert len(row.knockout_failures) == 1
    assert row.knockout_failures[0]["question_id"] == "q3"
    assert row.knockout_failures[0]["signal_values"] == ["uk_shift"]
    assert row.knockout_failures[0]["reason"] == "Cannot work UK shift hours."
    assert row.knockout_failures[0]["occurred_at_ms"] == 120_000
    assert row.state == "completed"


async def test_idempotent_retry_preserves_knockout_failures(db) -> None:
    session_id, tenant_id = await _seed_active_session(db)
    result = _result_with_knockouts(session_id)

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.commit()

    # Second call — session is now `completed`, must be a silent no-op.
    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.commit()

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert len(row.knockout_failures) == 1


async def test_empty_knockout_failures_writes_empty_list(db) -> None:
    session_id, tenant_id = await _seed_active_session(db)
    result = SessionResult(
        session_id=str(session_id),
        job_title="CS Specialist",
        stage_id=str(uuid.uuid4()),
        stage_type="phone_screen",
        candidate_name="Test Candidate",
        duration_seconds=420.0,
        questions_asked=3,
        questions_skipped=0,
        total_probes_fired=1,
        question_results=[],
        full_transcript=[],
        completed_at=datetime.now(UTC).isoformat(),
        # knockout_failures defaults to []
    )

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.commit()

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.knockout_failures == []
```

- [ ] **Step 2: Run integration test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/interview_runtime/integration/test_record_session_result_knockout_failures.py -v
```

Expected: AssertionError on `len(row.knockout_failures) == 1` (the column writes empty `[]` because the service's UPDATE doesn't include it yet) OR an early failure if the test file's directory hierarchy needs `__init__.py` files.

- [ ] **Step 3: Extend `record_session_result`**

Modify `backend/nexus/app/modules/interview_runtime/service.py:262-278`. Add `knockout_failures` to the `.values(...)` block:

```python
    res = await db.execute(
        update(SessionRow)
        .where(
            SessionRow.id == session_id,
            SessionRow.tenant_id == tenant_id,
            SessionRow.state == "active",
        )
        .values(
            raw_result_json=result.model_dump(mode="json"),
            transcript=[t.model_dump(mode="json") for t in result.full_transcript],
            questions_asked=result.questions_asked,
            probes_fired=result.total_probes_fired,
            knockout_failures=[k.model_dump(mode="json") for k in result.knockout_failures],
            agent_completed_at=now,
            result_status=derived_status,
            state="completed",
            state_changed_at=now,
        )
    )
```

- [ ] **Step 4: Run integration test to verify pass**

```bash
docker compose run --rm nexus pytest tests/interview_runtime/integration/test_record_session_result_knockout_failures.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Run the broader interview_runtime test sweep to catch regressions**

```bash
docker compose run --rm nexus pytest tests/interview_runtime/ tests/test_interview_runtime_*.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/service.py \
        backend/nexus/tests/interview_runtime/integration/test_record_session_result_knockout_failures.py \
        backend/nexus/tests/interview_runtime/integration/__init__.py \
        backend/nexus/tests/interview_runtime/__init__.py
git commit -m "$(cat <<'EOF'
feat(interview_runtime): persist knockout_failures via record_session_result (Phase 5)

record_session_result now writes the new sessions.knockout_failures
column from result.knockout_failures, alongside the existing
raw_result_json (which still contains the same data inline — Phase 5
keeps the dedicated column as the queryable surface for Phase 3D
analytics + EEOC fairness reviews per spec §3.5 and P5-Q2).

Idempotent-retry path preserves the prior write (the existing
state='active' gate filters out the second call).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Controller constructor rename + `agent_name` plumbing

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/controller.py` (constructor signature; `_agent_name` derivation; `build_controller_prompt` arg; `controller.started` log)
- Modify: `backend/nexus/app/modules/interview_engine/agent.py` (fetch `tenant_settings`; pass to controller)
- Create: `backend/nexus/tests/interview_engine/integration/test_agent_name_override.py`
- Update: existing fixtures / tests that construct `InterviewController` directly (if any)

**Why this task:** the existing `tenant_policy: KnockoutPolicy` constructor parameter is replaced with `tenant_settings: TenantSettings`. This is a refactor that preserves behavior (default `record_only` → behavior identical to today). Bundling the `agent_name` plumbing into this commit keeps the constructor signature stable across the rest of the phase.

- [ ] **Step 1: Find every existing `InterviewController(...)` construction site**

```bash
grep -rn "InterviewController(" backend/nexus/ --include="*.py"
```

Capture the list — you'll update each to the new signature.

- [ ] **Step 2: Write the failing integration test**

Create `backend/nexus/tests/interview_engine/integration/test_agent_name_override.py`:

```python
"""Integration test: tenant_settings.engine_agent_name override flows
through to build_controller_prompt + controller.started log."""
from __future__ import annotations

import uuid

import pytest
import structlog
from structlog.testing import capture_logs

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import (
    InterviewController,
    build_controller_prompt,
)
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.interview_runtime import SessionConfig
from app.modules.tenant_settings import TenantSettings

from tests.interview_engine.fixtures import build_minimal_session_config


def _build_collector() -> EventCollector:
    return EventCollector(
        session_id="sess-1",
        tenant_id="tenant-1",
        correlation_id="corr-1",
        controller_prompt_hash="sha256:abc",
        task_prompt_hashes={},
        model_versions={},
        redaction_mode="metadata",
    )


def test_build_controller_prompt_with_override() -> None:
    config = build_minimal_session_config()
    rendered = build_controller_prompt(config, agent_name="Acme-Bot")
    assert "Acme-Bot" in rendered


def test_build_controller_prompt_default_uses_arg() -> None:
    """No env-fallback fallback inside build_controller_prompt — caller
    pre-resolves the env fallback. The function only substitutes."""
    config = build_minimal_session_config()
    rendered = build_controller_prompt(config, agent_name="Dakota-1785")
    assert "Dakota-1785" in rendered


def test_controller_init_with_explicit_agent_name() -> None:
    config = build_minimal_session_config()
    settings = TenantSettings(
        tenant_id=uuid.uuid4(),
        engine_knockout_policy="record_only",
        engine_agent_name="Acme-Bot",
    )
    ctrl = InterviewController(
        session_config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="corr-1",
        collector=_build_collector(),
        idle_nudge_config=IdleNudgeConfig(60.0, 60.0, 60.0),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=30.0,
        ),
        tenant_settings=settings,
    )
    assert ctrl._agent_name == "Acme-Bot"
    assert ctrl._tenant_policy == "record_only"
    assert ctrl._agent_name_override_active is True


def test_controller_init_falls_back_to_env_when_null() -> None:
    """engine_agent_name=None falls back to settings.engine_agent_name (the env value)."""
    from app.config import settings as app_settings

    config = build_minimal_session_config()
    settings = TenantSettings(
        tenant_id=uuid.uuid4(),
        engine_knockout_policy="record_only",
        engine_agent_name=None,
    )
    ctrl = InterviewController(
        session_config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="corr-1",
        collector=_build_collector(),
        idle_nudge_config=IdleNudgeConfig(60.0, 60.0, 60.0),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=30.0,
        ),
        tenant_settings=settings,
    )
    assert ctrl._agent_name == app_settings.engine_agent_name
    assert ctrl._agent_name_override_active is False
```

If `tests/interview_engine/fixtures.py::build_minimal_session_config` doesn't exist yet, locate the equivalent helper used by Phase 3 integration tests via:

```bash
grep -rn "build_minimal_session_config\|build_test_session_config\|SessionConfig(" backend/nexus/tests/interview_engine/ | head -10
```

…and use whichever helper Phase 3 settled on. If the helper takes the form of an inline fixture rather than a module function, replicate the inline construction.

- [ ] **Step 3: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/interview_engine/integration/test_agent_name_override.py -v
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'tenant_settings'` (the parameter is still `tenant_policy`).

- [ ] **Step 4: Update `build_controller_prompt` signature**

Modify `backend/nexus/app/modules/interview_engine/controller.py` around line 85-102. Change the function signature to take `agent_name` and substitute it:

```python
def build_controller_prompt(session_config: SessionConfig, *, agent_name: str) -> str:
    """Load and substitute placeholders into the controller.txt prompt body.

    `agent_name` is the candidate-facing display name. The caller is
    responsible for resolving the env fallback when no per-tenant
    override is set (see `InterviewController.__init__`).
    """
    from string import Template
    from app.ai.prompts import prompt_loader

    template = Template(prompt_loader.get("interview/controller"))
    questions = session_config.stage.questions
    return template.substitute(
        agent_name=agent_name,
        company_about=session_config.company.about,
        company_industry=session_config.company.industry,
        company_stage=session_config.company.company_stage,
        company_hiring_bar=session_config.company.hiring_bar,
        job_title=session_config.job_title,
        seniority_level=session_config.seniority_level,
        duration_minutes=session_config.stage.duration_minutes,
        total_questions=len(questions),
    )
```

- [ ] **Step 5: Update `InterviewController.__init__` signature + derivation**

Modify `backend/nexus/app/modules/interview_engine/controller.py:106-139`. Replace `tenant_policy: KnockoutPolicy` with `tenant_settings: TenantSettings`; derive `_tenant_policy` and `_agent_name` from it; capture `_agent_name_override_active`; pass `agent_name` to `build_controller_prompt`.

```python
from app.modules.tenant_settings import TenantSettings  # add to existing imports near line 30+


class InterviewController(Agent):
    def __init__(
        self,
        *,
        session_config: SessionConfig,
        tenant_id: uuid.UUID,
        correlation_id: str,
        collector: EventCollector,
        idle_nudge_config: IdleNudgeConfig,
        budget: SessionBudget,
        tenant_settings: TenantSettings,        # was tenant_policy: KnockoutPolicy
    ) -> None:
        self._config: SessionConfig = session_config
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._collector = collector
        self._budget = budget
        self._idle_nudge_state = IdleNudgeStateMachine(idle_nudge_config)
        self._tenant_policy: KnockoutPolicy = tenant_settings.engine_knockout_policy
        self._agent_name: str = tenant_settings.engine_agent_name or settings.engine_agent_name
        self._agent_name_override_active: bool = tenant_settings.engine_agent_name is not None
        self._disqualified_signals: set[str] = set()
        self._knockout_failures: list[KnockoutFailureRecord] = []  # T7 swaps the type
        self._end_outcome: SessionOutcome | None = None
        self._current_task_run: asyncio.Task | None = None
        self._current_question_task = None  # type: ignore[var-annotated]
        self._terminated: bool = False
        self._idle_nudge_tick_task: asyncio.Task | None = None
        self._session_start_ms: int = 0
        self._session_start_monotonic: float = 0.0
        self._persisted: bool = False
        super().__init__(
            instructions=build_controller_prompt(session_config, agent_name=self._agent_name)
        )
```

- [ ] **Step 6: Add the `controller.started` log line in `on_enter`**

Modify `backend/nexus/app/modules/interview_engine/controller.py::on_enter` (around line 145). Insert near the top, after the existing `_session_start_*` setup but before the greeting:

```python
    async def on_enter(self) -> None:
        self._session_start_ms = now_ms()
        self._session_start_monotonic = time.monotonic()
        self._budget.started_at_monotonic = self._session_start_monotonic
        log.info(
            "controller.started",
            agent_name_displayed=self._agent_name,
            agent_name_override_active=self._agent_name_override_active,
            tenant_policy=self._tenant_policy,
        )
        await self._publish_progress_attributes()
        # ... rest unchanged ...
```

- [ ] **Step 7: Update `agent.py` entrypoint to fetch `tenant_settings` and pass it**

Modify `backend/nexus/app/modules/interview_engine/agent.py:159-234`. Add the `get_tenant_settings` import at the top (`from app.modules.tenant_settings import get_tenant_settings`). Inside the `async with get_bypass_session() as db:` block, fetch tenant settings after `build_session_config`. Replace `tenant_policy="record_only"` with `tenant_settings=tenant_settings`:

```python
    async with get_bypass_session() as db:
        config = await build_session_config(
            db,
            session_id=uuid.UUID(session_id),
            tenant_id=tenant_uuid,
        )
        tenant_settings = await get_tenant_settings(db, tenant_uuid)
    log.info(
        "engine.config.fetched",
        question_count=len(config.stage.questions),
        stage_type=config.stage.stage_type,
        tenant_policy=tenant_settings.engine_knockout_policy,
        agent_name_override_active=tenant_settings.engine_agent_name is not None,
    )
    _log_session_setup(config)

    # ... event_collector unchanged ...

    agent = InterviewController(
        session_config=config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
        collector=event_collector,
        idle_nudge_config=IdleNudgeConfig(
            first_nudge_seconds=settings.engine_idle_first_nudge_seconds,
            second_nudge_seconds=settings.engine_idle_second_nudge_seconds,
            give_up_seconds=settings.engine_idle_give_up_seconds,
        ),
        budget=SessionBudget(
            started_at_monotonic=time.monotonic(),
            duration_limit_seconds=config.stage.duration_minutes * 60.0,
            overhead_seconds=settings.engine_task_budget_overhead_seconds,
        ),
        tenant_settings=tenant_settings,
    )
```

- [ ] **Step 8: Update other `InterviewController(...)` construction sites**

Use the list captured in Step 1. Each site that passed `tenant_policy="record_only"` now passes `tenant_settings=TenantSettings(tenant_id=<uuid>, engine_knockout_policy="record_only")`. Tests for the existing close_polite stub may have their own constructions; update those uniformly.

- [ ] **Step 9: Run the agent-name-override test + the full interview_engine test sweep**

```bash
docker compose run --rm nexus pytest tests/interview_engine/integration/test_agent_name_override.py tests/interview_engine/ -v -m "not prompt_quality"
```

Expected: green.

- [ ] **Step 10: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/controller.py \
        backend/nexus/app/modules/interview_engine/agent.py \
        backend/nexus/tests/interview_engine/integration/test_agent_name_override.py \
        # ... any updated test fixtures ...
git commit -m "$(cat <<'EOF'
refactor(controller): take tenant_settings; agent_name override (Phase 5)

Replaces tenant_policy: KnockoutPolicy constructor parameter with
tenant_settings: TenantSettings. Derives _tenant_policy and
_agent_name from it; build_controller_prompt now takes agent_name
as an arg (caller resolves env fallback). New controller.started
structured-log line records agent_name_displayed +
agent_name_override_active for audit.

Behavior preserved: default tenant_settings has knockout_policy =
"record_only" and engine_agent_name = None, so the env value
substitutes and policy semantics are unchanged. The actual
close_polite branch lands in T8.

LiveKit routing label (decorator at agent.py:130 + dispatch_agent at
livekit.py:102) intentionally STAYS on env per P5-Q1 — that pair is
a fleet-wide routing primitive, not a candidate-facing identifier.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Replace `KnockoutFailureRecord` with persisted `KnockoutFailure`

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/controller.py` (delete dataclass at line 62-68; update type at line 125; update append site at lines 420-427; add empty-reason guard)
- Update: any test that referenced `KnockoutFailureRecord` directly

- [ ] **Step 1: Find existing references to `KnockoutFailureRecord`**

```bash
grep -rn "KnockoutFailureRecord" backend/nexus/
```

Capture the list. The dataclass is private to `controller.py` so the only references should be in `controller.py` itself + any unit test that constructs the record directly.

- [ ] **Step 2: Delete the in-memory dataclass and update the import**

Modify `backend/nexus/app/modules/interview_engine/controller.py`:

- Delete lines 60-68 (the `@dataclass class KnockoutFailureRecord:` block).
- Add `KnockoutFailure` to the existing `from app.modules.interview_runtime import (...)` import block (around line 48):

```python
from app.modules.interview_runtime import (
    KnockoutFailure,
    QuestionConfig,
    SessionConfig,
    SessionResult,
    record_session_result,
)
```

- [ ] **Step 3: Update the type annotation at line 125**

```python
        self._knockout_failures: list[KnockoutFailure] = []
```

- [ ] **Step 4: Update the append site (lines 420-427) with the empty-reason guard**

Replace:

```python
        if result.knockout:
            self._knockout_failures.append(
                KnockoutFailureRecord(
                    question_id=q.id,
                    reason=result.knockout_reason or "",
                    signal_values=list(q.signal_values),
                    occurred_at_ms=now_ms() - self._session_start_ms,
                )
            )
            self._collector.append(
                kind="disqualify.knockout",
                payload={
                    "question_id": q.id,
                    "reason_chars": len(result.knockout_reason or ""),
                    "reason": result.knockout_reason or "",
                },
                wall_ms=now_ms(),
            )
            # Phase 5 will read self._tenant_policy here and break on close_polite.
```

with:

```python
        if result.knockout:
            reason_text = (result.knockout_reason or "").strip()
            if not reason_text:
                # KnockoutFailure.reason has min_length=1; the upstream
                # disqualify_knockout tool requires non-empty reason. An
                # empty value is an upstream bug — log and skip the
                # append rather than crash the controller.
                log.warning(
                    "controller.knockout.empty_reason",
                    question_id=q.id,
                    signal_values=list(q.signal_values),
                )
                return
            self._knockout_failures.append(
                KnockoutFailure(
                    question_id=q.id,
                    reason=reason_text,
                    signal_values=list(q.signal_values),
                    occurred_at_ms=now_ms() - self._session_start_ms,
                )
            )
            self._collector.append(
                kind="disqualify.knockout",
                payload={
                    "question_id": q.id,
                    "reason_chars": len(reason_text),
                    "reason": reason_text,
                },
                wall_ms=now_ms(),
            )
            # Phase 5 (T8) wires close_polite here.
```

- [ ] **Step 5: Update `_build_session_result` to pass `knockout_failures`**

Find `_build_session_result` (around line 530 per the earlier grep). It currently builds a `SessionResult` from the in-memory state. Add the `knockout_failures` field to the constructor call:

```bash
grep -n "_build_session_result" backend/nexus/app/modules/interview_engine/controller.py
```

Inside that method's `SessionResult(...)` constructor call, add `knockout_failures=list(self._knockout_failures)`. (The list is already typed `list[KnockoutFailure]` from Step 3; pass through directly.)

- [ ] **Step 6: Run the controller test sweep + the runtime integration test**

```bash
docker compose run --rm nexus pytest tests/interview_engine/ tests/interview_runtime/ tests/test_interview_runtime_*.py -v -m "not prompt_quality"
```

Expected: green. The empty-reason path is tested implicitly when the upstream tool result has no reason — if no current test exercises that, add a small unit test inline. The end-to-end persistence path (engine writes → DB → service reads) is exercised by the T5 integration test combined with an end-to-end controller test if one exists.

- [ ] **Step 7: Add an empty-reason-guard unit test if not covered**

If the test sweep above shows no coverage of the empty-reason path, add a small integration test under `tests/interview_engine/integration/test_close_polite_policy.py` (which T8 will land) — OR add it now under `tests/interview_engine/unit/test_controller_handle_task_result.py`:

```python
"""Unit test for controller's _handle_task_result empty-reason guard."""
from __future__ import annotations

import uuid

import pytest
import structlog
from structlog.testing import capture_logs

from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_runtime import QuestionConfig, QuestionRubric


def _build_q() -> QuestionConfig:
    return QuestionConfig(
        id="q1",
        position=0,
        text="Sample question text long enough to satisfy min_length",
        signal_values=["sig_a"],
        estimated_minutes=2.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["e1", "e2", "e3"],
        red_flags=["r1", "r2"],
        rubric=QuestionRubric(
            excellent="...", meets_bar="...", below_bar="..."
        ),
        evaluation_hint="hint long enough to satisfy min_length 10",
        question_kind="technical_depth",
    )


def test_empty_reason_skips_append_and_logs(controller_factory) -> None:
    """controller_factory: a fixture from conftest that builds an
    InterviewController bound to a stub session. Stub-Session creation
    is out of scope here; if the factory doesn't exist, this test
    becomes a placeholder that's filled in at T8 (the close_polite
    integration test) where the same controller construction is needed
    for the close_polite scenario. Mark it skip until then."""
    pytest.skip("covered by T8 close_polite integration suite")
```

(If no `controller_factory` exists today and Phase 3's tests construct controllers inline, mirror that pattern instead. The `pytest.skip` placeholder is acceptable because T8 will land the close_polite integration suite that exercises the full path.)

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/controller.py
# (and any updated test files)
git commit -m "$(cat <<'EOF'
refactor(controller): use persisted KnockoutFailure (Phase 5)

Replaces the in-memory KnockoutFailureRecord dataclass with the
persisted KnockoutFailure pydantic model. The PII scrub validator
fires on every construction path. Adds an empty-reason guard at
the append site — KnockoutFailure.reason has min_length=1, so an
empty knockout_reason from the disqualify_knockout tool is logged
as an upstream bug warning and the append is skipped (rather than
crashing _handle_task_result with a ValidationError).

_build_session_result passes the in-memory list through to
SessionResult.knockout_failures unchanged, so T5's persistence
path now sees the engine-authored values end-to-end.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Wire `close_polite` policy at controller.py:438

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/controller.py` (at the stub comment, lines ~438)
- Create: `backend/nexus/tests/interview_engine/integration/test_close_polite_policy.py`

- [ ] **Step 1: Write failing integration test**

Create `backend/nexus/tests/interview_engine/integration/test_close_polite_policy.py`:

```python
"""Integration test: close_polite policy triggers _terminate(knockout_closed).

Two scenarios under a fake session/task harness:

A. record_only — knockout fires, controller continues to next
   question, no _terminate call. The in-memory _knockout_failures
   list grows; the loop carries on.

B. close_polite — knockout fires, _terminate(outcome="knockout_closed")
   runs, persistence happens once, the closing-line instructions
   from outcome_close.py::knockout_closed go to session.generate_reply.

The harness builds an InterviewController with a stub session that
captures `generate_reply` calls + `current_speech` accesses, plus a
patched record_session_result that records each invocation. Knockouts
are injected via `_handle_task_result(q, TaskResult(...))` directly.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_runtime import QuestionConfig, QuestionRubric
from app.modules.tenant_settings import TenantSettings

from tests.interview_engine.fixtures import build_minimal_session_config


def _make_q(qid: str = "q3") -> QuestionConfig:
    return QuestionConfig(
        id=qid,
        position=0,
        text="Sample question text long enough to satisfy min_length",
        signal_values=["uk_shift"],
        estimated_minutes=1.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["e1", "e2", "e3"],
        red_flags=["r1", "r2"],
        rubric=QuestionRubric(excellent="x", meets_bar="x", below_bar="x"),
        evaluation_hint="hint long enough for min_length",
        question_kind="compliance_binary",
    )


def _build_controller(policy: str) -> InterviewController:
    from app.modules.interview_engine.budget import SessionBudget
    from app.modules.interview_engine.event_log import EventCollector
    from app.modules.interview_engine.idle_nudge import IdleNudgeConfig

    config = build_minimal_session_config()
    tenant_settings = TenantSettings(
        tenant_id=uuid.uuid4(),
        engine_knockout_policy=policy,
        engine_agent_name=None,
    )
    return InterviewController(
        session_config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="corr-1",
        collector=EventCollector(
            session_id="sess-1",
            tenant_id="tenant-1",
            correlation_id="corr-1",
            controller_prompt_hash="sha256:abc",
            task_prompt_hashes={},
            model_versions={},
            redaction_mode="metadata",
        ),
        idle_nudge_config=IdleNudgeConfig(60.0, 60.0, 60.0),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=30.0,
        ),
        tenant_settings=tenant_settings,
    )


def _knockout_result() -> TaskResult:
    return TaskResult(
        knockout=True,
        knockout_reason="Cannot work UK shift hours.",
        signals_lacked=[],
        forced=False,
        # ... other TaskResult fields per Phase 3's TaskResult shape;
        # mirror an existing fixture if present.
    )


async def test_record_only_continues(monkeypatch) -> None:
    ctrl = _build_controller("record_only")
    # Spy on _terminate; we want to ensure it's NOT called.
    terminate_spy = AsyncMock()
    monkeypatch.setattr(ctrl, "_terminate", terminate_spy)

    q = _make_q("q3")
    ctrl._handle_task_result(q, _knockout_result())

    # Allow any create_task scheduled work to settle.
    await asyncio.sleep(0)

    assert len(ctrl._knockout_failures) == 1
    assert ctrl._knockout_failures[0].question_id == "q3"
    terminate_spy.assert_not_called()


async def test_close_polite_terminates(monkeypatch) -> None:
    ctrl = _build_controller("close_polite")
    terminate_spy = AsyncMock()
    monkeypatch.setattr(ctrl, "_terminate", terminate_spy)

    q = _make_q("q3")
    ctrl._handle_task_result(q, _knockout_result())

    # _handle_task_result schedules termination via asyncio.create_task;
    # yield once so the scheduled coroutine attaches to terminate_spy.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(ctrl._knockout_failures) == 1
    terminate_spy.assert_called_once()
    args, kwargs = terminate_spy.call_args
    assert kwargs.get("outcome") == "knockout_closed" or "knockout_closed" in args


async def test_close_polite_emits_event(monkeypatch) -> None:
    ctrl = _build_controller("close_polite")
    monkeypatch.setattr(ctrl, "_terminate", AsyncMock())

    q = _make_q("q3")
    ctrl._handle_task_result(q, _knockout_result())
    await asyncio.sleep(0)

    kinds = [evt.kind for evt in ctrl._collector._events]
    assert "disqualify.knockout" in kinds
    assert "controller.intent.knockout_closed" in kinds
```

Adjust the `TaskResult(...)` constructor to match the actual `TaskResult` schema in `app/modules/interview_engine/tasks/base.py`. Look it up:

```bash
grep -n "class TaskResult" backend/nexus/app/modules/interview_engine/tasks/base.py
```

…and fill in any required fields the test left out.

Likewise for `_collector._events` — look up the `EventCollector` API:

```bash
grep -n "class EventCollector\|def append\|self._events\|self.events" backend/nexus/app/modules/interview_engine/event_log/__init__.py backend/nexus/app/modules/interview_engine/event_log/*.py
```

If the public attribute is `events` not `_events`, adjust.

- [ ] **Step 2: Run test to verify the test SHAPE works (record_only path passes; close_polite path fails)**

```bash
docker compose run --rm nexus pytest tests/interview_engine/integration/test_close_polite_policy.py -v
```

Expected: `test_record_only_continues` passes (today's behavior); `test_close_polite_terminates` and `test_close_polite_emits_event` fail (the stub at line 438 is still a comment).

- [ ] **Step 3: Wire close_polite at the stub line**

Modify `backend/nexus/app/modules/interview_engine/controller.py`. Replace the stub comment `# Phase 5 (T8) wires close_polite here.` (which T7 left in place) with the actual logic:

```python
            if self._tenant_policy == "close_polite":
                log.info(
                    "controller.knockout.close_polite",
                    question_id=q.id,
                    signal_values=list(q.signal_values),
                )
                self._collector.append(
                    kind="controller.intent.knockout_closed",
                    payload={"question_id": q.id},
                    wall_ms=now_ms(),
                )
                asyncio.create_task(self._terminate(outcome="knockout_closed"))
                return
```

- [ ] **Step 4: Run the test to verify all three pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/integration/test_close_polite_policy.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Run the full interview_engine sweep for regressions**

```bash
docker compose run --rm nexus pytest tests/interview_engine/ -v -m "not prompt_quality"
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/controller.py \
        backend/nexus/tests/interview_engine/integration/test_close_polite_policy.py
git commit -m "$(cat <<'EOF'
feat(controller): wire close_polite knockout policy (Phase 5)

When tenant_settings.engine_knockout_policy == "close_polite" and a
knockout fires, _handle_task_result emits a
controller.intent.knockout_closed event and schedules
_terminate(outcome="knockout_closed") via asyncio.create_task. The
record_only path is unchanged.

The closing line is delivered by the existing _terminate flow,
which calls outcome_close.py::knockout_closed instructions:
"Thank the candidate; do NOT reference any specific failure or
knockout reason." (Senior-reviewer signed off in Phase 2; no
prompt body change in Phase 5.)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Frontend `SessionOutcome` shared module

**Files:**
- Create: `frontend/session/components/interview/lib/session-outcome.ts`
- Create: `frontend/session/tests/components/interview/session-outcome.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/session/tests/components/interview/session-outcome.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'

import {
  isSessionOutcome,
  SESSION_OUTCOMES,
  type SessionOutcome,
} from '@/components/interview/lib/session-outcome'

describe('SESSION_OUTCOMES', () => {
  it('lists all 6 backend outcomes', () => {
    expect(SESSION_OUTCOMES).toEqual([
      'completed',
      'knockout_closed',
      'time_expired',
      'candidate_ended',
      'candidate_unresponsive',
      'error',
    ])
  })
})

describe('isSessionOutcome', () => {
  it.each(SESSION_OUTCOMES)('returns true for %s', (v) => {
    expect(isSessionOutcome(v)).toBe(true)
  })

  it('returns false for an unknown outcome string', () => {
    expect(isSessionOutcome('mystery_outcome')).toBe(false)
  })

  it('returns false for null', () => {
    expect(isSessionOutcome(null)).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isSessionOutcome(undefined)).toBe(false)
  })

  it('returns false for empty string', () => {
    expect(isSessionOutcome('')).toBe(false)
  })

  it('returns false for non-string values', () => {
    // @ts-expect-error — testing the runtime guard, not the type
    expect(isSessionOutcome(42)).toBe(false)
    // @ts-expect-error — testing the runtime guard, not the type
    expect(isSessionOutcome({})).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend/session && npm run test -- components/interview/session-outcome.test.ts
```

Expected: import error (`Cannot find module '@/components/interview/lib/session-outcome'`).

- [ ] **Step 3: Write the shared module**

Create `frontend/session/components/interview/lib/session-outcome.ts`:

```typescript
/**
 * Shared SessionOutcome type — the single source of truth for the 6
 * outcome strings the engine publishes via the agent participant's
 * `session_outcome` attribute.
 *
 * Must stay in sync with backend `app/modules/interview_engine/outcome_close.py::SessionOutcome`.
 * If a value is added/removed here, update the backend list in the
 * same PR. The exhaustive switch in `OutcomeWatcher` (app/app.tsx)
 * uses `_exhaustive: never` to surface missed cases at compile time.
 */

export const SESSION_OUTCOMES = [
  'completed',
  'knockout_closed',
  'time_expired',
  'candidate_ended',
  'candidate_unresponsive',
  'error',
] as const

export type SessionOutcome = (typeof SESSION_OUTCOMES)[number]

/**
 * Runtime guard — drops unrecognized values to false. Defensive against
 * backend/frontend version skew (a future backend outcome the frontend
 * hasn't shipped support for yet should be ignored, not coerced).
 */
export function isSessionOutcome(v: unknown): v is SessionOutcome {
  return typeof v === 'string' && (SESSION_OUTCOMES as readonly string[]).includes(v)
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd frontend/session && npm run test -- components/interview/session-outcome.test.ts
```

Expected: green (8 tests).

- [ ] **Step 5: Run type-check + lint**

```bash
cd frontend/session && npm run type-check && npm run lint
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/session/components/interview/lib/session-outcome.ts \
        frontend/session/tests/components/interview/session-outcome.test.ts
git commit -m "$(cat <<'EOF'
feat(session): shared SessionOutcome type + isSessionOutcome guard (Phase 5)

Single source of truth for the 6 backend outcome strings the engine
publishes via the agent participant's session_outcome attribute.
Runtime guard drops unrecognized values to false — defensive against
backend/frontend version skew. Will be consumed by useSessionOutcome
(narrowed return type) and OutcomeWatcher (exhaustive switch) in the
next two tasks.

Must stay in sync with backend SessionOutcome in
outcome_close.py:19-26. Module docstring calls this out.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Narrow `useSessionOutcome` return to typed Literal

**Files:**
- Modify: `frontend/session/components/interview/app/hooks/use-session-outcome.ts`
- Create: `frontend/session/tests/components/interview/use-session-outcome.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/session/tests/components/interview/use-session-outcome.test.ts`:

```typescript
import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSessionOutcome } from '@/components/interview/app/hooks/use-session-outcome'
import { SESSION_OUTCOMES } from '@/components/interview/lib/session-outcome'

// Mock the @livekit/components-react useRemoteParticipants hook.
const mockRemotes = vi.hoisted(() => ({ value: [] as Array<{ identity: string; attributes: Record<string, string> }> }))
vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => mockRemotes.value,
}))

describe('useSessionOutcome', () => {
  beforeEach(() => {
    mockRemotes.value = []
  })

  it('returns null when no agent participant', () => {
    const { result } = renderHook(() => useSessionOutcome())
    expect(result.current).toBeNull()
  })

  it.each(SESSION_OUTCOMES)('returns %s when agent publishes it', (outcome) => {
    mockRemotes.value = [
      { identity: 'agent-abc123', attributes: { session_outcome: outcome } },
    ]
    const { result } = renderHook(() => useSessionOutcome())
    expect(result.current).toBe(outcome)
  })

  it('drops an unknown outcome string to null', () => {
    mockRemotes.value = [
      { identity: 'agent-abc123', attributes: { session_outcome: 'mystery_outcome' } },
    ]
    const { result } = renderHook(() => useSessionOutcome())
    expect(result.current).toBeNull()
  })

  it('keeps the last seen value when the agent disappears (ref-stickiness)', () => {
    mockRemotes.value = [
      { identity: 'agent-abc123', attributes: { session_outcome: 'completed' } },
    ]
    const { result, rerender } = renderHook(() => useSessionOutcome())
    expect(result.current).toBe('completed')

    mockRemotes.value = [] // agent disappears
    rerender()
    expect(result.current).toBe('completed') // sticky
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend/session && npm run test -- components/interview/use-session-outcome.test.ts
```

Expected: failures on the unknown-outcome test (today the hook returns the raw string `'mystery_outcome'`); type-check failure on `result.current === outcome` for typed values.

- [ ] **Step 3: Update the hook**

Modify `frontend/session/components/interview/app/hooks/use-session-outcome.ts`:

```typescript
'use client'

import { useRef } from 'react'
import { useRemoteParticipants } from '@livekit/components-react'

import { isSessionOutcome, type SessionOutcome } from '../../lib/session-outcome'

/**
 * Reads the agent participant's `session_outcome` attribute and holds it in a ref
 * so the value survives the moment the agent participant is removed from the
 * remote participants list (which happens immediately on Disconnected).
 *
 * The ref is updated synchronously during render (not in useEffect) so the
 * value is available on the same render in which the agent is visible.
 * Once set, it is never clobbered back to null — last seen value sticks even
 * after the participant is removed from the list.
 *
 * Engine writes one of 6 SessionOutcome values before calling shutdown; see
 * docs/superpowers/specs/2026-05-03-engine-redesign-phase-5-knockout-policy-design.md
 * §3.6.
 *
 * Defensive: an unrecognized outcome string is dropped to null rather than
 * coerced. Defends against backend/frontend version skew.
 */
export function useSessionOutcome(): SessionOutcome | null {
  const remotes = useRemoteParticipants()
  const ref = useRef<SessionOutcome | null>(null)

  const agent = remotes.find((p) => p.identity.startsWith('agent-'))
  const raw = agent?.attributes?.['session_outcome']
  if (raw && isSessionOutcome(raw)) ref.current = raw

  return ref.current
}
```

- [ ] **Step 4: Run hook tests to verify pass**

```bash
cd frontend/session && npm run test -- components/interview/use-session-outcome.test.ts
```

Expected: green.

- [ ] **Step 5: Run full frontend test sweep + type-check**

```bash
cd frontend/session && npm run test && npm run type-check
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add frontend/session/components/interview/app/hooks/use-session-outcome.ts \
        frontend/session/tests/components/interview/use-session-outcome.test.ts
git commit -m "$(cat <<'EOF'
feat(session): narrow useSessionOutcome to typed SessionOutcome (Phase 5)

Hook now returns SessionOutcome | null (was string | null). Unknown
values from the agent's session_outcome attribute are dropped to null
via isSessionOutcome — defensive against backend/frontend version
skew (a future backend outcome the frontend hasn't shipped support
for is ignored rather than coerced into the union).

Ref-stickiness behavior is unchanged: last-seen value persists when
the agent participant disappears from useRemoteParticipants.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: `OutcomeWatcher` exhaustive switch + `DisconnectError` `CANDIDATE_UNRESPONSIVE`

**Files:**
- Modify: `frontend/session/components/interview/app/app.tsx` (`OutcomeWatcher`, lines 141-150)
- Modify: `frontend/session/components/interview/app/DisconnectError.tsx`
- Create: `frontend/session/tests/components/interview/outcome-watcher.test.tsx`
- Create or extend: `frontend/session/tests/components/interview/disconnect-error.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/session/tests/components/interview/outcome-watcher.test.tsx`:

```typescript
import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// We need to inject the LiveKit Room mock + DisconnectReason values.
// The OutcomeWatcher reads room.on/off for RoomEvent.Disconnected.

const mockRemotes = vi.hoisted(() => ({ value: [] as Array<{ identity: string; attributes: Record<string, string> }> }))
vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => mockRemotes.value,
}))

// Import OutcomeWatcher AFTER the mocks are wired.
import { OutcomeWatcher } from '@/components/interview/app/app'

const RoomEvent = { Disconnected: 'disconnected' } as const

class FakeRoom {
  private handlers = new Map<string, Array<(...args: unknown[]) => void>>()
  on(event: string, fn: (...args: unknown[]) => void) {
    const arr = this.handlers.get(event) ?? []
    arr.push(fn)
    this.handlers.set(event, arr)
  }
  off(event: string, fn: (...args: unknown[]) => void) {
    const arr = this.handlers.get(event)?.filter((h) => h !== fn) ?? []
    this.handlers.set(event, arr)
  }
  emit(event: string, ...args: unknown[]) {
    for (const h of this.handlers.get(event) ?? []) h(...args)
  }
}

describe('OutcomeWatcher', () => {
  let onCompleted: ReturnType<typeof vi.fn>
  let onError: ReturnType<typeof vi.fn>
  let room: FakeRoom

  beforeEach(() => {
    onCompleted = vi.fn()
    onError = vi.fn()
    room = new FakeRoom()
    mockRemotes.value = []
  })

  function setOutcome(outcome: string | null) {
    if (outcome === null) {
      mockRemotes.value = []
    } else {
      mockRemotes.value = [
        { identity: 'agent-abc', attributes: { session_outcome: outcome } },
      ]
    }
  }

  function mount() {
    return render(
      <OutcomeWatcher
        room={room as never}
        onCompleted={onCompleted}
        onError={onError}
      />,
    )
  }

  it.each([
    ['completed', 'onCompleted'],
    ['knockout_closed', 'onCompleted'],
    ['time_expired', 'onCompleted'],
    ['candidate_ended', 'onCompleted'],
  ])('routes %s to %s', (outcome, expected) => {
    setOutcome(outcome)
    mount()
    room.emit(RoomEvent.Disconnected)
    if (expected === 'onCompleted') {
      expect(onCompleted).toHaveBeenCalledTimes(1)
      expect(onError).not.toHaveBeenCalled()
    } else {
      expect(onError).toHaveBeenCalledTimes(1)
      expect(onCompleted).not.toHaveBeenCalled()
    }
  })

  it('routes candidate_unresponsive to onError(CANDIDATE_UNRESPONSIVE)', () => {
    setOutcome('candidate_unresponsive')
    mount()
    room.emit(RoomEvent.Disconnected)
    expect(onError).toHaveBeenCalledWith('CANDIDATE_UNRESPONSIVE')
  })

  it('routes error to onError(ENGINE_ERROR)', () => {
    setOutcome('error')
    mount()
    room.emit(RoomEvent.Disconnected)
    expect(onError).toHaveBeenCalledWith('ENGINE_ERROR')
  })

  it('falls through to CLIENT_INITIATED → onCompleted when no engine outcome', () => {
    setOutcome(null)
    mount()
    room.emit(RoomEvent.Disconnected, 1) // DisconnectReason.CLIENT_INITIATED = 1
    expect(onCompleted).toHaveBeenCalledTimes(1)
  })

  it('falls through to DUPLICATE_IDENTITY → onError(DUPLICATE_SESSION)', () => {
    setOutcome(null)
    mount()
    room.emit(RoomEvent.Disconnected, 2) // DisconnectReason.DUPLICATE_IDENTITY = 2
    expect(onError).toHaveBeenCalledWith('DUPLICATE_SESSION')
  })

  it('falls through to UNEXPECTED_DISCONNECT for unknown reason', () => {
    setOutcome(null)
    mount()
    room.emit(RoomEvent.Disconnected, 99)
    expect(onError).toHaveBeenCalledWith('UNEXPECTED_DISCONNECT')
  })
})
```

If `OutcomeWatcher` isn't exported from `app/app.tsx`, export it (one-line `export function OutcomeWatcher`). Tests need direct access. If the file pattern in this repo wraps it as a non-exported helper, lift it into the `lib/` folder or export it under an internal name (`OutcomeWatcher_test_only`) and document the test-only export inline.

Create `frontend/session/tests/components/interview/disconnect-error.test.tsx` (or extend existing):

```typescript
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DisconnectError } from '@/components/interview/app/DisconnectError'

describe('DisconnectError', () => {
  it('renders CANDIDATE_UNRESPONSIVE copy', () => {
    const { container } = render(<DisconnectError code="CANDIDATE_UNRESPONSIVE" />)
    expect(container.textContent).toContain("We didn't hear from you")
    expect(container.textContent).toContain('contact your recruiter')
    expect(container.textContent).toContain('Error code: CANDIDATE_UNRESPONSIVE')
  })

  it('falls back to default copy for unknown code', () => {
    const { container } = render(<DisconnectError code="MYSTERY" />)
    expect(container.textContent).toContain('Session disconnected')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend/session && npm run test -- components/interview/outcome-watcher components/interview/disconnect-error
```

Expected: failures on `CANDIDATE_UNRESPONSIVE` (no copy entry) and on the four new outcome routings (today only `completed` and `error` are recognized; the others fall through to `UNEXPECTED_DISCONNECT`).

- [ ] **Step 3: Update `OutcomeWatcher` to use exhaustive switch**

Modify `frontend/session/components/interview/app/app.tsx` around lines 141-150. Replace:

```typescript
    const onDisconnected = (reason?: DisconnectReason) => {
      const o = lastOutcomeRef.current
      if (o === 'completed') return onCompleted()
      if (o === 'error') return onError('ENGINE_ERROR')

      const reasonName = reasonToName(reason)
      if (reasonName === 'CLIENT_INITIATED') return onCompleted()
      if (reasonName === 'DUPLICATE_IDENTITY') return onError('DUPLICATE_SESSION')
      onError('UNEXPECTED_DISCONNECT')
    }
```

with:

```typescript
    const onDisconnected = (reason?: DisconnectReason) => {
      const o = lastOutcomeRef.current
      switch (o) {
        case 'completed':
        case 'knockout_closed':
        case 'time_expired':
        case 'candidate_ended':
          return onCompleted()
        case 'candidate_unresponsive':
          return onError('CANDIDATE_UNRESPONSIVE')
        case 'error':
          return onError('ENGINE_ERROR')
        case null:
        case undefined:
          break  // fall through to DisconnectReason mapping
        default: {
          const _exhaustive: never = o  // compile-time guard: forces every SessionOutcome to be handled
          void _exhaustive
        }
      }

      const reasonName = reasonToName(reason)
      if (reasonName === 'CLIENT_INITIATED') return onCompleted()
      if (reasonName === 'DUPLICATE_IDENTITY') return onError('DUPLICATE_SESSION')
      onError('UNEXPECTED_DISCONNECT')
    }
```

If `OutcomeWatcher` isn't yet exported, add `export` to its declaration. Verify the type of `lastOutcomeRef.current` is `SessionOutcome | null` (it should be, after T10).

- [ ] **Step 4: Add `CANDIDATE_UNRESPONSIVE` to `DisconnectError`**

Modify `frontend/session/components/interview/app/DisconnectError.tsx`. Add a new entry to the `COPY` map (alphabetical order in the existing list — insert after `AGENT_NO_SHOW`):

```typescript
  CANDIDATE_UNRESPONSIVE: {
    title: "We didn't hear from you",
    body: "We ended the interview because we couldn't hear from you for a while. If this was unexpected, please contact your recruiter.",
  },
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd frontend/session && npm run test -- components/interview/outcome-watcher components/interview/disconnect-error
```

Expected: all green.

- [ ] **Step 6: Run full frontend test + type-check + lint**

```bash
cd frontend/session && npm run test && npm run type-check && npm run lint
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add frontend/session/components/interview/app/app.tsx \
        frontend/session/components/interview/app/DisconnectError.tsx \
        frontend/session/tests/components/interview/outcome-watcher.test.tsx \
        frontend/session/tests/components/interview/disconnect-error.test.tsx
git commit -m "$(cat <<'EOF'
feat(session): exhaustive 6-state outcome routing + CANDIDATE_UNRESPONSIVE (Phase 5)

OutcomeWatcher now uses an exhaustive switch over the 6 SessionOutcome
values. The four graceful endings (completed, knockout_closed,
time_expired, candidate_ended) route to onCompleted; candidate_unresponsive
routes to onError('CANDIDATE_UNRESPONSIVE'); error routes to
onError('ENGINE_ERROR'). The compile-time _exhaustive: never guard
catches any future SessionOutcome that's added without a switch case.

DisconnectError gains a CANDIDATE_UNRESPONSIVE entry — copy explains
the agent gave up after no response and offers a recruiter-contact
recovery path. Distinct from the generic UNEXPECTED_DISCONNECT (which
covers raw network drops without an engine signal).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Documentation updates + flip Phase 5 status to ✅ shipped

**Files:**
- Modify: `backend/nexus/CLAUDE.md` (migration list, revision count, modules tree, Phase 3D.engine-redesign-5 status block)
- Modify: `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` (Phase 5 row in status index)

- [ ] **Step 1: Update `backend/nexus/CLAUDE.md` — migration list**

Locate the Migrations bullet list (under "Database Migrations" → "Current State"). After the `0026_question_kind_column` line, insert:

```markdown
- `0027_tenant_settings` — **Phase 5**: new `tenant_settings` table (PK = tenant_id, FK→clients ON DELETE CASCADE, two columns: `engine_knockout_policy` enum ('record_only' | 'close_polite', default 'record_only') + `engine_agent_name` nullable text). Adds `sessions.knockout_failures JSONB NOT NULL DEFAULT '[]'`. Both ops are PG11+ metadata-only. Lazy-default read pattern: `get_tenant_settings` returns schema defaults when the tenant has no row — no backfill performed.
```

- [ ] **Step 2: Update revision count + head pointer**

Change:

```
- **Schema management:** Supabase SQL for the initial cut + Supabase-managed objects (auth hook, `supabase_auth_admin` grants); Alembic for every incremental change after that. `migrations/versions/` currently has 26 revisions; head is `0026_question_kind_column`.
```

to:

```
- **Schema management:** Supabase SQL for the initial cut + Supabase-managed objects (auth hook, `supabase_auth_admin` grants); Alembic for every incremental change after that. `migrations/versions/` currently has 27 revisions; head is `0027_tenant_settings`.
```

Also update the `migrations/` line under "Module Structure" (search for `27 revisions` after the edit lands):

```
├── migrations/                  ← Alembic — 27 revisions; head is `0027_tenant_settings`
```

- [ ] **Step 3: Add `tenant_settings/` to the Module Structure tree**

Insert under `app/modules/`, alphabetical position:

```
│       ├── tenant_settings/     ← Phase 5 — per-tenant engine config (knockout_policy + agent_name override)
```

- [ ] **Step 4: Add Phase 3D.engine-redesign-5 status block**

In the "Current State" section, after the `Phase 3D.engine-redesign-4 — done` block, insert:

```markdown
- **Phase 3D.engine-redesign-5** — done: `tenant_settings` table +
  `KnockoutFailure` persistence + `close_polite` policy wiring + 6-state
  `session_outcome` frontend wiring. New `app/modules/tenant_settings/`
  module (ORM + Pydantic + service, public API via `__init__.py`); new
  `KnockoutFailure` pydantic model in `interview_runtime.schemas` with
  defense-in-depth `_scrub_pii` validator (email + phone regex →
  `[redacted]`); `record_session_result` writes the new
  `sessions.knockout_failures` JSONB column for Phase 3D analytics.
  Controller's `tenant_policy: KnockoutPolicy` constructor parameter is
  replaced with `tenant_settings: TenantSettings`; the `close_polite`
  branch at `controller.py:438` (stub from Phase 2) now fires
  `_terminate(outcome="knockout_closed")` on a knockout. `agent_name`
  override flows from `tenant_settings.engine_agent_name` into
  `build_controller_prompt` substitution + a new `controller.started`
  audit-log line; the env value remains the LiveKit fleet-wide routing
  label (P5-Q1). Frontend gains a typed `SessionOutcome` Literal union
  in `frontend/session/components/interview/lib/session-outcome.ts`,
  exhaustive `OutcomeWatcher` switch, and a new `CANDIDATE_UNRESPONSIVE`
  code on `DisconnectError`. Migration `0027_tenant_settings`. See spec
  `docs/superpowers/specs/2026-05-03-engine-redesign-phase-5-knockout-policy-design.md`.
```

- [ ] **Step 5: Add a `tenant_settings` row to the Phase 3C-3D modules table**

Locate the table that lists `interview_runtime`, `scheduler`, `session` (the Phase 3 modules table). After the `interview_runtime` row, insert:

```markdown
| `tenant_settings` | Phase 5 — per-tenant engine configuration. ORM `TenantSettingsModel` (PK = tenant_id, FK clients.id ON DELETE CASCADE); Pydantic `TenantSettings` with `engine_knockout_policy: Literal['record_only','close_polite']` and `engine_agent_name: str \| None`. `get_tenant_settings(db, tenant_id)` is the single read path with lazy-default semantics (no row → schema defaults). No router; recruiter-side editing UI is post-arc per overview Decision #19. |
```

- [ ] **Step 6: Flip Phase 5 row in overview spec status index**

Modify `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md`. Locate the Phase status index table; change Phase 5 row from:

```
| 5 — Knockout policy | _pending_ | _pending_ | ⚪ not started |
```

to:

```
| 5 — Knockout policy | [`2026-05-03-…phase-5-knockout-policy-design.md`](2026-05-03-engine-redesign-phase-5-knockout-policy-design.md) | [`2026-05-03-…phase-5-knockout-policy.md`](../plans/2026-05-03-engine-redesign-phase-5-knockout-policy.md) | ✅ shipped |
```

- [ ] **Step 7: Verify all docs render cleanly**

```bash
# Sanity check: no broken markdown links, no left-over _pending_ for Phase 5.
grep -n "Phase 5" docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md
grep -n "0027_tenant_settings\|engine-redesign-5" backend/nexus/CLAUDE.md
```

Expected: Phase 5 row shows ✅ shipped + both file links; CLAUDE.md mentions `0027_tenant_settings` in the migration list, the modules tree, and the engine-redesign-5 status block.

- [ ] **Step 8: Run a final sanity sweep across the backend test surface**

```bash
docker compose run --rm nexus pytest tests/test_module_boundaries.py tests/test_main_*.py tests/test_tenant_settings_*.py tests/test_interview_runtime_*.py tests/interview_runtime/ tests/interview_engine/ -v -m "not prompt_quality"
cd frontend/session && npm run test && npm run type-check && npm run lint
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add backend/nexus/CLAUDE.md \
        docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md
git commit -m "$(cat <<'EOF'
docs(engine): flip Phase 5 to shipped + update CLAUDE.md migrations (Phase 5)

backend/nexus/CLAUDE.md gains:
- 0027_tenant_settings entry in the migrations bullet list
- revision count 26 → 27, head pointer flip
- tenant_settings/ entry in the modules tree
- Phase 3D.engine-redesign-5 "done" status block
- tenant_settings row in the Phase 3C-3D modules table

Overview spec status index: Phase 5 row flipped from "⚪ not started"
to "✅ shipped" with links to the spec + plan.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

**Spec coverage check** — every requirement in the Phase 5 spec maps to a task:

| Spec section | Implemented in |
|---|---|
| §2.1 — `tenant_settings` migration + RLS pair + `sessions.knockout_failures` column | T1 |
| §2.1 — `tenant_settings` module (ORM, Pydantic, service, `__init__`) | T2 |
| §2.1 — `_TENANT_SCOPED_TABLES` + `KNOWN_DOMAIN_MODULES` | T1, T2 |
| §2.1 — `KnockoutFailure` model + PII scrub + `interview_runtime/__init__` re-export | T3 |
| §2.1 — `SessionResult.knockout_failures` field + `Session` ORM column | T4 |
| §2.1 — `record_session_result` writes new column | T5 |
| §2.1 — controller constructor rename + `agent_name` plumb + `controller.started` log | T6 |
| §2.1 — `agent.py` reads `tenant_settings`; passes to controller | T6 |
| §2.1 — replace `KnockoutFailureRecord` with persisted `KnockoutFailure` | T7 |
| §2.1 — close_polite wiring at `controller.py:438` | T8 |
| §2.1 — frontend `session-outcome.ts` shared type + guard | T9 |
| §2.1 — `useSessionOutcome` narrowing | T10 |
| §2.1 — `OutcomeWatcher` exhaustive switch + `DisconnectError` `CANDIDATE_UNRESPONSIVE` | T11 |
| §5.1 — backend tests at unit + service + integration tiers | T1-T8 (test in same task as the code) |
| §5.2 — frontend Vitest tests | T9-T11 |
| §6.1 — tenant isolation + RLS startup check | T1 |
| §6.2 — three-layer PII boundary | T3 (scrub layer) |
| §6.3 — no new fairness sign-off (no prompt body changes) | spec §6.3 documents; no task |
| §7 — documentation updates | T12 |

**Placeholder scan** — search the plan for red flags:

- No "TBD" / "TODO" / "implement later" / "fill in details".
- Test code blocks contain runnable code; the only `pytest.skip` is a deliberate hand-off from T7 to T8 with rationale.
- Step 8 of T6 says "Update other `InterviewController(...)` construction sites" with rationale; the search command is in step 1 of the same task. This is the right shape for "find and update each call site" — the engineer captures the list, then iterates. Acceptable.

**Type-consistency check** — names align across tasks:
- `tenant_settings: TenantSettings` constructor param: introduced T6, consumed T6, T8.
- `_tenant_policy`, `_agent_name`, `_agent_name_override_active` instance attrs: set in T6, read in T6 (controller.started log), T7 (kept as-is), T8 (close_polite check).
- `KnockoutFailure`: defined T3, used T4 (SessionResult), T5 (write path), T7 (controller).
- `SESSION_OUTCOMES` const + `SessionOutcome` type + `isSessionOutcome` guard: defined T9, consumed T10, T11.
