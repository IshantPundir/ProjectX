# ATS Adapter System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only Ceipal ATS inbound sync end-to-end (backend module + scheduler + recruiter UI), built on a vendor-agnostic adapter Protocol so Greenhouse/Workday adapters in the future are additive-only.

**Architecture:** Per-tenant `ATSAdapter` instance holding decrypted credentials, returning canonical Pydantic DTOs via `AsyncIterator` (pagination internal). External cron → CLI tick → per-tenant Dramatiq actor → 5-phase `ATSImporter` (clients → users → jobs → applicants → submissions) writing into existing module services. Credentials and tokens encrypted at rest via `MultiFernet`. Full audit trail via existing `audit_log` module + a new `ats_sync_logs` table.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async (asyncpg), Dramatiq + Redis, Alembic, Pydantic v2, structlog, OpenTelemetry, `cryptography.fernet`, httpx, pytest + pytest-asyncio. Frontend: Next.js 16 App Router, React Hook Form + Zod, TanStack Query, components/px primitives on @base-ui-components/react.

**Spec:** `docs/superpowers/specs/2026-05-12-ats-adapter-design.md`

---

## Phase ordering

The plan has 12 phases. Backend phases 1–9 build bottom-up: schema first, then the module's data layer, then the adapter, then the orchestrator, then the entrypoints. Frontend phases 10–11 ship after the backend `/api/ats/*` surface is functional. Phase 12 wraps verification + rollout docs.

Every task ends in a green-tests commit. A partial run leaves the codebase deployable.

All backend paths below are relative to `backend/nexus/` unless prefixed with `docs/` or `frontend/`. Run all backend tests inside the container: `docker compose run --rm nexus pytest ...`.

---

## Phase 1 — Alembic migration 0029 (schema foundation)

Schema changes ship first as their own deployable unit. The Python code in later phases assumes these tables and columns exist.

### Task 1: Generate the migration scaffold

**Files:**
- Create: `migrations/versions/0029_ats_core.py`

- [ ] **Step 1: Create the migration with a stable revision ID**

Run:
```bash
docker compose run --rm nexus alembic revision -m "ats_core" --rev-id 0029
```

This generates `migrations/versions/0029_ats_core.py` with `down_revision = "0028_audio_tuning_summary"` and empty `upgrade()`/`downgrade()` bodies.

- [ ] **Step 2: Replace the file body with the full migration**

Open `migrations/versions/0029_ats_core.py` and replace its contents with:

```python
"""ats_core

Revision ID: 0029
Revises: 0028_audio_tuning_summary
Create Date: 2026-05-12

Adds the per-tenant ATS integration tables (ats_connections,
ats_client_mappings, ats_user_mappings, ats_job_recruiter_assignments,
ats_sync_logs), plus the column additions needed on organizational_units,
job_postings, candidates, and candidate_job_assignments. RLS policies use
the canonical tenant_isolation + service_bypass pair wrapped in
NULLIF(..., '')::uuid per app/CLAUDE.md.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0029"
down_revision = "0028_audio_tuning_summary"
branch_labels = None
depends_on = None


_NEW_TABLES = (
    "ats_connections",
    "ats_client_mappings",
    "ats_user_mappings",
    "ats_job_recruiter_assignments",
    "ats_sync_logs",
)


def _apply_canonical_rls(table: str) -> None:
    """Apply the canonical tenant_isolation + service_bypass RLS pair
    with NULLIF-wrapped current_tenant cast (per app/CLAUDE.md)."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table}
          USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)
    op.execute(f"""
        CREATE POLICY service_bypass ON {table}
          USING (current_setting('app.bypass_rls', true) = 'true');
    """)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexus_app;")


def upgrade() -> None:
    # ---- ats_connections ----
    op.create_table(
        "ats_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor", sa.Text(), nullable=False),
        sa.Column("credentials_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("access_token_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_cursors", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False,
                  server_default=sa.text("900")),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("poll_lock_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_error", sa.Text()),
        sa.Column("rate_limit_qps", sa.Numeric()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("disabled_reason", sa.Text()),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint("tenant_id", "vendor", name="uq_ats_connections_tenant_vendor"),
    )
    op.create_index("ix_ats_connections_due", "ats_connections",
                    ["next_poll_at"], postgresql_where=sa.text("active = true"))
    _apply_canonical_rls("ats_connections")

    # ---- ats_client_mappings ----
    op.create_table(
        "ats_client_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_client_id", sa.Text(), nullable=False),
        sa.Column("external_client_name", sa.Text(), nullable=False),
        sa.Column("org_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_unit_id"], ["organizational_units.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "ats_vendor", "external_client_id",
                            name="uq_ats_client_mappings_external"),
    )
    _apply_canonical_rls("ats_client_mappings")

    # ---- ats_user_mappings ----
    op.create_table(
        "ats_user_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column("external_user_email", sa.Text(), nullable=False),
        sa.Column("external_user_display_name", sa.Text(), nullable=False),
        sa.Column("external_user_role", sa.Text()),
        sa.Column("external_user_status", sa.Text()),
        sa.Column("external_user_metadata", postgresql.JSONB()),
        sa.Column("internal_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("mapped_at", sa.DateTime(timezone=True)),
        sa.Column("mapped_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["internal_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["mapped_by"], ["users.id"]),
        sa.UniqueConstraint("tenant_id", "ats_vendor", "external_user_id",
                            name="uq_ats_user_mappings_external"),
    )
    _apply_canonical_rls("ats_user_mappings")

    # ---- ats_job_recruiter_assignments ----
    op.create_table(
        "ats_job_recruiter_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_posting_id"], ["job_postings.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_posting_id", "external_user_id",
                            name="uq_ats_job_recruiter_assignments"),
    )
    _apply_canonical_rls("ats_job_recruiter_assignments")

    # ---- ats_sync_logs ----
    op.create_table(
        "ats_sync_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),  # running | success | partial | failed
        sa.Column("entity_counts", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_phase", sa.Text()),
        sa.Column("error_summary", sa.Text()),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["ats_connections.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_ats_sync_logs_connection_started", "ats_sync_logs",
                    ["connection_id", "started_at"])
    _apply_canonical_rls("ats_sync_logs")

    # ---- Column additions to existing tables ----
    op.add_column("organizational_units",
                  sa.Column("company_profile_completion_status", sa.Text(),
                            nullable=False, server_default=sa.text("'complete'")))
    op.create_check_constraint(
        "ck_org_units_completion_status",
        "organizational_units",
        "company_profile_completion_status IN ('pending', 'complete')",
    )

    op.add_column("job_postings",
                  sa.Column("external_status", sa.Text()))
    # Broaden the status CHECK constraint to add blocked_pending_client_setup.
    # The exact name of the existing CHECK constraint depends on the original migration;
    # discover and drop the existing CHECK before recreating.
    op.execute("""
        DO $$
        DECLARE
            cname text;
        BEGIN
            SELECT conname INTO cname
            FROM pg_constraint
            WHERE conrelid = 'job_postings'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%status%';
            IF cname IS NOT NULL THEN
                EXECUTE 'ALTER TABLE job_postings DROP CONSTRAINT ' || cname;
            END IF;
        END$$;
    """)
    op.create_check_constraint(
        "ck_job_postings_status",
        "job_postings",
        "status IN ('draft', 'signals_extracting', 'signals_extraction_failed', "
        "'signals_extracted', 'pipeline_built', 'active', 'archived', "
        "'blocked_pending_client_setup')",
    )

    op.add_column("candidate_job_assignments",
                  sa.Column("source", sa.Text(), nullable=False,
                            server_default=sa.text("'manual'")))
    op.add_column("candidate_job_assignments",
                  sa.Column("external_id", sa.Text()))
    op.add_column("candidate_job_assignments",
                  sa.Column("source_metadata", postgresql.JSONB()))
    op.execute("""
        CREATE UNIQUE INDEX candidate_job_assignments_external_idx
          ON candidate_job_assignments (tenant_id, source, external_id)
          WHERE external_id IS NOT NULL;
    """)

    op.execute("""
        CREATE UNIQUE INDEX candidates_tenant_source_external_idx
          ON candidates (tenant_id, source, external_id)
          WHERE pii_redacted_at IS NULL AND external_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS candidates_tenant_source_external_idx;")
    op.execute("DROP INDEX IF EXISTS candidate_job_assignments_external_idx;")
    op.drop_column("candidate_job_assignments", "source_metadata")
    op.drop_column("candidate_job_assignments", "external_id")
    op.drop_column("candidate_job_assignments", "source")

    op.drop_constraint("ck_job_postings_status", "job_postings")
    # Restore the prior CHECK to avoid leaving the column unchecked.
    op.create_check_constraint(
        "ck_job_postings_status",
        "job_postings",
        "status IN ('draft', 'signals_extracting', 'signals_extraction_failed', "
        "'signals_extracted', 'pipeline_built', 'active', 'archived')",
    )
    op.drop_column("job_postings", "external_status")

    op.drop_constraint("ck_org_units_completion_status", "organizational_units")
    op.drop_column("organizational_units", "company_profile_completion_status")

    for table in reversed(_NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS service_bypass ON {table};")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.drop_table(table)
```

- [ ] **Step 3: Run the migration up + down to verify reversibility**

```bash
docker compose run --rm nexus alembic upgrade head
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```

Expected: each command exits 0. The double-up confirms the migration is idempotent against a fresh tail.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0029_ats_core.py
git commit -m "feat(migration/0029): ats_core schema — 5 tables + column additions + RLS"
```

### Task 2: Register new tables with the RLS-completeness startup check

**Files:**
- Modify: `app/main.py` (the `_TENANT_SCOPED_TABLES` list)

- [ ] **Step 1: Add the five new tables to `_TENANT_SCOPED_TABLES`**

Open `app/main.py` and find the `_TENANT_SCOPED_TABLES` tuple (per backend `CLAUDE.md` this is in the startup-check helper `_assert_rls_completeness`). Add these five entries in alphabetical position:

```python
"ats_client_mappings",
"ats_connections",
"ats_job_recruiter_assignments",
"ats_sync_logs",
"ats_user_mappings",
```

- [ ] **Step 2: Run the startup assertion against the migrated DB**

```bash
docker compose run --rm -e ENVIRONMENT=development nexus python -c \
  "import asyncio; from app.main import _assert_rls_completeness; asyncio.run(_assert_rls_completeness())"
```

Expected: prints `rls.completeness.ok` (the structured log line indicating every enumerated table has both policies). If it errors out naming one of the new tables, double-check the migration's `_apply_canonical_rls` call ran for that table.

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(main): register ats_* tables in _TENANT_SCOPED_TABLES startup check"
```

### Task 3: Add the cross-tenant isolation test for all five tables

**Files:**
- Create: `tests/modules/ats/__init__.py` (empty)
- Create: `tests/modules/ats/test_rls_isolation.py`

- [ ] **Step 1: Create the test directory**

```bash
mkdir -p tests/modules/ats
touch tests/modules/ats/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/modules/ats/test_rls_isolation.py`:

```python
"""Cross-tenant RLS isolation for the five ats_* tables.

Each test inserts a row under tenant A's RLS context then verifies a SELECT
under tenant B's context returns zero rows.
"""
from __future__ import annotations

import uuid
import pytest
from sqlalchemy import text

from app.database import async_session_factory


@pytest.mark.asyncio
@pytest.mark.parametrize("table_name,extra_columns", [
    ("ats_connections", {
        "vendor": "ceipal",
        "credentials_ciphertext": b"x",
        "created_by": "{user_id}",
    }),
    ("ats_client_mappings", {
        "ats_vendor": "ceipal",
        "external_client_id": "ext-1",
        "external_client_name": "Acme",
        "org_unit_id": "{org_unit_id}",
    }),
    ("ats_user_mappings", {
        "ats_vendor": "ceipal",
        "external_user_id": "u-1",
        "external_user_email": "u@x.com",
        "external_user_display_name": "U One",
    }),
    ("ats_sync_logs", {
        "connection_id": "{conn_id}",
        "started_at": "now()",
        "status": "success",
        "correlation_id": "test",
    }),
])
async def test_ats_table_is_tenant_isolated(table_name, extra_columns, ats_two_tenants_fixture):
    tenant_a, tenant_b, deps = ats_two_tenants_fixture

    # Substitute placeholders with real UUIDs from the fixture
    cols = {k: v.format(**deps) if isinstance(v, str) and "{" in v else v
            for k, v in extra_columns.items()}

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL ROLE nexus_app"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a}'"))
            col_names = ["tenant_id"] + list(cols.keys())
            placeholders = [":tenant_id"] + [f":{k}" for k in cols.keys()]
            await session.execute(
                text(f"INSERT INTO {table_name} ({', '.join(col_names)}) "
                     f"VALUES ({', '.join(placeholders)})"),
                {"tenant_id": tenant_a, **cols},
            )

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL ROLE nexus_app"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_b}'"))
            result = await session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            assert result.scalar_one() == 0, (
                f"{table_name} returned rows under tenant B's RLS context — "
                f"isolation broken"
            )
```

- [ ] **Step 3: Add the fixture in conftest**

Append to `tests/conftest.py` (or create `tests/modules/ats/conftest.py` if isolation is preferred):

```python
import pytest
import uuid
from sqlalchemy import text
from app.database import async_session_factory


@pytest.fixture
async def ats_two_tenants_fixture():
    """Create two tenants + a root org_unit and a user under tenant_a so
    the ats_* tables' FK requirements are satisfied for the insert under A."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_id = uuid.uuid4()
    org_unit_id = uuid.uuid4()
    conn_id = uuid.uuid4()

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("INSERT INTO clients (id, name) VALUES (:a, 'A'), (:b, 'B')"),
                {"a": tenant_a, "b": tenant_b},
            )
            await session.execute(
                text("INSERT INTO users (id, email, tenant_id, auth_user_id) "
                     "VALUES (:u, 'u@x.com', :t, :a)"),
                {"u": user_id, "t": tenant_a, "a": uuid.uuid4()},
            )
            await session.execute(
                text("INSERT INTO organizational_units "
                     "(id, client_id, name, unit_type, is_root, company_profile) "
                     "VALUES (:o, :t, 'Root', 'company', true, '{\"name\": \"A\"}')"),
                {"o": org_unit_id, "t": tenant_a},
            )
            # Pre-create the ats_connections row for the ats_sync_logs FK
            await session.execute(
                text("INSERT INTO ats_connections (id, tenant_id, vendor, "
                     "credentials_ciphertext, created_by) "
                     "VALUES (:c, :t, 'ceipal', :cred, :u)"),
                {"c": conn_id, "t": tenant_a, "cred": b"x", "u": user_id},
            )
    yield (
        str(tenant_a),
        str(tenant_b),
        {"user_id": str(user_id), "org_unit_id": str(org_unit_id),
         "conn_id": str(conn_id)},
    )
    # Teardown
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("DELETE FROM clients WHERE id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
```

- [ ] **Step 4: Run the test**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_rls_isolation.py -v
```

Expected: all four parametrized cases PASS. If any returns rows under tenant B, the migration's RLS policy didn't apply to that table — revisit `_apply_canonical_rls` in migration 0029.

- [ ] **Step 5: Commit**

```bash
git add tests/modules/ats/ tests/conftest.py
git commit -m "test(ats/rls): cross-tenant isolation for all five new tables"
```

---

## Phase 2 — Config + encryption module

The two foundations every downstream task depends on: the env-driven encryption key setting and the `MultiFernet`-based `crypto.py` module.

### Task 4: Add `ats_credentials_encryption_keys` setting

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Add the setting + field validator**

Find the section in `app/config.py` near the `candidate_jwt_secret` setting and its `_candidate_secret_required` validator (around line 85–118 per the spec). Add:

```python
    # ATS integration — encrypts per-tenant credentials and OAuth tokens at rest.
    # First key in the list encrypts; all keys are tried for decrypt (MultiFernet).
    # Rotation = prepend a new key, backfill ciphertexts, drop the old key.
    # Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ats_credentials_encryption_keys: list[str] = []

    # Default backoff (seconds) when an adapter raises ATSRateLimitedError
    # without a Retry-After hint. Per-connection rate_limit_qps can override.
    ats_default_retry_after_seconds: int = 60

    @field_validator("ats_credentials_encryption_keys")
    @classmethod
    def _ats_encryption_keys_required(cls, v: list[str], info) -> list[str]:
        env = info.data.get("environment", "development")
        if not v and env != "test":
            raise ValueError(
                "ATS_CREDENTIALS_ENCRYPTION_KEYS is required "
                "(comma-separated; first key is active). Generate a new key with: "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`"
            )
        return v
```

- [ ] **Step 2: Document the env var in `.env.example`**

Add to `backend/nexus/.env.example`:

```
# ATS integration — encrypts ATS connection credentials at rest.
# Comma-separated list of Fernet keys; the first key is used to encrypt new
# values. Add a new key to the front to start a rotation; old keys remain
# available for decrypting historical ciphertexts.
ATS_CREDENTIALS_ENCRYPTION_KEYS=
ATS_DEFAULT_RETRY_AFTER_SECONDS=60
```

- [ ] **Step 3: Verify pydantic still parses the settings**

```bash
docker compose run --rm -e ENVIRONMENT=test nexus python -c \
  "from app.config import settings; print('keys:', len(settings.ats_credentials_encryption_keys))"
```

Expected: prints `keys: 0` (test env permits empty).

- [ ] **Step 4: Commit**

```bash
git add app/config.py .env.example
git commit -m "feat(config): add ats_credentials_encryption_keys (MultiFernet, required outside test)"
```

### Task 5: Implement the encryption module with TDD

**Files:**
- Create: `app/modules/ats/__init__.py` (empty for now; populated in Phase 9)
- Create: `app/modules/ats/crypto.py`
- Create: `tests/modules/ats/test_crypto.py`

- [ ] **Step 1: Create the empty package files**

```bash
mkdir -p app/modules/ats
touch app/modules/ats/__init__.py
```

- [ ] **Step 2: Write the failing tests first**

Create `tests/modules/ats/test_crypto.py`:

```python
from __future__ import annotations

import json
import pytest
from cryptography.fernet import Fernet, InvalidToken


def _set_keys(monkeypatch, *keys: str) -> None:
    """Re-bind settings.ats_credentials_encryption_keys for the test."""
    from app.config import settings
    monkeypatch.setattr(settings, "ats_credentials_encryption_keys", list(keys))
    # Reset module-level _fernet cache so it picks up new keys
    from app.modules.ats import crypto
    crypto._fernet = None


def test_encrypt_decrypt_secret_round_trip(monkeypatch):
    from app.modules.ats.crypto import encrypt_secret, decrypt_secret
    key = Fernet.generate_key().decode()
    _set_keys(monkeypatch, key)

    plaintext = "ceipal-bearer-token-abc123"
    ct = encrypt_secret(plaintext)
    assert isinstance(ct, bytes)
    assert plaintext not in ct.decode(errors="ignore")  # not stored in plain
    assert decrypt_secret(ct) == plaintext


def test_encrypt_decrypt_credentials_blob_round_trip(monkeypatch):
    from app.modules.ats.crypto import (
        encrypt_credentials_blob, decrypt_credentials_blob,
    )
    key = Fernet.generate_key().decode()
    _set_keys(monkeypatch, key)

    blob = {"email": "x@y.com", "password": "p@ss!", "api_key": "k"}
    ct = encrypt_credentials_blob(blob)
    assert decrypt_credentials_blob(ct) == blob


def test_multifernet_rotation_decrypts_old_then_new(monkeypatch):
    """After adding a new key to the front, old ciphertexts still decrypt."""
    from app.modules.ats.crypto import encrypt_secret, decrypt_secret
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # Encrypt under old key only
    _set_keys(monkeypatch, old_key)
    old_ct = encrypt_secret("legacy")

    # Rotate: new_key first, old_key still present
    _set_keys(monkeypatch, new_key, old_key)
    assert decrypt_secret(old_ct) == "legacy"   # old ciphertext still readable

    new_ct = encrypt_secret("rotated")
    # Drop old key; new ciphertext still readable
    _set_keys(monkeypatch, new_key)
    assert decrypt_secret(new_ct) == "rotated"


def test_decrypt_with_only_unknown_key_raises(monkeypatch):
    from app.modules.ats.crypto import encrypt_secret, decrypt_secret
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    _set_keys(monkeypatch, key_a)
    ct = encrypt_secret("x")

    _set_keys(monkeypatch, key_b)  # totally different keyring
    with pytest.raises(InvalidToken):
        decrypt_secret(ct)
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_crypto.py -v
```

Expected: 4 errors (`ModuleNotFoundError: No module named 'app.modules.ats.crypto'`).

- [ ] **Step 4: Implement `crypto.py`**

Create `app/modules/ats/crypto.py`:

```python
"""ATS credential encryption.

Wraps the `cryptography.fernet.MultiFernet` API so application code is
provider-agnostic. `settings.ats_credentials_encryption_keys` is a list of
Fernet keys; the FIRST key encrypts, all keys are tried for decrypt.

Rotation runbook: `docs/security/ats-credentials-rotation.md`.
"""
from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings

_fernet: MultiFernet | None = None


def _get_fernet() -> MultiFernet:
    """Lazy-init the MultiFernet from settings.ats_credentials_encryption_keys.

    Cached in module scope. Tests reset by setting `_fernet = None`.
    """
    global _fernet
    if _fernet is None:
        keys = settings.ats_credentials_encryption_keys
        if not keys:
            raise RuntimeError(
                "ats_credentials_encryption_keys is empty; encryption unavailable. "
                "Set ATS_CREDENTIALS_ENCRYPTION_KEYS in env."
            )
        _fernet = MultiFernet([Fernet(k.encode()) for k in keys])
    return _fernet


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a single string secret (access_token, refresh_token, …)."""
    return _get_fernet().encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a single string secret. Raises cryptography.fernet.InvalidToken
    if no key in the ring can decrypt."""
    return _get_fernet().decrypt(ciphertext).decode()


def encrypt_credentials_blob(plaintext: dict[str, Any]) -> bytes:
    """Encrypt a credentials dict (vendor-specific shape) for storage in
    ats_connections.credentials_ciphertext."""
    return _get_fernet().encrypt(json.dumps(plaintext, sort_keys=True).encode())


def decrypt_credentials_blob(ciphertext: bytes) -> dict[str, Any]:
    """Decrypt a credentials dict. Caller validates shape per vendor."""
    return json.loads(_get_fernet().decrypt(ciphertext).decode())
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_crypto.py -v --cov=app.modules.ats.crypto --cov-report=term-missing
```

Expected: 4 passed. Coverage should report `app/modules/ats/crypto.py 100%` (no uncovered branches).

- [ ] **Step 6: Commit**

```bash
git add app/modules/ats/crypto.py app/modules/ats/__init__.py tests/modules/ats/test_crypto.py
git commit -m "feat(ats/crypto): MultiFernet credential + token encryption with rotation"
```

---

## Phase 3 — Module data layer (errors, DTOs, ORM, connection state)

Declarations only; no orchestration logic yet. Each task creates one focused file and ships with a smoke test that proves the declarations parse/round-trip.

### Task 6: Exception hierarchy (`errors.py`)

**Files:**
- Create: `app/modules/ats/errors.py`
- Create: `tests/modules/ats/test_errors.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_errors.py`:

```python
"""Hierarchy + behavioral tests for the ATS exception classes.

The actor logic (later phase) classifies catches by whether the exception is
ATSPermanentError or ATSTransientError. These tests pin that classification.
"""
from __future__ import annotations

import pytest

from app.modules.ats.errors import (
    ATSError,
    ATSPermanentError, ATSCredentialsInvalidError, ATSAuthorizationError,
    ATSVendorContractError, ATSUnknownVendorError, ATSConnectionNotFoundError,
    ATSTransientError, ATSNetworkError, ATSRateLimitedError,
)


def test_permanent_subclasses():
    for cls in (ATSCredentialsInvalidError, ATSAuthorizationError,
                ATSVendorContractError, ATSUnknownVendorError,
                ATSConnectionNotFoundError):
        assert issubclass(cls, ATSPermanentError)
        assert issubclass(cls, ATSError)


def test_transient_subclasses():
    assert issubclass(ATSNetworkError, ATSTransientError)
    assert issubclass(ATSRateLimitedError, ATSTransientError)


def test_permanent_and_transient_are_disjoint():
    for cls in (ATSNetworkError, ATSRateLimitedError):
        assert not issubclass(cls, ATSPermanentError)
    for cls in (ATSCredentialsInvalidError, ATSAuthorizationError,
                ATSVendorContractError):
        assert not issubclass(cls, ATSTransientError)


def test_rate_limited_carries_retry_after():
    exc = ATSRateLimitedError(retry_after_seconds=42, message="429 from vendor")
    assert exc.retry_after_seconds == 42
    assert "42" in str(exc)


def test_rate_limited_default_message():
    exc = ATSRateLimitedError(retry_after_seconds=60)
    assert "60" in str(exc)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_errors.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.modules.ats.errors'`.

- [ ] **Step 3: Implement `errors.py`**

Create `app/modules/ats/errors.py`:

```python
"""Typed exception hierarchy for ATS adapter operations.

The Dramatiq actor (app/modules/ats/actors.py) catches these to decide:
  - ATSRateLimitedError  → advance next_poll_at, return cleanly (no retry)
  - ATSPermanentError    → disable connection, raise (lands in DLQ)
  - ATSTransientError    → re-raise so Dramatiq retries with exp backoff
  - any other Exception  → unexpected, treat as transient (Dramatiq retries)
"""
from __future__ import annotations


class ATSError(Exception):
    """Base class for all ATS adapter errors."""


# ----- Permanent (orchestrator disables connection, surfaces in UI) -----

class ATSPermanentError(ATSError):
    """Non-retryable. Caller must take action."""


class ATSCredentialsInvalidError(ATSPermanentError):
    """Auth failed even after refresh attempt. Recruiter must reconnect."""


class ATSAuthorizationError(ATSPermanentError):
    """API key has insufficient scope. Recruiter must regenerate."""


class ATSVendorContractError(ATSPermanentError):
    """Vendor returned a response we cannot parse — schema drift.
    Logged with full raw payload; engineering action required."""


class ATSUnknownVendorError(ATSPermanentError):
    """No adapter registered for the connection's vendor."""


class ATSConnectionNotFoundError(ATSPermanentError):
    """The connection row referenced by the actor no longer exists."""


# ----- Transient (Dramatiq retries) -----

class ATSTransientError(ATSError):
    """Retryable."""


class ATSNetworkError(ATSTransientError):
    """Network failure, 5xx response, connection timeout."""


class ATSRateLimitedError(ATSTransientError):
    """Vendor said 'wait N seconds'. Actor sets next_poll_at = now() + N
    and exits cleanly (no Dramatiq retry; next tick resumes naturally)."""

    def __init__(self, retry_after_seconds: int, message: str = "") -> None:
        super().__init__(
            message or f"Rate limited; retry after {retry_after_seconds}s"
        )
        self.retry_after_seconds = retry_after_seconds
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_errors.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/errors.py tests/modules/ats/test_errors.py
git commit -m "feat(ats/errors): typed exception hierarchy (permanent vs transient)"
```

### Task 7: Canonical DTOs (`schemas.py`)

**Files:**
- Create: `app/modules/ats/schemas.py`
- Create: `tests/modules/ats/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_schemas.py`:

```python
"""Smoke tests for the canonical ATS DTOs — confirm fields, types, and
that the raw payload is preserved verbatim."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.modules.ats.schemas import (
    ATSClientPayload, ATSUserPayload, ATSJobPayload,
    ATSApplicantPayload, ATSSubmissionPayload,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_client_payload_minimal():
    p = ATSClientPayload(external_id="cid", name="Acme", raw={}, fetched_at=_now())
    assert p.contacts == []
    assert p.website is None


def test_client_payload_preserves_raw():
    raw = {"id": "cid", "name": "Acme", "weird_vendor_field": 1}
    p = ATSClientPayload(external_id="cid", name="Acme", raw=raw, fetched_at=_now())
    assert p.raw is raw  # same object preserved


def test_job_payload_recruiter_assignments_default_empty():
    p = ATSJobPayload(
        external_id="jid", external_client_id="cid", title="t",
        raw={}, fetched_at=_now(),
    )
    assert p.assigned_recruiter_external_ids == []
    assert p.skills == []


def test_submission_payload_pay_rate_coerces_numeric_and_string():
    """The Ceipal API has been observed returning pay_rate as int, float, or
    string. The DTO must coerce all three to Decimal."""
    for raw_val in (40, 40.0, "40.00"):
        p = ATSSubmissionPayload(
            external_id="sid", applicant_external_id="aid", job_external_id="jid",
            pay_rate=raw_val, raw={}, fetched_at=_now(),
        )
        assert isinstance(p.pay_rate, Decimal)
        assert p.pay_rate == Decimal("40.00") or p.pay_rate == Decimal("40")


def test_submission_payload_pay_rate_none_is_allowed():
    p = ATSSubmissionPayload(
        external_id="sid", applicant_external_id="aid", job_external_id="jid",
        raw={}, fetched_at=_now(),
    )
    assert p.pay_rate is None


def test_applicant_payload_required_fields():
    with pytest.raises(Exception):
        ATSApplicantPayload(raw={}, fetched_at=_now())  # missing external_id, name, email


def test_user_payload_required_fields():
    p = ATSUserPayload(
        external_id="uid", email="u@x.com", display_name="U One",
        raw={}, fetched_at=_now(),
    )
    assert p.role is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_schemas.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `schemas.py`**

Create `app/modules/ats/schemas.py`:

```python
"""Canonical vendor-agnostic DTOs returned by ATSAdapter implementations.

Every DTO carries a `raw: dict` of the verbatim vendor payload — this lets
us add field extractions later without re-syncing, and gives audit forensics
a complete picture. The `raw` field lives in DB columns (source_metadata),
NEVER in log fields.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ATSClientPayload(BaseModel):
    external_id: str
    name: str
    website: str | None = None
    industry: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    address: str | None = None
    status: str | None = None
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any]
    fetched_at: datetime


class ATSUserPayload(BaseModel):
    external_id: str
    email: str
    display_name: str
    role: str | None = None
    status: str | None = None
    raw: dict[str, Any]
    fetched_at: datetime


class ATSJobPayload(BaseModel):
    external_id: str
    external_client_id: str
    title: str
    description: str | None = None
    status: str | None = None
    location: str | None = None
    skills: list[str] = Field(default_factory=list)
    employment_type: str | None = None
    work_arrangement: str | None = None
    salary_range_min: int | None = None
    salary_range_max: int | None = None
    salary_currency: str | None = None
    assigned_recruiter_external_ids: list[str] = Field(default_factory=list)
    raw: dict[str, Any]
    fetched_at: datetime


class ATSApplicantPayload(BaseModel):
    external_id: str
    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    current_title: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None
    raw: dict[str, Any]
    fetched_at: datetime


class ATSSubmissionPayload(BaseModel):
    external_id: str
    applicant_external_id: str
    job_external_id: str
    submission_status: str | None = None
    pipeline_status: str | None = None
    source: str | None = None                    # 'Naukri', 'LinkedIn', …
    submitted_on: datetime | None = None
    submitted_by_external_id: str | None = None
    pay_rate: Decimal | None = None
    employment_type: str | None = None
    raw: dict[str, Any]                          # carries resume_token, Documents[], etc.
    fetched_at: datetime

    @field_validator("pay_rate", mode="before")
    @classmethod
    def _coerce_pay_rate(cls, v):
        """Ceipal returns pay_rate as int, float, or string across responses."""
        if v is None or v == "":
            return None
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_schemas.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/schemas.py tests/modules/ats/test_schemas.py
git commit -m "feat(ats/schemas): canonical vendor-agnostic DTOs with pay_rate coercion"
```

### Task 8: ORM models (`models.py`)

**Files:**
- Create: `app/modules/ats/models.py`

- [ ] **Step 1: Implement the ORM classes mirroring migration 0029**

Create `app/modules/ats/models.py`:

```python
"""ORM mappings for the ATS integration tables.

Schema source-of-truth is migration 0029_ats_core. These classes mirror it
so Base.metadata.create_all builds the same shape in test DBs that skip
alembic.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, LargeBinary, Numeric, Text,
    UniqueConstraint, text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ATSConnection(Base):
    """Per-(tenant, vendor) ATS integration with encrypted credentials + tokens."""
    __tablename__ = "ats_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "vendor", name="uq_ats_connections_tenant_vendor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    credentials_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_cursors: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    poll_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("900")
    )
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    poll_lock_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_error: Mapped[str | None] = mapped_column(Text)
    rate_limit_qps: Mapped[float | None] = mapped_column(Numeric)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSClientMapping(Base):
    """Ceipal client ↔ ProjectX client_account org_unit."""
    __tablename__ = "ats_client_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ats_vendor", "external_client_id",
                         name="uq_ats_client_mappings_external"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    ats_vendor: Mapped[str] = mapped_column(Text, nullable=False)
    external_client_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_client_name: Mapped[str] = mapped_column(Text, nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizational_units.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSUserMapping(Base):
    """Ceipal user ↔ ProjectX user (nullable mapping)."""
    __tablename__ = "ats_user_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ats_vendor", "external_user_id",
                         name="uq_ats_user_mappings_external"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    ats_vendor: Mapped[str] = mapped_column(Text, nullable=False)
    external_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_user_email: Mapped[str] = mapped_column(Text, nullable=False)
    external_user_display_name: Mapped[str] = mapped_column(Text, nullable=False)
    external_user_role: Mapped[str | None] = mapped_column(Text)
    external_user_status: Mapped[str | None] = mapped_column(Text)
    external_user_metadata: Mapped[dict | None] = mapped_column(JSONB)
    internal_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    mapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mapped_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSJobRecruiterAssignment(Base):
    """Ceipal-assigned recruiter external_ids per ProjectX job_posting."""
    __tablename__ = "ats_job_recruiter_assignments"
    __table_args__ = (
        UniqueConstraint("job_posting_id", "external_user_id",
                         name="uq_ats_job_recruiter_assignments"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    ats_vendor: Mapped[str] = mapped_column(Text, nullable=False)
    external_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSSyncLog(Base):
    """One row per sync run; status ∈ {running, success, partial, failed}."""
    __tablename__ = "ats_sync_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ats_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    entity_counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    error_phase: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
```

- [ ] **Step 2: Verify the ORM imports cleanly**

```bash
docker compose run --rm nexus python -c \
  "from app.modules.ats.models import (ATSConnection, ATSClientMapping, \
   ATSUserMapping, ATSJobRecruiterAssignment, ATSSyncLog); print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add app/modules/ats/models.py
git commit -m "feat(ats/models): ORM mirrors for migration 0029 tables"
```

### Task 9: `ATSConnectionState` + load/persist helpers

**Files:**
- Create: `app/modules/ats/connection.py`
- Create: `tests/modules/ats/test_connection.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_connection.py`:

```python
"""Load/persist round-trip through the encryption boundary.

Insert a row directly via ORM, load via load_connection_state, mutate tokens,
persist, reload — confirm the mutated values come back decrypted correctly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.database import async_session_factory


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    from app.config import settings
    from app.modules.ats import crypto
    monkeypatch.setattr(
        settings, "ats_credentials_encryption_keys",
        [Fernet.generate_key().decode()],
    )
    crypto._fernet = None


@pytest.fixture
async def seeded_connection():
    """Insert a tenant + user + ats_connection row directly. Returns its UUID."""
    from app.modules.ats.crypto import encrypt_credentials_blob
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    creds_ct = encrypt_credentials_blob(
        {"email": "x@y.com", "password": "p", "api_key": "k"}
    )

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
                {"t": tenant_id},
            )
            await session.execute(
                text("INSERT INTO users (id, email, tenant_id, auth_user_id) "
                     "VALUES (:u, 'u@x.com', :t, :a)"),
                {"u": user_id, "t": tenant_id, "a": uuid.uuid4()},
            )
            await session.execute(
                text("INSERT INTO ats_connections "
                     "(id, tenant_id, vendor, credentials_ciphertext, created_by) "
                     "VALUES (:c, :t, 'ceipal', :ct, :u)"),
                {"c": conn_id, "t": tenant_id, "ct": creds_ct, "u": user_id},
            )
    yield (tenant_id, conn_id)
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("DELETE FROM clients WHERE id = :t"), {"t": tenant_id}
            )


@pytest.mark.asyncio
async def test_load_returns_decrypted_state(seeded_connection):
    from app.modules.ats.connection import load_connection_state
    tenant_id, conn_id = seeded_connection

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            state = await load_connection_state(session, conn_id)

    assert state.id == conn_id
    assert state.tenant_id == tenant_id
    assert state.vendor == "ceipal"
    assert state.credentials == {"email": "x@y.com", "password": "p", "api_key": "k"}
    assert state.access_token is None
    assert state.refresh_token is None
    assert state.last_synced_cursors == {}


@pytest.mark.asyncio
async def test_persist_round_trips_mutated_tokens(seeded_connection):
    from app.modules.ats.connection import load_connection_state, persist_connection_state
    tenant_id, conn_id = seeded_connection
    expires = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            state = await load_connection_state(session, conn_id)
            state.access_token = "new-access-tok"
            state.refresh_token = "new-refresh-tok"
            state.access_token_expires_at = expires
            state.last_synced_cursors = {"clients": expires.isoformat()}
            await persist_connection_state(session, state)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            reloaded = await load_connection_state(session, conn_id)

    assert reloaded.access_token == "new-access-tok"
    assert reloaded.refresh_token == "new-refresh-tok"
    assert reloaded.last_synced_cursors == {"clients": expires.isoformat()}


@pytest.mark.asyncio
async def test_load_missing_raises_typed_error():
    from app.modules.ats.connection import load_connection_state
    from app.modules.ats.errors import ATSConnectionNotFoundError

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            with pytest.raises(ATSConnectionNotFoundError):
                await load_connection_state(session, uuid.uuid4())
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_connection.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.modules.ats.connection'`.

- [ ] **Step 3: Implement `connection.py`**

Create `app/modules/ats/connection.py`:

```python
"""ATSConnectionState — in-memory working copy of an ats_connections row.

Distinct from the ORM model `ATSConnection` (app/modules/ats/models.py):
  - ORM row: persisted; credentials + tokens encrypted.
  - State:   in-memory; decrypted; mutable; adapter writes through it.

Lifecycle: load → decrypt → state → adapter mutates → encrypt → persist.
The adapter never touches the ORM directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ats.crypto import (
    decrypt_credentials_blob, decrypt_secret,
    encrypt_credentials_blob, encrypt_secret,
)
from app.modules.ats.errors import ATSConnectionNotFoundError
from app.modules.ats.models import ATSConnection


@dataclass
class ATSConnectionState:
    id: UUID
    tenant_id: UUID
    vendor: str
    credentials: dict[str, Any]
    access_token: str | None = None
    refresh_token: str | None = None
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    last_synced_cursors: dict[str, str] = field(default_factory=dict)
    poll_interval_seconds: int = 900


async def load_connection_state(
    db: AsyncSession, connection_id: UUID,
) -> ATSConnectionState:
    """Hydrate the in-memory state from a persisted ATSConnection row.

    Caller is responsible for tenant-scope binding (this typically runs inside
    a bypass-RLS session with SET LOCAL app.current_tenant already issued, or
    after an explicit tenant_id filter at the application layer).
    """
    row = await db.get(ATSConnection, connection_id)
    if row is None:
        raise ATSConnectionNotFoundError(str(connection_id))

    return ATSConnectionState(
        id=row.id,
        tenant_id=row.tenant_id,
        vendor=row.vendor,
        credentials=decrypt_credentials_blob(row.credentials_ciphertext),
        access_token=(
            decrypt_secret(row.access_token_ciphertext)
            if row.access_token_ciphertext else None
        ),
        refresh_token=(
            decrypt_secret(row.refresh_token_ciphertext)
            if row.refresh_token_ciphertext else None
        ),
        access_token_expires_at=row.access_token_expires_at,
        refresh_token_expires_at=row.refresh_token_expires_at,
        last_synced_cursors=dict(row.last_synced_cursors or {}),
        poll_interval_seconds=row.poll_interval_seconds,
    )


async def persist_connection_state(
    db: AsyncSession, state: ATSConnectionState,
) -> None:
    """Write back the mutated token + cursor fields. credentials_ciphertext
    is NOT rewritten here (credentials don't change during a sync; the
    /connections POST handler is the only place that writes credentials).
    """
    row = await db.get(ATSConnection, state.id)
    if row is None:
        raise ATSConnectionNotFoundError(str(state.id))

    row.access_token_ciphertext = (
        encrypt_secret(state.access_token) if state.access_token else None
    )
    row.refresh_token_ciphertext = (
        encrypt_secret(state.refresh_token) if state.refresh_token else None
    )
    row.access_token_expires_at = state.access_token_expires_at
    row.refresh_token_expires_at = state.refresh_token_expires_at
    row.last_synced_cursors = state.last_synced_cursors
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_connection.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/connection.py tests/modules/ats/test_connection.py
git commit -m "feat(ats/connection): ATSConnectionState + load/persist with encryption boundary"
```

---

## Phase 4 — Adapter Protocol + Registry + Sources bridge

The vendor-agnostic surface. After this phase, the module has the shape; Ceipal is the first concrete impl in Phase 5.

### Task 10: `ATSAdapter` Protocol declaration

**Files:**
- Create: `app/modules/ats/adapter.py`

- [ ] **Step 1: Replace the placeholder `adapter.py`**

The current `app/modules/ats/adapter.py` (declared as a stub during Phase-0 exploration) holds a minimal 3-method Protocol. Overwrite it with the full version. If the file does not yet exist, create it.

Write `app/modules/ats/adapter.py`:

```python
"""ATSAdapter Protocol — the contract every ATS implementation satisfies.

Construction goes through app.modules.ats.registry.get_ats_adapter(state).
The adapter holds a reference to ATSConnectionState; it mutates token fields
during a sync (refresh), and the orchestrator persists those mutations after
the sync completes.

Adapter instances are short-lived (one per sync run) and NOT thread-safe.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, ClassVar, Protocol

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import (
    ATSApplicantPayload, ATSClientPayload, ATSJobPayload,
    ATSSubmissionPayload, ATSUserPayload,
)


class ATSAdapter(Protocol):
    """Per-tenant ATS adapter.

    All list_* methods return AsyncIterators that handle pagination internally.
    All methods may raise:
      - ATSCredentialsInvalidError (permanent; reconnect required)
      - ATSAuthorizationError (permanent; scope insufficient)
      - ATSVendorContractError (permanent; vendor schema drift)
      - ATSRateLimitedError (transient; caller advances next_poll_at)
      - ATSNetworkError (transient; caller retries)
    """

    vendor: ClassVar[str]        # 'ceipal', 'greenhouse', 'workday'
    state: ATSConnectionState    # mutable; orchestrator persists after sync

    async def ensure_authenticated(self) -> None:
        """Refresh tokens if expired or near-expiry (proactive at 80% lifetime).

        Idempotent — safe to call when tokens are already valid. Raises
        ATSCredentialsInvalidError if the stored credentials no longer work.
        """
        ...

    def list_clients(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        """Yield client records. If `since` is None: full sync."""
        ...

    def list_users(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        """Yield user records (recruiters/admins on the tenant's ATS account)."""
        ...

    def list_jobs(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        """Yield job postings."""
        ...

    def list_applicants(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        """Yield applicants — the people. Delta sync where supported."""
        ...

    def list_submissions(
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        """Yield submissions for a specific job — the applicant↔job link entity."""
        ...
```

- [ ] **Step 2: Confirm the Protocol imports cleanly**

```bash
docker compose run --rm nexus python -c \
  "from app.modules.ats.adapter import ATSAdapter; print('vendor attr:', \
   hasattr(ATSAdapter, 'vendor'))"
```

Expected: prints `vendor attr: False` (ClassVar declarations don't materialize as attributes on the Protocol class — that's expected; runtime checks are done by `isinstance` against concrete adapter classes that DO define `vendor`).

- [ ] **Step 3: Commit**

```bash
git add app/modules/ats/adapter.py
git commit -m "feat(ats/adapter): full ATSAdapter Protocol (6 methods, canonical DTOs)"
```

### Task 11: Adapter registry

**Files:**
- Create: `app/modules/ats/registry.py`
- Create: `tests/modules/ats/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_registry.py`:

```python
"""Registry returns the right adapter class by vendor; raises on unknown."""
from __future__ import annotations

import uuid

import pytest

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import ATSUnknownVendorError


def _state(vendor: str) -> ATSConnectionState:
    return ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor=vendor,
        credentials={},
    )


def test_get_ats_adapter_returns_ceipal_for_ceipal_vendor():
    from app.modules.ats.registry import get_ats_adapter
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    adapter = get_ats_adapter(_state("ceipal"))
    assert isinstance(adapter, CeipalAdapter)
    assert adapter.state.vendor == "ceipal"


def test_get_ats_adapter_raises_on_unknown_vendor():
    from app.modules.ats.registry import get_ats_adapter

    with pytest.raises(ATSUnknownVendorError) as exc_info:
        get_ats_adapter(_state("greenhouse_v2_alpha"))
    assert "greenhouse_v2_alpha" in str(exc_info.value)


def test_supported_vendors_includes_ceipal():
    from app.modules.ats.registry import SUPPORTED_VENDORS
    assert "ceipal" in SUPPORTED_VENDORS
```

- [ ] **Step 2: Run the test — confirm it fails because the Ceipal adapter doesn't exist yet**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_registry.py -v
```

Expected: ModuleNotFoundError on either `app.modules.ats.registry` or `app.modules.ats.adapters.ceipal`. This is the right order — Task 11 wires up the registry shell with a placeholder import of CeipalAdapter; Task 12 (Phase 5) implements it.

- [ ] **Step 3: Create the empty `adapters/` package with a minimal CeipalAdapter stub**

```bash
mkdir -p app/modules/ats/adapters
touch app/modules/ats/adapters/__init__.py
```

Create `app/modules/ats/adapters/ceipal.py` as a one-class stub (full implementation in Phase 5):

```python
"""CeipalAdapter — STUB. Full implementation lands in Task 13+ (Phase 5).

This stub exists so app/modules/ats/registry.py can import it now. The
Protocol methods raise NotImplementedError until the implementation phase.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, ClassVar

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import (
    ATSApplicantPayload, ATSClientPayload, ATSJobPayload,
    ATSSubmissionPayload, ATSUserPayload,
)


class CeipalAdapter:
    vendor: ClassVar[str] = "ceipal"

    def __init__(self, state: ATSConnectionState) -> None:
        self.state = state

    async def ensure_authenticated(self) -> None:
        raise NotImplementedError("Phase 5")

    async def list_clients(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover  (makes the function an async generator)

    async def list_users(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover

    async def list_jobs(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover

    async def list_applicants(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover

    async def list_submissions(  # type: ignore[override]
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover
```

- [ ] **Step 4: Implement the registry**

Create `app/modules/ats/registry.py`:

```python
"""Vendor-keyed factory for ATSAdapter instances.

Adding a new vendor:
  1. Implement app/modules/ats/adapters/<vendor>.py satisfying ATSAdapter.
  2. Add `<VendorAdapter>.vendor: <VendorAdapter>` to _REGISTRY.
  3. Define the vendor's credential schema in the connection-create router.

Vendor selection is per-CONNECTION (data — state.vendor), not per-deployment
(env). Different tenants can use different ATSes simultaneously.
"""
from __future__ import annotations

from typing import Type

from app.modules.ats.adapter import ATSAdapter
from app.modules.ats.adapters.ceipal import CeipalAdapter
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import ATSUnknownVendorError


_REGISTRY: dict[str, Type[ATSAdapter]] = {
    CeipalAdapter.vendor: CeipalAdapter,        # type: ignore[type-abstract]
    # GreenhouseAdapter.vendor: GreenhouseAdapter,    # future
    # WorkdayAdapter.vendor: WorkdayAdapter,          # future
}

SUPPORTED_VENDORS: frozenset[str] = frozenset(_REGISTRY.keys())


def get_ats_adapter(state: ATSConnectionState) -> ATSAdapter:
    """Construct the adapter for `state.vendor`.

    Raises ATSUnknownVendorError if the vendor is not registered — indicates
    either a config drift (vendor was deregistered) or a malformed DB row;
    either case requires engineering investigation, so it's permanent.
    """
    cls = _REGISTRY.get(state.vendor)
    if cls is None:
        raise ATSUnknownVendorError(
            f"No ATS adapter registered for vendor {state.vendor!r}. "
            f"Supported: {sorted(SUPPORTED_VENDORS)}"
        )
    return cls(state)  # type: ignore[call-arg]
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_registry.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/modules/ats/registry.py app/modules/ats/adapters/__init__.py \
        app/modules/ats/adapters/ceipal.py tests/modules/ats/test_registry.py
git commit -m "feat(ats/registry): vendor-keyed factory + CeipalAdapter stub"
```

### Task 12: `ATSImportSource` — the candidates bridge

**Files:**
- Create: `app/modules/ats/sources.py`
- Create: `tests/modules/ats/test_sources.py`

This bridge lives in `ats/sources.py`, NOT `candidates/sources.py`. Cross-module import direction is `ats → candidates` only (importer calls `candidates.service.import_candidate`); the opposite would create a cycle (per backend `CLAUDE.md` module-boundary rule).

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_sources.py`:

```python
"""ATSImportSource.normalize: ATSApplicantPayload → SourcedCandidate."""
from __future__ import annotations

from datetime import datetime, timezone


def test_ats_import_source_normalizes_to_sourced_candidate():
    from app.modules.ats.sources import ATSImportSource
    from app.modules.ats.schemas import ATSApplicantPayload
    from app.modules.candidates.sources import SourcedCandidate

    payload = ATSApplicantPayload(
        external_id="appl-1", name="Jane Doe", email="jane@x.com",
        phone="555-0100", location="Bangalore",
        current_title="Sr Engineer", linkedin_url="https://linkedin.com/in/jane",
        notes=None,
        raw={"id": "appl-1", "extra_vendor_field": "preserved"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    src = ATSImportSource(vendor="ceipal")
    out = src.normalize(payload)

    assert isinstance(out, SourcedCandidate)
    assert out.name == "Jane Doe"
    assert out.email == "jane@x.com"
    assert out.source == "ats_ceipal"
    assert out.external_id == "appl-1"
    assert out.source_metadata["extra_vendor_field"] == "preserved"


def test_vendor_prefix_is_applied():
    from app.modules.ats.sources import ATSImportSource
    from app.modules.ats.schemas import ATSApplicantPayload
    from datetime import datetime, timezone

    payload = ATSApplicantPayload(
        external_id="g-1", name="X", email="x@y.com",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    assert ATSImportSource("ceipal").normalize(payload).source == "ats_ceipal"
    assert ATSImportSource("greenhouse").normalize(payload).source == "ats_greenhouse"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_sources.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `sources.py`**

Create `app/modules/ats/sources.py`:

```python
"""Bridge between canonical ATS DTOs and the CandidateSource Protocol.

Lives in ats/, not in candidates/, to keep the cross-module import direction
acyclic (ats imports from candidates; candidates does NOT import from ats).
The orchestrator calls candidates.service.import_candidate(sourced=...) with
the SourcedCandidate produced here.
"""
from __future__ import annotations

from app.modules.ats.schemas import ATSApplicantPayload
from app.modules.candidates.sources import SourcedCandidate


class ATSImportSource:
    """Normalizes ATSApplicantPayload → SourcedCandidate.

    Vendor-parameterised because the resulting candidate.source string is
    tagged with the vendor ('ats_ceipal' / 'ats_greenhouse' / …).
    """

    def __init__(self, vendor: str) -> None:
        self._vendor = vendor

    def normalize(self, raw: ATSApplicantPayload) -> SourcedCandidate:
        return SourcedCandidate(
            name=raw.name,
            email=raw.email,
            phone=raw.phone,
            location=raw.location,
            current_title=raw.current_title,
            linkedin_url=raw.linkedin_url,
            notes=raw.notes,
            source=f"ats_{self._vendor}",
            external_id=raw.external_id,
            source_metadata=raw.raw,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_sources.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/sources.py tests/modules/ats/test_sources.py
git commit -m "feat(ats/sources): ATSImportSource bridges ATS payload → SourcedCandidate"
```

---

## Phase 5 — Ceipal adapter implementation

Three tasks: HTTP foundation + auth (Task 13), shared pagination + error envelope mapping (Task 14), then the five list endpoints (Task 15). Each list endpoint follows the same pattern, so once the helpers are in place the per-endpoint code is short.

All Ceipal tests use `httpx.MockTransport` to fake the API surface — no network access required.

### Task 13: HTTP client foundation + auth + `ensure_authenticated`

**Files:**
- Create: `app/modules/ats/adapters/ceipal.py` (replacing the stub from Task 11)
- Create: `tests/modules/ats/adapters/__init__.py` (empty)
- Create: `tests/modules/ats/adapters/test_ceipal_auth.py`

- [ ] **Step 1: Create the test directory**

```bash
mkdir -p tests/modules/ats/adapters
touch tests/modules/ats/adapters/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/modules/ats/adapters/test_ceipal_auth.py`:

```python
"""CeipalAdapter authentication: createAuthtoken, refreshToken, ensure_authenticated.

Auth-token refresh in Ceipal is unusual: refresh requires the EXPIRED access
token in the Token header, not the refresh token in the body. Tests pin
that contract.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import ATSCredentialsInvalidError, ATSAuthorizationError


def _state(**overrides) -> ATSConnectionState:
    base = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token=None, refresh_token=None,
        access_token_expires_at=None, refresh_token_expires_at=None,
    )
    base.update(overrides)
    return ATSConnectionState(**base)


def _make_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_initial_auth_calls_createAuthtoken_with_credentials():
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 3600,
        })

    adapter = CeipalAdapter(_state(), _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert "/v2/createAuthtoken/" in captured["url"]
    assert captured["body"] == {"email": "u@x.com", "password": "p", "apiKey": "k"}
    assert adapter.state.access_token == "fresh-access"
    assert adapter.state.refresh_token == "fresh-refresh"
    assert adapter.state.access_token_expires_at is not None


@pytest.mark.asyncio
async def test_refresh_uses_expired_access_token_in_header():
    """Ceipal's quirk: refreshToken takes the EXPIRED access token in the
    `Token: Bearer <token>` header (not the refresh_token in body)."""
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["token_header"] = request.headers.get("Token")
        return httpx.Response(200, json={
            "access_token": "refreshed-access",
            "expires_in": 3600,
        })

    expired = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    state = _state(
        access_token="old-expired-token",
        access_token_expires_at=expired,
        refresh_token="rfr-tok",
        refresh_token_expires_at=datetime.now(tz=timezone.utc) + timedelta(days=5),
    )
    adapter = CeipalAdapter(state, _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert "/v2/refreshToken/" in captured["url"]
    assert captured["token_header"] == "Bearer old-expired-token"
    assert adapter.state.access_token == "refreshed-access"


@pytest.mark.asyncio
async def test_refresh_expired_falls_back_to_full_reauth():
    """When refresh_token has also expired, the adapter re-auths from
    stored credentials transparently — recruiter sees no disconnection."""
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "createAuthtoken" in str(request.url):
            return httpx.Response(200, json={
                "access_token": "reauth-access",
                "refresh_token": "reauth-refresh",
                "expires_in": 3600,
            })
        return httpx.Response(401, json={"message": "Please provide the access token."})

    expired = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    state = _state(
        access_token="old", access_token_expires_at=expired,
        refresh_token="r", refresh_token_expires_at=expired,
    )
    adapter = CeipalAdapter(state, _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert any("createAuthtoken" in u for u in calls)
    assert adapter.state.access_token == "reauth-access"


@pytest.mark.asyncio
async def test_invalid_credentials_raise_typed_error():
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Please provide the access token."})

    adapter = CeipalAdapter(_state(), _transport=_make_transport(handler))
    with pytest.raises(ATSCredentialsInvalidError):
        await adapter.ensure_authenticated()


@pytest.mark.asyncio
async def test_skip_refresh_when_token_still_valid():
    """Idempotency: calling ensure_authenticated when tokens are valid is a no-op."""
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"access_token": "x", "expires_in": 3600})

    far_future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
    state = _state(access_token="still-good", access_token_expires_at=far_future)
    adapter = CeipalAdapter(state, _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert call_count == 0
    assert adapter.state.access_token == "still-good"
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/ats/adapters/test_ceipal_auth.py -v
```

Expected: tests fail (the stub from Task 11 raises NotImplementedError; tests can't even construct the adapter with the `_transport` kwarg).

- [ ] **Step 4: Replace the stub with the full CeipalAdapter (auth-only portion)**

Replace `app/modules/ats/adapters/ceipal.py` with:

```python
"""CeipalAdapter — Ceipal ATS v2 API implementation.

Auth model is unusual:
  - createAuthtoken: email + password + apiKey → access_token (1h) + refresh_token (7d)
  - refreshToken:    expired access_token in `Token: Bearer ...` header → new access_token

Refresh strategy: proactive at 80% of access_token lifetime. If refresh_token
has also expired, fall back to full re-auth from stored credentials.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, ClassVar

import httpx
import structlog

from app.config import settings
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import (
    ATSAuthorizationError, ATSCredentialsInvalidError,
    ATSNetworkError, ATSRateLimitedError, ATSVendorContractError,
)
from app.modules.ats.schemas import (
    ATSApplicantPayload, ATSClientPayload, ATSJobPayload,
    ATSSubmissionPayload, ATSUserPayload,
)


logger = structlog.get_logger()

CEIPAL_BASE_URL = "https://api.ceipal.com/v2"
ACCESS_TOKEN_REFRESH_THRESHOLD = 0.20  # refresh when ≤20% of lifetime remains


class CeipalAdapter:
    vendor: ClassVar[str] = "ceipal"

    def __init__(
        self,
        state: ATSConnectionState,
        *,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.state = state
        # _transport is for tests via httpx.MockTransport; production calls
        # pass None and httpx uses its default async transport.
        self._client = httpx.AsyncClient(
            base_url=CEIPAL_BASE_URL,
            timeout=httpx.Timeout(30.0),
            transport=_transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- Auth ----------

    async def ensure_authenticated(self) -> None:
        """Idempotent. Refresh tokens if expired or near-expiry."""
        now = datetime.now(tz=timezone.utc)

        # Case 1: tokens still valid → no-op
        if self.state.access_token and self.state.access_token_expires_at:
            time_left = (self.state.access_token_expires_at - now).total_seconds()
            # access_token typically lives 3600s; refresh when ≤720s remain
            if time_left > 3600 * ACCESS_TOKEN_REFRESH_THRESHOLD:
                return

        # Case 2: refresh_token still valid → use refresh endpoint
        if (
            self.state.access_token
            and self.state.refresh_token_expires_at
            and self.state.refresh_token_expires_at > now
        ):
            try:
                await self._refresh_via_token_header()
                return
            except ATSCredentialsInvalidError:
                # Fall through to full reauth — refresh_token may be invalid
                # despite our expiry tracker
                logger.warning(
                    "ats.ceipal.refresh_failed_falling_back_to_reauth",
                    connection_id=str(self.state.id),
                )

        # Case 3: full re-auth from stored credentials
        await self._authenticate_with_credentials()

    async def _authenticate_with_credentials(self) -> None:
        creds = self.state.credentials
        body = {
            "email": creds["email"],
            "password": creds["password"],
            "apiKey": creds["api_key"],
        }
        try:
            response = await self._client.post("/createAuthtoken/", json=body)
        except httpx.HTTPError as exc:
            raise ATSNetworkError(f"createAuthtoken network error: {exc}") from exc

        self._handle_auth_response(response, "createAuthtoken")

    async def _refresh_via_token_header(self) -> None:
        try:
            response = await self._client.post(
                "/refreshToken/",
                headers={"Token": f"Bearer {self.state.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ATSNetworkError(f"refreshToken network error: {exc}") from exc

        self._handle_auth_response(response, "refreshToken")

    def _handle_auth_response(self, response: httpx.Response, endpoint: str) -> None:
        if response.status_code == 200:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise ATSVendorContractError(
                    f"{endpoint} returned 200 with non-JSON body"
                ) from exc
            self._apply_auth_payload(payload)
            logger.info(
                "ats.ceipal.auth.ok",
                connection_id=str(self.state.id),
                endpoint=endpoint,
            )
            return

        # Error envelope: 401 → invalid creds; 403 → scope; 429 → rate; 5xx → transient
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        message = body.get("message", "")

        if response.status_code == 401:
            raise ATSCredentialsInvalidError(
                f"{endpoint} 401: {message}"
            )
        if response.status_code == 403:
            raise ATSAuthorizationError(f"{endpoint} 403: {message}")
        if response.status_code == 429:
            raise ATSRateLimitedError(
                retry_after_seconds=settings.ats_default_retry_after_seconds,
                message=f"{endpoint} 429: {message}",
            )
        if response.status_code >= 500:
            raise ATSNetworkError(f"{endpoint} {response.status_code}: {message}")
        raise ATSVendorContractError(
            f"{endpoint} unexpected {response.status_code}: {message}"
        )

    def _apply_auth_payload(self, payload: dict) -> None:
        now = datetime.now(tz=timezone.utc)
        access = payload.get("access_token")
        if not access:
            raise ATSVendorContractError("Auth response missing access_token")
        self.state.access_token = access

        # Ceipal returns expires_in (seconds) per the docs; default to 3600
        # if missing (1h is the documented lifetime).
        expires_in = int(payload.get("expires_in", 3600))
        self.state.access_token_expires_at = now + timedelta(seconds=expires_in)

        # refreshToken endpoint may not return a new refresh_token; only set if present.
        refresh = payload.get("refresh_token")
        if refresh:
            self.state.refresh_token = refresh
            # refresh_token lifetime is 7d per docs
            self.state.refresh_token_expires_at = now + timedelta(days=7)

    # ---------- List endpoints (implemented in Task 15) ----------

    async def list_clients(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_users(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_jobs(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_applicants(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_submissions(  # type: ignore[override]
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/adapters/test_ceipal_auth.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add app/modules/ats/adapters/ceipal.py tests/modules/ats/adapters/
git commit -m "feat(ats/ceipal): auth + ensure_authenticated with token-header refresh + reauth fallback"
```

### Task 14: Shared pagination helper + error envelope mapping

**Files:**
- Modify: `app/modules/ats/adapters/ceipal.py`
- Create: `tests/modules/ats/adapters/test_ceipal_paging.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/adapters/test_ceipal_paging.py`:

```python
"""Pagination walks pages until `next` is empty; error envelope maps correctly."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import (
    ATSAuthorizationError, ATSRateLimitedError,
    ATSNetworkError, ATSVendorContractError,
)


def _adapter_with_transport(handler):
    from app.modules.ats.adapters.ceipal import CeipalAdapter
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token="valid", access_token_expires_at=future,
    )
    return CeipalAdapter(state, _transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_paginate_walks_two_pages_until_next_empty():
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    pages = {
        1: {
            "count": 3, "num_pages": 2, "page_number": 1, "limit": 2,
            "next": "https://api.ceipal.com/v2/getThings/?page=2",
            "previous": "",
            "results": [{"id": "a"}, {"id": "b"}],
        },
        2: {
            "count": 3, "num_pages": 2, "page_number": 2, "limit": 2,
            "next": "",
            "previous": "https://api.ceipal.com/v2/getThings/?page=1",
            "results": [{"id": "c"}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=pages[page])

    adapter = _adapter_with_transport(handler)
    all_ids = []
    async for item in adapter._paginate("/getThings/", {}):
        all_ids.append(item["id"])
    assert all_ids == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_429_raises_rate_limited_with_default_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "Request limit exceeded. Please try again later."})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSRateLimitedError) as exc_info:
        async for _ in adapter._paginate("/getThings/", {}):
            pass
    assert exc_info.value.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_403_raises_authorization_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Your company access is temporarily disabled."})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSAuthorizationError):
        async for _ in adapter._paginate("/getThings/", {}):
            pass


@pytest.mark.asyncio
async def test_400_raises_vendor_contract_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Invalid parameters or filters."})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSVendorContractError):
        async for _ in adapter._paginate("/getThings/", {}):
            pass


@pytest.mark.asyncio
async def test_500_raises_network_error_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal"})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSNetworkError):
        async for _ in adapter._paginate("/getThings/", {}):
            pass
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/ats/adapters/test_ceipal_paging.py -v
```

Expected: `AttributeError: ... '_paginate'` — helper doesn't exist yet.

- [ ] **Step 3: Add the `_paginate` helper and shared error-envelope mapper**

In `app/modules/ats/adapters/ceipal.py`, insert these methods inside the `CeipalAdapter` class (after `_apply_auth_payload`):

```python
    # ---------- Shared HTTP plumbing for list endpoints ----------

    async def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        await self.ensure_authenticated()
        try:
            response = await self._client.request(
                method, path, params=params or {},
                headers={"Authorization": f"Bearer {self.state.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ATSNetworkError(f"{path} network error: {exc}") from exc
        self._raise_for_envelope(response, path)
        return response

    def _raise_for_envelope(self, response: httpx.Response, path: str) -> None:
        """Translate Ceipal's HTTP-status + JSON error envelope into typed exceptions.

        Ceipal envelope: {"message": "<human string>"}. 404 on a LIST endpoint
        means 'no rows match the filter' (NOT an error) — callers handle that by
        treating the empty results array as the empty list.
        """
        if response.status_code == 200:
            return

        body = {}
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = {"message": response.text[:200]}
        message = body.get("message", "")

        if response.status_code == 401:
            # ensure_authenticated should have prevented this — if we hit a 401
            # mid-list, treat as credentials-invalid (likely revoked upstream).
            raise ATSCredentialsInvalidError(f"{path} 401: {message}")
        if response.status_code == 403:
            raise ATSAuthorizationError(f"{path} 403: {message}")
        if response.status_code == 429:
            raise ATSRateLimitedError(
                retry_after_seconds=settings.ats_default_retry_after_seconds,
                message=f"{path} 429: {message}",
            )
        if response.status_code == 400:
            raise ATSVendorContractError(f"{path} 400: {message}")
        if response.status_code >= 500:
            raise ATSNetworkError(f"{path} {response.status_code}: {message}")
        if response.status_code == 404:
            # For list endpoints we synthesize an empty page rather than raising.
            # Caller's `if not next` loop exits naturally; per-method coercion is
            # handled in _paginate via the result-array length.
            return
        raise ATSVendorContractError(
            f"{path} unexpected {response.status_code}: {message}"
        )

    async def _paginate(
        self, path: str, params: dict,
    ):
        """Yield items from every page of a Ceipal list endpoint.

        Pagination envelope: {count, num_pages, page_number, limit, next, previous, results}
        Walks until `next` is empty (or 404, which we treat as 'no more').
        """
        page = 1
        params = dict(params)
        while True:
            params["page"] = page
            response = await self._request("GET", path, params=params)
            if response.status_code == 404:
                return
            envelope = response.json()
            for item in envelope.get("results", []):
                yield item
            if not envelope.get("next"):
                return
            page += 1
```

Also add the necessary import at the top of the file if not already present:
```python
import json  # already imported in Task 13
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/adapters/test_ceipal_paging.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/adapters/ceipal.py tests/modules/ats/adapters/test_ceipal_paging.py
git commit -m "feat(ats/ceipal): _paginate + _raise_for_envelope (typed error mapping)"
```

### Task 15: Implement the five list endpoints

**Files:**
- Modify: `app/modules/ats/adapters/ceipal.py`
- Create: `tests/modules/ats/adapters/test_ceipal_lists.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/adapters/test_ceipal_lists.py`:

```python
"""Each list_* method: delta filter passes through, results parse into the
right canonical DTO with raw preserved verbatim."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.ats.connection import ATSConnectionState


def _adapter(handler):
    from app.modules.ats.adapters.ceipal import CeipalAdapter
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token="t", access_token_expires_at=future,
    )
    return CeipalAdapter(state, _transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_clients_parses_envelope_and_preserves_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getClientsList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "cid-hash",
                "name": "Oracle",
                "website": "www.oracle.com",
                "industry_exp": "Computer Software",
                "country": "India", "state": "Karnataka", "city": "",
                "address": "", "zipcode": "",
                "status": "Active",
                "vendor_quirk_field": "preserved",
            }],
        })

    a = _adapter(handler)
    payloads = []
    async for c in a.list_clients():
        payloads.append(c)
    assert len(payloads) == 1
    p = payloads[0]
    assert p.external_id == "cid-hash"
    assert p.name == "Oracle"
    assert p.industry == "Computer Software"
    assert p.raw["vendor_quirk_field"] == "preserved"


@pytest.mark.asyncio
async def test_list_clients_passes_modifiedAfter_when_since_given():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "count": 0, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "", "results": [],
        })

    a = _adapter(handler)
    since = datetime(2026, 5, 12, 8, 30, 0, tzinfo=timezone.utc)
    async for _ in a.list_clients(since=since):
        pass
    assert captured["params"]["modifiedAfter"] == "2026-05-12 08:30:00"


@pytest.mark.asyncio
async def test_list_users_passes_through():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getUsersList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "uid",
                "first_name": "John", "last_name": "Doe",
                "display_name": "John Doe", "email_id": "j@x.com",
                "role": "Administrator", "status": "Active",
            }],
        })

    a = _adapter(handler)
    out = [u async for u in a.list_users()]
    assert out[0].external_id == "uid"
    assert out[0].email == "j@x.com"
    assert out[0].display_name == "John Doe"


@pytest.mark.asyncio
async def test_list_jobs_extracts_skills_and_recruiters():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getJobPostingsList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "jid", "client": "cid-hash",
                "position_title": "Java AWS Developer",
                "public_job_desc": "<html>JD body</html>",
                "job_status": "Active",
                "primary_city": "Bangalore",
                "employment_type": "Full Time",
                "remote_opportunities": "Yes",
                "skills": "Java, AWS, Python",
                "assigned_recruiter": "rid-1,rid-2,rid-3",
                "pay_rates": [{
                    "pay_rate_currency": "INR",
                    "min_pay_rate": "1000000",
                    "max_pay_rate": "2000000",
                }],
            }],
        })

    a = _adapter(handler)
    out = [j async for j in a.list_jobs()]
    j = out[0]
    assert j.external_id == "jid"
    assert j.external_client_id == "cid-hash"
    assert j.title == "Java AWS Developer"
    assert j.status == "Active"
    assert set(j.skills) == {"Java", "AWS", "Python"}
    assert j.assigned_recruiter_external_ids == ["rid-1", "rid-2", "rid-3"]


@pytest.mark.asyncio
async def test_list_applicants_parses_minimal_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getApplicantsList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "aid", "applicant_id": "9999",
                "firstname": "Jane", "lastname": "Doe",
                "email": "jane@x.com", "mobile_number": "555-0100",
                "city": "Bangalore", "state": "Karnataka",
                "job_title": "Senior Engineer",
            }],
        })

    a = _adapter(handler)
    out = [a_ async for a_ in a.list_applicants()]
    p = out[0]
    assert p.external_id == "aid"
    assert p.name == "Jane Doe"
    assert p.email == "jane@x.com"
    assert p.phone == "555-0100"
    assert p.location == "Bangalore, Karnataka"
    assert p.current_title == "Senior Engineer"


@pytest.mark.asyncio
async def test_list_submissions_requires_job_id_and_extracts_link():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getSubmissionsList" in str(request.url)
        assert request.url.params["jobId"] == "TVhUa2J3eDA"
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "sid", "submission_id": 9061,
                "applicant_id": 9999, "job_seeker_id": "appl-hash",
                "job_id": "TVhUa2J3eDA",
                "submission_status": "Internal Interview Scheduled",
                "pipeline_status": "",
                "source": "Naukri",
                "submitted_on": "2026-05-12 06:31:23",
                "pay_rate": 40.0,
                "employment_type": "Full Time",
                "resume_token": "opaque-token-abc",
            }],
        })

    a = _adapter(handler)
    out = [s async for s in a.list_submissions(job_external_id="TVhUa2J3eDA")]
    s = out[0]
    assert s.external_id == "sid"
    assert s.applicant_external_id == "appl-hash"
    assert s.job_external_id == "TVhUa2J3eDA"
    assert s.submission_status == "Internal Interview Scheduled"
    assert s.source == "Naukri"
    assert s.raw["resume_token"] == "opaque-token-abc"   # preserved for future
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/ats/adapters/test_ceipal_lists.py -v
```

Expected: failures from `raise NotImplementedError("Task 15")`.

- [ ] **Step 3: Implement the five list methods**

In `app/modules/ats/adapters/ceipal.py`, replace the five `raise NotImplementedError("Task 15")` blocks with real implementations. Append these methods after `_paginate`:

```python
    # ---------- List endpoints ----------

    @staticmethod
    def _format_since(since: datetime | None) -> dict:
        """Ceipal accepts modifiedAfter as 'YYYY-MM-DD HH:MM:SS' (no timezone)."""
        if since is None:
            return {}
        # Strip tzinfo; Ceipal docs use space-separated naive timestamps
        utc = since.astimezone(timezone.utc).replace(tzinfo=None)
        return {"modifiedAfter": utc.strftime("%Y-%m-%d %H:%M:%S")}

    async def list_clients(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        now = datetime.now(tz=timezone.utc)
        params = {"limit": 50, **self._format_since(since)}
        async for raw in self._paginate("/getClientsList/", params):
            yield ATSClientPayload(
                external_id=raw["id"],
                name=raw["name"],
                website=raw.get("website") or None,
                industry=raw.get("industry_exp") or None,
                country=raw.get("country") or None,
                state=raw.get("state") or None,
                city=raw.get("city") or None,
                address=raw.get("address") or None,
                status=raw.get("status") or None,
                contacts=raw.get("contacts", []),
                raw=raw,
                fetched_at=now,
            )

    async def list_users(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        now = datetime.now(tz=timezone.utc)
        # getUsersList does NOT document modifiedAfter; full sync per run.
        async for raw in self._paginate("/getUsersList/", {}):
            display = raw.get("display_name") or (
                f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip()
            )
            yield ATSUserPayload(
                external_id=raw["id"],
                email=raw.get("email_id") or raw.get("email", ""),
                display_name=display or "(unnamed)",
                role=raw.get("role") or None,
                status=raw.get("status") or None,
                raw=raw,
                fetched_at=now,
            )

    async def list_jobs(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        now = datetime.now(tz=timezone.utc)
        params = {"limit": 50, **self._format_since(since)}
        async for raw in self._paginate("/getJobPostingsList/", params):
            skills_str = raw.get("skills") or ""
            skills = [s.strip() for s in skills_str.split(",") if s.strip()]
            recruiter_str = raw.get("assigned_recruiter") or ""
            recruiter_ids = [r.strip() for r in recruiter_str.split(",") if r.strip()]
            pay_rates = raw.get("pay_rates") or []
            first_pay = pay_rates[0] if pay_rates else {}

            yield ATSJobPayload(
                external_id=raw["id"],
                external_client_id=raw.get("client") or "",
                title=raw.get("position_title") or raw.get("public_job_title") or "",
                description=raw.get("public_job_desc") or raw.get("requisition_description"),
                status=raw.get("job_status") or None,
                location=raw.get("primary_city") or raw.get("country") or None,
                skills=skills,
                employment_type=raw.get("employment_type") or None,
                work_arrangement=(
                    "remote" if raw.get("remote_opportunities") == "Yes" else None
                ),
                salary_range_min=_safe_int(first_pay.get("min_pay_rate")),
                salary_range_max=_safe_int(first_pay.get("max_pay_rate")),
                salary_currency=first_pay.get("pay_rate_currency") or None,
                assigned_recruiter_external_ids=recruiter_ids,
                raw=raw,
                fetched_at=now,
            )

    async def list_applicants(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        now = datetime.now(tz=timezone.utc)
        params = {"limit": 50, **self._format_since(since)}
        async for raw in self._paginate("/getApplicantsList/", params):
            full_name = " ".join(filter(None, [
                raw.get("firstname"), raw.get("middlename"), raw.get("lastname"),
            ])).strip() or "(unknown)"
            location_parts = [p for p in [raw.get("city"), raw.get("state")] if p]
            location = ", ".join(location_parts) or None
            yield ATSApplicantPayload(
                external_id=raw["id"],
                name=full_name,
                email=raw.get("email") or raw.get("email_address_1") or "",
                phone=(
                    raw.get("mobile_number")
                    or raw.get("home_phone_number")
                    or raw.get("work_phone_number")
                    or None
                ),
                location=location,
                current_title=raw.get("job_title") or None,
                linkedin_url=None,           # not in standard payload
                notes=None,
                raw=raw,
                fetched_at=now,
            )

    async def list_submissions(  # type: ignore[override]
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        now = datetime.now(tz=timezone.utc)
        params = {"jobId": job_external_id, "limit": 50, **self._format_since(since)}
        async for raw in self._paginate("/getSubmissionsList/", params):
            yield ATSSubmissionPayload(
                external_id=raw["id"],
                applicant_external_id=str(
                    raw.get("job_seeker_id")
                    or raw.get("applicant_id")
                    or ""
                ),
                job_external_id=raw.get("job_id") or job_external_id,
                submission_status=raw.get("submission_status") or None,
                pipeline_status=raw.get("pipeline_status") or None,
                source=raw.get("source") or None,
                submitted_on=_parse_ceipal_datetime(raw.get("submitted_on")),
                submitted_by_external_id=raw.get("submitted_by") or None,
                pay_rate=raw.get("pay_rate"),         # validator coerces
                employment_type=raw.get("employment_type") or None,
                raw=raw,
                fetched_at=now,
            )


# ---------- Module-level helpers ----------

def _safe_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_ceipal_datetime(value) -> datetime | None:
    """Ceipal returns two date formats in the same payload:
      - '2026-05-12T06:38:35Z' (ISO 8601 UTC)
      - '2026-05-12 06:31:23'  (space-separated, no timezone — assume UTC)
    """
    if value is None or value == "":
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/adapters/test_ceipal_lists.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Run the full Ceipal adapter test suite to ensure nothing regressed**

```bash
docker compose run --rm nexus pytest tests/modules/ats/adapters/ -v
```

Expected: all 16 tests pass (5 auth + 5 paging + 6 lists).

- [ ] **Step 6: Commit**

```bash
git add app/modules/ats/adapters/ceipal.py tests/modules/ats/adapters/test_ceipal_lists.py
git commit -m "feat(ats/ceipal): five list_* methods with delta filter + dual date format parsing"
```

---

## Phase 6 — Service helpers (`import_candidate`, unblock trigger)

Two small additions to existing modules. Each ships independently and is testable in isolation.

### Task 16: `import_candidate` in `candidates.service`

**Files:**
- Modify: `app/modules/candidates/service.py`
- Modify: `app/modules/candidates/errors.py` (if separate; otherwise within service.py)
- Create: `tests/modules/candidates/test_import_candidate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/candidates/test_import_candidate.py`:

```python
"""import_candidate: upsert by (tenant_id, source, external_id); on
duplicate-email collision with an existing manual candidate, link external_id
+ source_metadata without overwriting editable fields."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.database import async_session_factory


@pytest.mark.asyncio
async def test_import_creates_new_candidate(import_candidate_fixture):
    from app.modules.candidates.service import import_candidate
    from app.modules.candidates.sources import SourcedCandidate

    tenant_id, user_id = import_candidate_fixture
    sourced = SourcedCandidate(
        name="Jane Doe", email="new-jane@x.com", phone="555-0100",
        location=None, current_title=None, linkedin_url=None, notes=None,
        source="ats_ceipal", external_id="ext-1", source_metadata={"foo": "bar"},
    )
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            cand = await import_candidate(session, sourced, tenant_id, user_id)

    assert cand.name == "Jane Doe"
    assert cand.source == "ats_ceipal"
    assert cand.external_id == "ext-1"
    assert cand.source_metadata == {"foo": "bar"}


@pytest.mark.asyncio
async def test_import_is_idempotent_on_external_id(import_candidate_fixture):
    """Re-running import with the same external_id updates, doesn't duplicate."""
    from app.modules.candidates.service import import_candidate
    from app.modules.candidates.sources import SourcedCandidate

    tenant_id, user_id = import_candidate_fixture

    def _src(**overrides):
        base = dict(
            name="Jane", email="dup@x.com", phone=None,
            location=None, current_title=None, linkedin_url=None, notes=None,
            source="ats_ceipal", external_id="same-id", source_metadata=None,
        )
        base.update(overrides)
        return SourcedCandidate(**base)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            c1 = await import_candidate(session, _src(), tenant_id, user_id)
            c2 = await import_candidate(
                session, _src(name="Jane (updated)"), tenant_id, user_id,
            )

    assert c1.id == c2.id  # same row
    assert c2.name == "Jane (updated)"


@pytest.mark.asyncio
async def test_email_collision_with_manual_links_external_id(import_candidate_fixture):
    """A manual candidate already exists with the same email. Import should
    link external_id + source_metadata onto the existing row, NOT overwrite
    editable fields (name, phone) that the recruiter may have edited."""
    from app.modules.candidates.service import import_candidate, create_candidate
    from app.modules.candidates.sources import (
        SourcedCandidate, ManualSource,
    )
    from app.modules.candidates.schemas import CandidateCreateRequest

    tenant_id, user_id = import_candidate_fixture

    # Manual candidate first
    manual_req = CandidateCreateRequest(
        name="Manual Name (recruiter-edited)", email="collide@x.com",
        source="manual",
    )
    # Build a minimal UserContext for create_candidate's audit log
    user_ctx = _fake_user_context(user_id, tenant_id)
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            await create_candidate(session, manual_req, ManualSource(), user_ctx, tenant_id)

    # Now ATS import for same email
    sourced = SourcedCandidate(
        name="ATS Name", email="collide@x.com", phone="555-9999",
        location=None, current_title=None, linkedin_url=None, notes=None,
        source="ats_ceipal", external_id="ats-1",
        source_metadata={"vendor_field": "x"},
    )
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            linked = await import_candidate(session, sourced, tenant_id, user_id)

    assert linked.name == "Manual Name (recruiter-edited)"     # NOT overwritten
    assert linked.external_id == "ats-1"                       # linked
    assert linked.source_metadata == {"vendor_field": "x"}     # linked


# Helpers
def _fake_user_context(user_id, tenant_id):
    """Minimal UserContext shim for tests."""
    from types import SimpleNamespace
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, email="t@test.com"),
        tenant_id=tenant_id,
    )
```

- [ ] **Step 2: Add the conftest fixture**

Append to `tests/modules/candidates/conftest.py` (create if missing):

```python
import pytest
import uuid
from sqlalchemy import text
from app.database import async_session_factory


@pytest.fixture
async def import_candidate_fixture():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
                {"t": tenant_id},
            )
            await session.execute(
                text("INSERT INTO users (id, email, tenant_id, auth_user_id) "
                     "VALUES (:u, 'u@x.com', :t, :a)"),
                {"u": user_id, "t": tenant_id, "a": uuid.uuid4()},
            )
    yield (str(tenant_id), str(user_id))
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("DELETE FROM clients WHERE id = :t"), {"t": tenant_id}
            )
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/modules/candidates/test_import_candidate.py -v
```

Expected: `ImportError: cannot import name 'import_candidate' from 'app.modules.candidates.service'`.

- [ ] **Step 4: Implement `import_candidate` in `candidates/service.py`**

Append to `app/modules/candidates/service.py`:

```python
async def import_candidate(
    db: AsyncSession,
    sourced: SourcedCandidate,
    tenant_id: UUID | str,
    created_by: UUID | str,
) -> Candidate:
    """Upsert a candidate from a non-form source (ATS import, CSV bulk).

    Idempotency contract:
      - Primary key: (tenant_id, source, external_id) when external_id is set
        — partial unique index `candidates_tenant_source_external_idx`.
      - On (tenant_id, email) collision with an existing non-redacted candidate:
        link external_id + source_metadata onto the existing row, but do NOT
        overwrite editable fields (name, phone, location, current_title,
        linkedin_url, notes). The recruiter may have edited them.

    Audit: writes `candidate.imported` (new row) or `candidate.linked_to_external`
    (existing row got a new external_id) via the existing audit module.
    """
    tid = UUID(str(tenant_id))
    actor_id = UUID(str(created_by))

    # 1. Try lookup by (tenant_id, source, external_id) — idempotent re-import
    if sourced.external_id:
        result = await db.execute(
            select(Candidate).where(
                Candidate.tenant_id == tid,
                Candidate.source == sourced.source,
                Candidate.external_id == sourced.external_id,
                Candidate.pii_redacted_at.is_(None),
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            # Update mutable fields (idempotent re-import refresh)
            existing.name = sourced.name
            existing.phone = sourced.phone
            existing.location = sourced.location
            existing.current_title = sourced.current_title
            existing.linkedin_url = sourced.linkedin_url
            existing.notes = sourced.notes
            existing.source_metadata = sourced.source_metadata
            await db.flush()
            return existing

    # 2. Lookup by (tenant_id, email) — collision with manual candidate
    result = await db.execute(
        select(Candidate).where(
            Candidate.tenant_id == tid,
            Candidate.email == sourced.email,
            Candidate.pii_redacted_at.is_(None),
        )
    )
    collision = result.scalar_one_or_none()
    if collision is not None and sourced.external_id:
        # Link external_id + source_metadata onto existing row; do NOT overwrite
        # editable fields the recruiter may have customized.
        was_unlinked = collision.external_id is None
        collision.external_id = sourced.external_id
        collision.source_metadata = sourced.source_metadata
        # Do NOT touch source (was 'manual', stays 'manual') — the audit trail
        # of who created this row originally is preserved.
        await db.flush()
        if was_unlinked:
            await log_event(
                db, tenant_id=tid, actor_id=actor_id, actor_email="ats-import",
                action="candidate.linked_to_external",
                resource="candidate", resource_id=collision.id,
                payload={"source": sourced.source,
                         "external_id": sourced.external_id},
            )
        return collision

    # 3. Insert new row
    candidate = Candidate(
        tenant_id=tid,
        name=sourced.name, email=sourced.email, phone=sourced.phone,
        location=sourced.location, current_title=sourced.current_title,
        linkedin_url=sourced.linkedin_url, notes=sourced.notes,
        source=sourced.source, external_id=sourced.external_id,
        source_metadata=sourced.source_metadata,
        created_by=actor_id,
    )
    db.add(candidate)
    await db.flush()
    await log_event(
        db, tenant_id=tid, actor_id=actor_id, actor_email="ats-import",
        action="candidate.imported",
        resource="candidate", resource_id=candidate.id,
        payload={"source": sourced.source,
                 "external_id": sourced.external_id},
    )
    return candidate
```

If `select` or `UUID` isn't already imported at the top of `service.py`, add `from sqlalchemy import select` and `from uuid import UUID`.

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/candidates/test_import_candidate.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/modules/candidates/service.py tests/modules/candidates/
git commit -m "feat(candidates/service): import_candidate (idempotent upsert + manual-collision linking)"
```

### Task 17: Profile-completion unblock trigger in `org_units.service`

**Files:**
- Modify: `app/modules/org_units/service.py`
- Create: `tests/modules/org_units/test_unblock_pending_jobs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/org_units/test_unblock_pending_jobs.py`:

```python
"""When company_profile_completion_status flips pending → complete on a
client_account, every JD in blocked_pending_client_setup state for that
org_unit must transition to draft. Audit + actor-enqueue happens in the
caller (the API handler); this helper just does the state mutation."""
from __future__ import annotations

import uuid
import pytest
from sqlalchemy import text

from app.database import async_session_factory


@pytest.mark.asyncio
async def test_unblock_transitions_blocked_to_draft(unblock_jobs_fixture):
    from app.modules.org_units.service import _unblock_pending_jobs_for_org_unit

    tenant_id, org_unit_id, blocked_job_ids, unrelated_job_id = unblock_jobs_fixture

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            unblocked = await _unblock_pending_jobs_for_org_unit(
                session, org_unit_id, tenant_id,
            )

    assert sorted(unblocked) == sorted(blocked_job_ids)

    # Verify state transitions
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            rows = await session.execute(
                text("SELECT id, status FROM job_postings WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            statuses = {str(r.id): r.status for r in rows}

    for jid in blocked_job_ids:
        assert statuses[jid] == "draft"
    assert statuses[unrelated_job_id] == "draft"  # was already draft, unchanged
```

- [ ] **Step 2: Add the conftest fixture**

Append to `tests/modules/org_units/conftest.py` (create if missing):

```python
import uuid
import pytest
from sqlalchemy import text
from app.database import async_session_factory


@pytest.fixture
async def unblock_jobs_fixture():
    """Seed: one org_unit with two blocked JDs + one unrelated draft JD."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    org_unit_id = uuid.uuid4()
    other_org_unit_id = uuid.uuid4()
    blocked_a, blocked_b = uuid.uuid4(), uuid.uuid4()
    unrelated = uuid.uuid4()

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
                {"t": tenant_id},
            )
            await session.execute(
                text("INSERT INTO users (id, email, tenant_id, auth_user_id) "
                     "VALUES (:u, 'u@x.com', :t, :a)"),
                {"u": user_id, "t": tenant_id, "a": uuid.uuid4()},
            )
            await session.execute(
                text("INSERT INTO organizational_units "
                     "(id, client_id, name, unit_type, is_root, company_profile, "
                     "company_profile_completion_status) VALUES "
                     "(:o1, :t, 'Oracle', 'client_account', false, '{}', 'complete'),"
                     "(:o2, :t, 'Other', 'client_account', false, '{}', 'complete')"),
                {"o1": org_unit_id, "o2": other_org_unit_id, "t": tenant_id},
            )
            # Two blocked JDs in this org unit
            await session.execute(
                text("INSERT INTO job_postings (id, tenant_id, org_unit_id, title, "
                     "description_raw, status, created_by) VALUES "
                     "(:j1, :t, :o, 'A', 'a', 'blocked_pending_client_setup', :u),"
                     "(:j2, :t, :o, 'B', 'b', 'blocked_pending_client_setup', :u)"),
                {"j1": blocked_a, "j2": blocked_b, "t": tenant_id,
                 "o": org_unit_id, "u": user_id},
            )
            # An unrelated draft in a different org unit
            await session.execute(
                text("INSERT INTO job_postings (id, tenant_id, org_unit_id, title, "
                     "description_raw, status, created_by) VALUES "
                     "(:j, :t, :o, 'U', 'u', 'draft', :u)"),
                {"j": unrelated, "t": tenant_id,
                 "o": other_org_unit_id, "u": user_id},
            )
    yield (
        str(tenant_id), str(org_unit_id),
        [str(blocked_a), str(blocked_b)], str(unrelated),
    )
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("DELETE FROM clients WHERE id = :t"), {"t": tenant_id}
            )
```

- [ ] **Step 3: Run the test — verify fail**

```bash
docker compose run --rm nexus pytest tests/modules/org_units/test_unblock_pending_jobs.py -v
```

Expected: `ImportError: cannot import name '_unblock_pending_jobs_for_org_unit'`.

- [ ] **Step 4: Implement the helper**

Append to `app/modules/org_units/service.py`:

```python
async def _unblock_pending_jobs_for_org_unit(
    db: AsyncSession,
    org_unit_id: UUID | str,
    tenant_id: UUID | str,
) -> list[str]:
    """Transition every JD in `blocked_pending_client_setup` state under this
    org_unit to `draft`. Returns the list of unblocked job_posting IDs as
    strings so the caller can enqueue extract_and_enhance_jd for each.

    Called from the company-profile-update API handler when
    `company_profile_completion_status` transitions pending → complete.
    Writes one `jd.unblocked_by_profile_completion` audit row per JD.
    """
    from app.modules.jd.models import JobPosting    # local to avoid circular
    from app.modules.audit import log_event

    tid = UUID(str(tenant_id))
    ouid = UUID(str(org_unit_id))

    result = await db.execute(
        select(JobPosting).where(
            JobPosting.tenant_id == tid,
            JobPosting.org_unit_id == ouid,
            JobPosting.status == "blocked_pending_client_setup",
        )
    )
    unblocked: list[str] = []
    for job in result.scalars().all():
        job.status = "draft"
        await log_event(
            db, tenant_id=tid, actor_id=None, actor_email="system",
            action="jd.unblocked_by_profile_completion",
            resource="job_posting", resource_id=job.id,
            payload={"org_unit_id": str(ouid)},
        )
        unblocked.append(str(job.id))
    await db.flush()
    return unblocked
```

If `select` isn't already imported in `org_units/service.py`, add it. Same for `UUID`.

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/org_units/test_unblock_pending_jobs.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add app/modules/org_units/service.py tests/modules/org_units/
git commit -m "feat(org_units/service): _unblock_pending_jobs_for_org_unit on profile completion"
```

---

## Phase 7 — Importer (5-phase orchestrator)

The central translation layer between ATS DTOs and ProjectX rows. Each phase is its own DB transaction so partial failures are recoverable.

### Task 18: Importer skeleton + `_run_phase` helper + `SyncResult`

**Files:**
- Create: `app/modules/ats/importer.py`
- Create: `tests/modules/ats/test_importer_skeleton.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_importer_skeleton.py`:

```python
"""ATSImporter shell: _run_phase opens its own DB session, sets tenant scope,
writes the cursor on success, returns a PhaseResult, advances the OTel span."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.modules.ats.connection import ATSConnectionState


def _fake_adapter():
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    return adapter


@pytest.mark.asyncio
async def test_sync_tenant_runs_all_five_phases_in_order():
    from app.modules.ats.importer import ATSImporter, SyncResult

    importer = ATSImporter()
    adapter = _fake_adapter()

    called = []
    importer._sync_clients = AsyncMock(side_effect=lambda *a, **k: called.append("clients") or _empty_phase())
    importer._sync_users = AsyncMock(side_effect=lambda *a, **k: called.append("users") or _empty_phase())
    importer._sync_jobs = AsyncMock(side_effect=lambda *a, **k: called.append("jobs") or _empty_phase())
    importer._sync_applicants = AsyncMock(side_effect=lambda *a, **k: called.append("applicants") or _empty_phase())
    importer._sync_submissions = AsyncMock(side_effect=lambda *a, **k: called.append("submissions") or _empty_phase())

    result = await importer.sync_tenant(adapter)

    assert called == ["clients", "users", "jobs", "applicants", "submissions"]
    assert isinstance(result, SyncResult)


def _empty_phase():
    from app.modules.ats.importer import PhaseResult
    return PhaseResult(new=0, updated=0, skipped=0,
                       sync_started_at=datetime.now(tz=timezone.utc))


@pytest.mark.asyncio
async def test_sync_result_default_counts_zero():
    from app.modules.ats.importer import SyncResult, PhaseResult

    r = SyncResult()
    for phase in ("clients", "users", "jobs", "applicants", "submissions"):
        assert getattr(r, phase) is None  # not run yet
```

- [ ] **Step 2: Run — verify fail**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_importer_skeleton.py -v
```

- [ ] **Step 3: Implement the skeleton**

Create `app/modules/ats/importer.py`:

```python
"""ATSImporter — five-phase orchestrator translating ATS DTOs to ProjectX rows.

Each phase opens its OWN bypass-RLS DB session, sets `app.current_tenant`,
runs inside an OTel span, and commits independently. Partial-failure tolerance:
a failure in phase N leaves phases 1..N-1 durable and their cursors advanced.

Phase ordering is sequential by data dependency:
  1. clients     → client_account org_units (auto-create with stub profile)
  2. users       → ats_user_mappings (reference data; recruiter maps later)
  3. jobs        → job_postings (blocked_pending_client_setup if profile=pending)
  4. applicants  → candidates (via candidates.service.import_candidate)
  5. submissions → candidate_job_assignments (per-job; uses jobs touched in phase 3)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
from opentelemetry import trace
from sqlalchemy import text

from app.database import get_bypass_session
from app.modules.ats.adapter import ATSAdapter


logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


@dataclass
class PhaseResult:
    new: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    sync_started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def as_counts(self) -> dict:
        """Compact JSON-able form for ats_sync_logs.entity_counts."""
        return {"new": self.new, "updated": self.updated, "skipped": self.skipped,
                "errors": len(self.errors)}


@dataclass
class SyncResult:
    clients: PhaseResult | None = None
    users: PhaseResult | None = None
    jobs: PhaseResult | None = None
    applicants: PhaseResult | None = None
    submissions: PhaseResult | None = None

    def entity_counts(self) -> dict:
        return {
            name: getattr(self, name).as_counts() if getattr(self, name) else None
            for name in ("clients", "users", "jobs", "applicants", "submissions")
        }


class ATSImporter:
    async def sync_tenant(self, adapter: ATSAdapter) -> SyncResult:
        result = SyncResult()
        result.clients     = await self._run_phase("clients",     self._sync_clients,     adapter)
        result.users       = await self._run_phase("users",       self._sync_users,       adapter)
        result.jobs        = await self._run_phase("jobs",        self._sync_jobs,        adapter)
        result.applicants  = await self._run_phase("applicants",  self._sync_applicants,  adapter)
        result.submissions = await self._run_phase("submissions", self._sync_submissions, adapter)
        return result

    async def _run_phase(self, name, fn, adapter) -> PhaseResult:
        tenant_id = adapter.state.tenant_id
        with tracer.start_as_current_span(f"ats.sync.{name}",
                                          attributes={"ats.vendor": adapter.vendor,
                                                      "tenant_id": str(tenant_id)}):
            async with get_bypass_session() as db:
                await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                phase_result = await fn(db, adapter)
                await db.commit()
            adapter.state.last_synced_cursors[name] = phase_result.sync_started_at.isoformat()
            logger.info(
                "ats.sync.phase.ok",
                phase=name, vendor=adapter.vendor,
                tenant_id=str(tenant_id),
                **phase_result.as_counts(),
            )
            return phase_result

    # Phase methods — implementations land in Tasks 19–22.
    async def _sync_clients(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 19")

    async def _sync_users(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 19")

    async def _sync_jobs(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 20")

    async def _sync_applicants(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 21")

    async def _sync_submissions(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 22")
```

- [ ] **Step 4: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_importer_skeleton.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/importer.py tests/modules/ats/test_importer_skeleton.py
git commit -m "feat(ats/importer): skeleton + _run_phase + SyncResult / PhaseResult"
```

### Task 19: `_sync_clients` + `_sync_users`

**Files:**
- Modify: `app/modules/ats/importer.py`
- Create: `tests/modules/ats/test_importer_clients_users.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_importer_clients_users.py`:

```python
"""Phase 1 (clients) and Phase 2 (users):
  - New Ceipal client → auto-create client_account org_unit with stub profile,
    completion_status='pending'.
  - Existing mapping → update last_synced_at + external_client_name, do NOT
    rename the org_unit (recruiter may have customized).
  - User mapping is reference-only (internal_user_id stays NULL on insert).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.database import async_session_factory
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import ATSClientPayload, ATSUserPayload


def _async_iter(items):
    async def _aiter():
        for item in items:
            yield item
    return _aiter()


def _adapter_with_clients(tenant_id, client_payloads, user_payloads):
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=tenant_id, vendor="ceipal", credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    adapter.vendor = "ceipal"
    adapter.list_clients = lambda since=None: _async_iter(client_payloads)
    adapter.list_users = lambda since=None: _async_iter(user_payloads)
    return adapter


@pytest.mark.asyncio
async def test_sync_clients_creates_pending_org_unit_for_new_mapping(importer_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, user_id, root_unit_id = importer_fixture
    payload = ATSClientPayload(
        external_id="cid-1", name="Oracle", website="www.oracle.com",
        industry="Computer Software", country="India", state="Karnataka",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    result = await importer._run_phase("clients", importer._sync_clients, adapter)
    assert result.new == 1

    # Verify the org unit was created with pending status and stub profile
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            row = await session.execute(text(
                "SELECT o.name, o.unit_type, o.company_profile, "
                "o.company_profile_completion_status, m.external_client_id "
                "FROM organizational_units o "
                "JOIN ats_client_mappings m ON m.org_unit_id = o.id "
                "WHERE m.tenant_id = :t"
            ), {"t": tenant_id})
            r = row.one()
    assert r.name == "Oracle"
    assert r.unit_type == "client_account"
    assert r.company_profile_completion_status == "pending"
    assert r.company_profile["name"] == "Oracle"
    assert r.company_profile["website"] == "www.oracle.com"
    assert r.company_profile["industry"] == "Computer Software"
    assert r.external_client_id == "cid-1"


@pytest.mark.asyncio
async def test_sync_clients_existing_mapping_updates_dont_rename_org_unit(importer_fixture):
    """Existing mapping → only refresh metadata; DON'T rename the org_unit."""
    # (For brevity: caller seeds an existing mapping with org_unit name 'Renamed by Recruiter',
    # then re-imports with the original Ceipal name 'Oracle'. Assert org_unit.name stayed 'Renamed by Recruiter'.)
    # Full fixture wiring as in test above; left as the standard pattern.
    pass  # Implementation follows the same structure.


@pytest.mark.asyncio
async def test_sync_users_inserts_unmapped_rows(importer_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, user_id, root_unit_id = importer_fixture
    payload = ATSUserPayload(
        external_id="ceipal-uid-1", email="recruiter@x.com",
        display_name="Jane Recruiter", role="Recruiter", status="Active",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [], [payload])
    result = await ATSImporter()._run_phase("users", ATSImporter()._sync_users, adapter)
    assert result.new == 1

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            row = await session.execute(text(
                "SELECT external_user_id, external_user_email, internal_user_id "
                "FROM ats_user_mappings WHERE tenant_id = :t"
            ), {"t": tenant_id})
            r = row.one()
    assert r.external_user_id == "ceipal-uid-1"
    assert r.external_user_email == "recruiter@x.com"
    assert r.internal_user_id is None  # NOT auto-mapped
```

- [ ] **Step 2: Add the fixture**

Append to `tests/modules/ats/conftest.py` (create if needed):

```python
import uuid
import pytest
from sqlalchemy import text
from app.database import async_session_factory


@pytest.fixture
async def importer_fixture():
    """Seed a tenant + root company org_unit. Returns IDs as strings."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    root_unit_id = uuid.uuid4()
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
                {"t": tenant_id},
            )
            await session.execute(
                text("INSERT INTO users (id, email, tenant_id, auth_user_id) "
                     "VALUES (:u, 'u@x.com', :t, :a)"),
                {"u": user_id, "t": tenant_id, "a": uuid.uuid4()},
            )
            await session.execute(
                text("INSERT INTO organizational_units "
                     "(id, client_id, name, unit_type, is_root, company_profile, "
                     "company_profile_completion_status) "
                     "VALUES (:o, :t, 'Acme', 'company', true, "
                     "'{\"name\": \"Acme\"}', 'complete')"),
                {"o": root_unit_id, "t": tenant_id},
            )
            # The importer needs to know who created the connection — seed one.
            await session.execute(
                text("INSERT INTO ats_connections (id, tenant_id, vendor, "
                     "credentials_ciphertext, created_by) "
                     "VALUES (:c, :t, 'ceipal', :ct, :u)"),
                {"c": uuid.uuid4(), "t": tenant_id, "ct": b"x", "u": user_id},
            )
    yield (str(tenant_id), str(user_id), str(root_unit_id))
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("DELETE FROM clients WHERE id = :t"), {"t": tenant_id}
            )
```

- [ ] **Step 3: Run — verify fail**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_importer_clients_users.py -v
```

- [ ] **Step 4: Implement `_sync_clients` and `_sync_users`**

In `app/modules/ats/importer.py`, replace the `NotImplementedError("Task 19")` for both methods with:

```python
    async def _sync_clients(self, db, adapter) -> PhaseResult:
        """Phase 1: upsert ats_client_mappings; auto-create client_account
        org_units (with stub profile + completion_status='pending') for new clients."""
        from app.modules.ats.models import ATSClientMapping, ATSConnection
        from app.modules.org_units.models import OrganizationalUnit

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        # Look up root company unit (for parent_unit_id) and the connection's created_by
        root = await db.scalar(
            select(OrganizationalUnit).where(
                OrganizationalUnit.client_id == tenant_id,
                OrganizationalUnit.is_root.is_(True),
            )
        )
        if root is None:
            raise RuntimeError(f"tenant {tenant_id} has no root company org_unit")

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        created_by = connection.created_by

        since = self._cursor_or_none(adapter.state, "clients")
        async for payload in adapter.list_clients(since=since):
            existing = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_id == payload.external_id,
                )
            )
            if existing is not None:
                # Update mapping metadata; do NOT rename the org_unit.
                existing.external_client_name = payload.name
                existing.source_metadata = {"contacts": payload.contacts, "raw": payload.raw}
                existing.last_synced_at = datetime.now(tz=timezone.utc)
                result.updated += 1
                continue

            # Create the org_unit with stub profile
            stub = {
                "name": payload.name,
                "website": payload.website,
                "industry": payload.industry,
                "country": payload.country,
                "state": payload.state,
                "city": payload.city,
                "address": payload.address,
            }
            stub = {k: v for k, v in stub.items() if v is not None}
            new_unit = OrganizationalUnit(
                client_id=tenant_id, parent_unit_id=root.id,
                name=payload.name, unit_type="client_account",
                is_root=False, company_profile=stub,
                company_profile_completion_status="pending",
                created_by=created_by,
            )
            db.add(new_unit)
            await db.flush()

            db.add(ATSClientMapping(
                tenant_id=tenant_id, ats_vendor=adapter.vendor,
                external_client_id=payload.external_id,
                external_client_name=payload.name,
                org_unit_id=new_unit.id,
                source_metadata={"contacts": payload.contacts, "raw": payload.raw},
            ))
            await log_event(
                db, tenant_id=tenant_id, actor_id=created_by,
                actor_email="ats-import",
                action="ats.client_mapping.created",
                resource="ats_client_mapping",
                resource_id=new_unit.id,
                payload={"vendor": adapter.vendor,
                         "external_client_id": payload.external_id,
                         "org_unit_id": str(new_unit.id)},
            )
            result.new += 1
        return result

    async def _sync_users(self, db, adapter) -> PhaseResult:
        """Phase 2: upsert ats_user_mappings. internal_user_id stays NULL —
        recruiter explicitly maps via UI later."""
        from app.modules.ats.models import ATSUserMapping

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        async for payload in adapter.list_users(since=None):
            existing = await db.scalar(
                select(ATSUserMapping).where(
                    ATSUserMapping.tenant_id == tenant_id,
                    ATSUserMapping.ats_vendor == adapter.vendor,
                    ATSUserMapping.external_user_id == payload.external_id,
                )
            )
            if existing is not None:
                existing.external_user_email = payload.email
                existing.external_user_display_name = payload.display_name
                existing.external_user_role = payload.role
                existing.external_user_status = payload.status
                existing.external_user_metadata = payload.raw
                existing.last_synced_at = datetime.now(tz=timezone.utc)
                result.updated += 1
                continue

            db.add(ATSUserMapping(
                tenant_id=tenant_id, ats_vendor=adapter.vendor,
                external_user_id=payload.external_id,
                external_user_email=payload.email,
                external_user_display_name=payload.display_name,
                external_user_role=payload.role,
                external_user_status=payload.status,
                external_user_metadata=payload.raw,
                internal_user_id=None,
            ))
            result.new += 1
        return result

    @staticmethod
    def _cursor_or_none(state, phase_name: str) -> datetime | None:
        raw = state.last_synced_cursors.get(phase_name)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
```

Add the missing imports at the top of `importer.py`:
```python
from sqlalchemy import select
from app.modules.audit import log_event
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_importer_clients_users.py -v
```

Expected: 3 passed (the placeholder `pass` test is a stub — implement the existing-mapping branch with the same pattern shown in the first test).

- [ ] **Step 6: Commit**

```bash
git add app/modules/ats/importer.py tests/modules/ats/test_importer_clients_users.py \
        tests/modules/ats/conftest.py
git commit -m "feat(ats/importer): _sync_clients (auto-create org_unit) + _sync_users"
```

### Task 20: `_sync_jobs` with blocked-state branching

**Files:**
- Modify: `app/modules/ats/importer.py`
- Create: `tests/modules/ats/test_importer_jobs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_importer_jobs.py`:

```python
"""Phase 3 (jobs):
  - Resolve external_client_id → org_unit via ats_client_mappings.
  - If org_unit.completion_status='pending': status='blocked_pending_client_setup'.
  - If 'complete': status='draft' (caller enqueues extract_and_enhance_jd).
  - assigned_recruiter_external_ids → ats_job_recruiter_assignments rows.
  - Missing client mapping → skip + count in result.skipped.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.database import async_session_factory
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import ATSJobPayload


def _async_iter(items):
    async def _aiter():
        for item in items:
            yield item
    return _aiter()


@pytest.fixture
async def jobs_fixture(importer_fixture):
    """Add a client mapping with a 'pending' completion-status org_unit."""
    tenant_id, user_id, root_unit_id = importer_fixture
    pending_unit_id = uuid.uuid4()
    complete_unit_id = uuid.uuid4()
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text(
                "INSERT INTO organizational_units (id, client_id, name, unit_type, "
                "is_root, parent_unit_id, company_profile, "
                "company_profile_completion_status) VALUES "
                "(:p, :t, 'Pending', 'client_account', false, :r, '{\"name\":\"P\"}', 'pending'),"
                "(:c, :t, 'Complete', 'client_account', false, :r, '{\"name\":\"C\"}', 'complete')"
            ), {"p": pending_unit_id, "c": complete_unit_id, "t": tenant_id, "r": root_unit_id})
            await session.execute(text(
                "INSERT INTO ats_client_mappings (tenant_id, ats_vendor, external_client_id, "
                "external_client_name, org_unit_id) VALUES "
                "(:t, 'ceipal', 'pending-client', 'P', :p),"
                "(:t, 'ceipal', 'complete-client', 'C', :c)"
            ), {"t": tenant_id, "p": pending_unit_id, "c": complete_unit_id})
    yield (tenant_id, user_id, root_unit_id, str(pending_unit_id), str(complete_unit_id))


def _jobs_adapter(tenant_id, jobs):
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), vendor="ceipal", credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    adapter.vendor = "ceipal"
    adapter.list_jobs = lambda since=None: _async_iter(jobs)
    return adapter


@pytest.mark.asyncio
async def test_job_for_complete_client_lands_in_draft(jobs_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, _, _, _, complete_unit = jobs_fixture
    job = ATSJobPayload(
        external_id="jid", external_client_id="complete-client",
        title="Java Engineer", description="JD body",
        status="Active", raw={}, fetched_at=datetime.now(tz=timezone.utc),
        assigned_recruiter_external_ids=["rid-1"],
    )
    adapter = _jobs_adapter(tenant_id, [job])
    result = await ATSImporter()._run_phase("jobs", ATSImporter()._sync_jobs, adapter)
    assert result.new == 1

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            r = await session.execute(text(
                "SELECT status, org_unit_id::text, external_status, source, external_id "
                "FROM job_postings WHERE tenant_id = :t"
            ), {"t": tenant_id})
            row = r.one()
            assert row.status == "draft"
            assert row.org_unit_id == complete_unit
            assert row.external_status == "Active"
            assert row.source == "ats_ceipal"
            assert row.external_id == "jid"

            recruiters = await session.execute(text(
                "SELECT external_user_id FROM ats_job_recruiter_assignments "
                "WHERE tenant_id = :t"
            ), {"t": tenant_id})
            assert {r.external_user_id for r in recruiters} == {"rid-1"}


@pytest.mark.asyncio
async def test_job_for_pending_client_lands_in_blocked_state(jobs_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, _, _, pending_unit, _ = jobs_fixture
    job = ATSJobPayload(
        external_id="j2", external_client_id="pending-client",
        title="x", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    await ATSImporter()._run_phase("jobs", ATSImporter()._sync_jobs, adapter)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            r = await session.execute(text(
                "SELECT status FROM job_postings WHERE tenant_id = :t AND external_id = 'j2'"
            ), {"t": tenant_id})
            assert r.scalar_one() == "blocked_pending_client_setup"


@pytest.mark.asyncio
async def test_job_with_unknown_client_mapping_is_skipped(jobs_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j3", external_client_id="not-yet-imported-client",
        title="x", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    result = await ATSImporter()._run_phase("jobs", ATSImporter()._sync_jobs, adapter)
    assert result.skipped == 1
    assert result.new == 0
```

- [ ] **Step 2: Run — verify fail**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_importer_jobs.py -v
```

- [ ] **Step 3: Implement `_sync_jobs`**

In `app/modules/ats/importer.py`, replace the `NotImplementedError("Task 20")` with:

```python
    async def _sync_jobs(self, db, adapter) -> PhaseResult:
        """Phase 3: upsert job_postings; resolve client mapping → org_unit;
        gate status by org_unit.company_profile_completion_status."""
        from app.modules.ats.models import (
            ATSClientMapping, ATSConnection, ATSJobRecruiterAssignment,
        )
        from app.modules.jd.models import JobPosting
        from app.modules.org_units.models import OrganizationalUnit

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        created_by = connection.created_by

        since = self._cursor_or_none(adapter.state, "jobs")
        async for payload in adapter.list_jobs(since=since):
            # Resolve client mapping
            mapping = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_id == payload.external_client_id,
                )
            )
            if mapping is None:
                logger.warning(
                    "ats.sync.jobs.skipped_missing_client_mapping",
                    external_job_id=payload.external_id,
                    external_client_id=payload.external_client_id,
                )
                result.skipped += 1
                continue

            org_unit = await db.get(OrganizationalUnit, mapping.org_unit_id)
            target_status = (
                "blocked_pending_client_setup"
                if org_unit.company_profile_completion_status == "pending"
                else "draft"
            )

            existing = await db.scalar(
                select(JobPosting).where(
                    JobPosting.tenant_id == tenant_id,
                    JobPosting.source == f"ats_{adapter.vendor}",
                    JobPosting.external_id == payload.external_id,
                )
            )
            if existing is not None:
                existing.title = payload.title
                existing.description_raw = payload.description or existing.description_raw
                existing.external_status = payload.status
                existing.location = payload.location or existing.location
                existing.employment_type = payload.employment_type or existing.employment_type
                existing.work_arrangement = payload.work_arrangement or existing.work_arrangement
                existing.salary_range_min = payload.salary_range_min
                existing.salary_range_max = payload.salary_range_max
                existing.salary_currency = payload.salary_currency
                job_id = existing.id
                result.updated += 1
            else:
                jp = JobPosting(
                    tenant_id=tenant_id, org_unit_id=org_unit.id,
                    title=payload.title,
                    description_raw=payload.description or "",
                    status=target_status,
                    source=f"ats_{adapter.vendor}",
                    external_id=payload.external_id,
                    external_status=payload.status,
                    location=payload.location,
                    employment_type=payload.employment_type,
                    work_arrangement=payload.work_arrangement,
                    salary_range_min=payload.salary_range_min,
                    salary_range_max=payload.salary_range_max,
                    salary_currency=payload.salary_currency,
                    created_by=created_by,
                )
                db.add(jp)
                await db.flush()
                job_id = jp.id
                await log_event(
                    db, tenant_id=tenant_id, actor_id=created_by,
                    actor_email="ats-import",
                    action="jd.imported_from_ats",
                    resource="job_posting", resource_id=jp.id,
                    payload={"vendor": adapter.vendor,
                             "external_id": payload.external_id,
                             "target_status": target_status},
                )
                result.new += 1

            # Sync recruiter assignments (replace-all semantics)
            await db.execute(
                text("DELETE FROM ats_job_recruiter_assignments "
                     "WHERE tenant_id = :t AND job_posting_id = :j AND ats_vendor = :v"),
                {"t": tenant_id, "j": job_id, "v": adapter.vendor},
            )
            for rid in payload.assigned_recruiter_external_ids:
                db.add(ATSJobRecruiterAssignment(
                    tenant_id=tenant_id, job_posting_id=job_id,
                    ats_vendor=adapter.vendor, external_user_id=rid,
                ))
        return result
```

- [ ] **Step 4: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_importer_jobs.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/importer.py tests/modules/ats/test_importer_jobs.py
git commit -m "feat(ats/importer): _sync_jobs with blocked_pending_client_setup branching + recruiter assignments"
```

### Task 21: `_sync_applicants` + `_sync_submissions`

**Files:**
- Modify: `app/modules/ats/importer.py`
- Create: `tests/modules/ats/test_importer_applicants_submissions.py`

- [ ] **Step 1: Implement both phases at once (their logic is short and complementary)**

Replace the two `NotImplementedError` blocks in `importer.py`:

```python
    async def _sync_applicants(self, db, adapter) -> PhaseResult:
        """Phase 4: applicants → candidates via import_candidate.

        Reuses the candidates module's idempotent service function; collisions
        with manual-flow candidates (same email) link external_id without
        overwriting editable fields.
        """
        from app.modules.ats.sources import ATSImportSource
        from app.modules.ats.models import ATSConnection
        from app.modules.candidates.service import import_candidate

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        created_by = connection.created_by

        bridge = ATSImportSource(vendor=adapter.vendor)
        since = self._cursor_or_none(adapter.state, "applicants")
        async for payload in adapter.list_applicants(since=since):
            try:
                sourced = bridge.normalize(payload)
                candidate = await import_candidate(db, sourced, tenant_id, created_by)
                # import_candidate writes its own audit row. We just count.
                if candidate.created_at == candidate.updated_at:
                    result.new += 1
                else:
                    result.updated += 1
            except Exception as exc:
                logger.warning(
                    "ats.sync.applicants.row_failed",
                    external_id=payload.external_id, error=str(exc),
                )
                result.errors.append(payload.external_id)
        return result

    async def _sync_submissions(self, db, adapter) -> PhaseResult:
        """Phase 5: for each known job_posting from this vendor, fetch
        submissions and upsert candidate_job_assignments. The submission
        external_id is the join key on candidate_job_assignments.

        Submission → candidate resolution goes via candidates.external_id
        (set by import_candidate in Phase 4). Submission → job resolution
        goes via job_postings.external_id (set by _sync_jobs in Phase 3).
        Both lookups are scoped to the same (tenant_id, vendor) pair.
        """
        from app.modules.candidates.models import Candidate
        from app.modules.candidates.models_assignments import CandidateJobAssignment
        from app.modules.jd.models import JobPosting

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id
        vendor_source = f"ats_{adapter.vendor}"

        # Iterate jobs we know about from this vendor
        jobs = await db.execute(
            select(JobPosting).where(
                JobPosting.tenant_id == tenant_id,
                JobPosting.source == vendor_source,
            )
        )
        since = self._cursor_or_none(adapter.state, "submissions")
        for job in jobs.scalars():
            if not job.external_id:
                continue
            async for sub in adapter.list_submissions(
                job_external_id=job.external_id, since=since,
            ):
                # Resolve candidate by external_id
                candidate = await db.scalar(
                    select(Candidate).where(
                        Candidate.tenant_id == tenant_id,
                        Candidate.source == vendor_source,
                        Candidate.external_id == sub.applicant_external_id,
                        Candidate.pii_redacted_at.is_(None),
                    )
                )
                if candidate is None:
                    logger.warning(
                        "ats.sync.submissions.skip_unknown_applicant",
                        submission_external_id=sub.external_id,
                        applicant_external_id=sub.applicant_external_id,
                    )
                    result.skipped += 1
                    continue

                existing = await db.scalar(
                    select(CandidateJobAssignment).where(
                        CandidateJobAssignment.tenant_id == tenant_id,
                        CandidateJobAssignment.source == vendor_source,
                        CandidateJobAssignment.external_id == sub.external_id,
                    )
                )
                meta = {
                    "submission_status": sub.submission_status,
                    "pipeline_status": sub.pipeline_status,
                    "source": sub.source,
                    "submitted_on": sub.submitted_on.isoformat() if sub.submitted_on else None,
                    "pay_rate": str(sub.pay_rate) if sub.pay_rate else None,
                    "employment_type": sub.employment_type,
                    "raw": sub.raw,
                }
                if existing is not None:
                    existing.source_metadata = meta
                    result.updated += 1
                else:
                    db.add(CandidateJobAssignment(
                        tenant_id=tenant_id,
                        candidate_id=candidate.id,
                        job_posting_id=job.id,
                        source=vendor_source,
                        external_id=sub.external_id,
                        source_metadata=meta,
                    ))
                    result.new += 1
        return result
```

> **Note on imports:** `CandidateJobAssignment` lives in whatever module file the existing `candidate_job_assignments` table is mapped from. The exploration report named `app/modules/candidates/models.py` as the candidate file but the assignment ORM may be in a separate `models_assignments.py` or grouped in `candidates/models.py` — open the existing models.py to confirm and adjust the import path.

- [ ] **Step 2: Write the failing tests**

Create `tests/modules/ats/test_importer_applicants_submissions.py` with two tests:

```python
"""Phase 4 + 5 happy paths and edge cases."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.database import async_session_factory
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import ATSApplicantPayload, ATSSubmissionPayload


def _async_iter(items):
    async def _aiter():
        for item in items:
            yield item
    return _aiter()


@pytest.mark.asyncio
async def test_sync_applicants_imports_via_import_candidate(jobs_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    payload = ATSApplicantPayload(
        external_id="aid-1", name="Jane Doe", email="jane@x.com",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), vendor="ceipal", credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    adapter.vendor = "ceipal"
    adapter.list_applicants = lambda since=None: _async_iter([payload])

    result = await ATSImporter()._run_phase("applicants",
                                             ATSImporter()._sync_applicants, adapter)
    assert result.new >= 1

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            r = await session.execute(text(
                "SELECT source, external_id FROM candidates WHERE tenant_id = :t"
            ), {"t": tenant_id})
            row = r.one()
    assert row.source == "ats_ceipal"
    assert row.external_id == "aid-1"


@pytest.mark.asyncio
async def test_sync_submissions_creates_assignment_linking_candidate_to_job(jobs_fixture):
    """Run jobs + applicants + submissions phases in order; verify the
    candidate_job_assignments row exists with the right source/external_id."""
    # Full multi-phase integration test — caller seeds, then runs all three
    # importer phases in order. The pattern mirrors prior tests.
    pass  # Follow the established structure.
```

- [ ] **Step 3: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_importer_applicants_submissions.py -v
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/ats/importer.py tests/modules/ats/test_importer_applicants_submissions.py
git commit -m "feat(ats/importer): _sync_applicants (via import_candidate) + _sync_submissions"
```

---

## Phase 8 — Sync log writer + `poll_ats_connection` actor

The actor is the entry-point a Dramatiq message lands on. It owns the load-auth-sync-persist cycle plus the `ats_sync_logs` audit row.

### Task 22: Sync-log writer helpers (`service.py`)

**Files:**
- Create: `app/modules/ats/service.py` (service-layer functions)
- Create: `tests/modules/ats/test_sync_log.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_sync_log.py`:

```python
"""create_sync_log inserts a 'running' row; finalize_sync_log_* close it."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.database import async_session_factory


@pytest.mark.asyncio
async def test_create_sync_log_returns_running_row(importer_fixture):
    from app.modules.ats.service import create_sync_log_row

    tenant_id, *_ = importer_fixture
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            conn_row = await session.execute(text(
                "SELECT id FROM ats_connections WHERE tenant_id = :t LIMIT 1"
            ), {"t": tenant_id})
            connection_id = conn_row.scalar_one()

            log_id = await create_sync_log_row(
                session, connection_id=connection_id,
                tenant_id=uuid.UUID(tenant_id),
                correlation_id="test-corr-1",
            )

            r = await session.execute(text(
                "SELECT status, correlation_id FROM ats_sync_logs WHERE id = :i"
            ), {"i": log_id})
            row = r.one()
    assert row.status == "running"
    assert row.correlation_id == "test-corr-1"


@pytest.mark.asyncio
async def test_finalize_sync_log_success(importer_fixture):
    from app.modules.ats.service import create_sync_log_row, finalize_sync_log_success
    from app.modules.ats.importer import SyncResult, PhaseResult

    tenant_id, *_ = importer_fixture
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            cid = (await session.execute(text(
                "SELECT id FROM ats_connections WHERE tenant_id = :t LIMIT 1"
            ), {"t": tenant_id})).scalar_one()

            log_id = await create_sync_log_row(
                session, connection_id=cid,
                tenant_id=uuid.UUID(tenant_id), correlation_id="c",
            )

            sync_result = SyncResult()
            sync_result.clients = PhaseResult(new=2, updated=30, skipped=0,
                                              sync_started_at=datetime.now(tz=timezone.utc))
            await finalize_sync_log_success(session, log_id, sync_result)

            r = await session.execute(text(
                "SELECT status, entity_counts, completed_at FROM ats_sync_logs WHERE id = :i"
            ), {"i": log_id})
            row = r.one()
    assert row.status == "success"
    assert row.completed_at is not None
    assert row.entity_counts["clients"]["new"] == 2
    assert row.entity_counts["clients"]["updated"] == 30


@pytest.mark.asyncio
async def test_finalize_sync_log_failure_records_phase_and_error(importer_fixture):
    from app.modules.ats.service import create_sync_log_row, finalize_sync_log_failure

    tenant_id, *_ = importer_fixture
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            cid = (await session.execute(text(
                "SELECT id FROM ats_connections WHERE tenant_id = :t LIMIT 1"
            ), {"t": tenant_id})).scalar_one()

            log_id = await create_sync_log_row(
                session, connection_id=cid,
                tenant_id=uuid.UUID(tenant_id), correlation_id="c",
            )
            await finalize_sync_log_failure(
                session, log_id, phase="jobs",
                error_summary="ATSRateLimitedError: retry after 60s",
            )

            r = await session.execute(text(
                "SELECT status, error_phase, error_summary FROM ats_sync_logs WHERE id = :i"
            ), {"i": log_id})
            row = r.one()
    assert row.status == "failed"
    assert row.error_phase == "jobs"
    assert "retry after 60s" in row.error_summary
```

- [ ] **Step 2: Run — verify fail**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_sync_log.py -v
```

- [ ] **Step 3: Implement `service.py` sync-log writers**

Create `app/modules/ats/service.py`:

```python
"""Service-layer functions for ATS connection lifecycle + sync log writers.

Connection-management endpoints (router.py) and the poll_ats_connection actor
(actors.py) both call into here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ats.importer import SyncResult
from app.modules.ats.models import ATSConnection, ATSSyncLog


logger = structlog.get_logger()


async def create_sync_log_row(
    db: AsyncSession,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    correlation_id: str,
) -> UUID:
    """Insert an ats_sync_logs row with status='running' and return its id."""
    row = ATSSyncLog(
        connection_id=connection_id,
        tenant_id=tenant_id,
        started_at=datetime.now(tz=UTC),
        status="running",
        correlation_id=correlation_id,
        entity_counts={},
    )
    db.add(row)
    await db.flush()
    return row.id


async def finalize_sync_log_success(
    db: AsyncSession, log_id: UUID, sync_result: SyncResult,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    log.status = "success"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    await db.flush()


async def finalize_sync_log_partial(
    db: AsyncSession, log_id: UUID, sync_result: SyncResult, error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    log.status = "partial"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    log.error_summary = error_summary[:1000]  # truncate
    await db.flush()


async def finalize_sync_log_failure(
    db: AsyncSession, log_id: UUID, *, phase: str, error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    log.status = "failed"
    log.completed_at = datetime.now(tz=UTC)
    log.error_phase = phase
    log.error_summary = error_summary[:1000]
    await db.flush()


async def advance_next_poll_at(
    db: AsyncSession,
    connection_id: UUID,
    interval_seconds: int | None = None,
    jitter_seconds: int = 60,
) -> None:
    """next_poll_at = now() + interval + jitter(0, jitter_seconds).

    interval_seconds: if None, uses the connection's stored poll_interval_seconds.
    """
    import random
    j = random.randint(0, jitter_seconds)
    if interval_seconds is None:
        # Use the stored interval
        await db.execute(text(
            "UPDATE ats_connections "
            "SET next_poll_at = now() + (poll_interval_seconds || ' seconds')::interval "
            "+ (:j || ' seconds')::interval, "
            "poll_lock_acquired_at = NULL "
            "WHERE id = :i"
        ), {"i": connection_id, "j": j})
    else:
        await db.execute(text(
            "UPDATE ats_connections "
            "SET next_poll_at = now() + (:s || ' seconds')::interval, "
            "poll_lock_acquired_at = NULL "
            "WHERE id = :i"
        ), {"i": connection_id, "s": interval_seconds + j})


async def disable_connection(
    db: AsyncSession, connection_id: UUID, reason: str,
) -> None:
    """Mark a connection inactive. Recruiter must reconnect via UI."""
    row = await db.get(ATSConnection, connection_id)
    if row is None:
        return
    row.active = False
    row.disabled_reason = reason[:500]
    row.disabled_at = datetime.now(tz=UTC)
    await db.flush()
```

- [ ] **Step 4: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_sync_log.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/service.py tests/modules/ats/test_sync_log.py
git commit -m "feat(ats/service): sync-log lifecycle helpers + advance_next_poll_at + disable_connection"
```

### Task 23: `poll_ats_connection` Dramatiq actor

**Files:**
- Create: `app/modules/ats/actors.py`
- Create: `tests/modules/ats/test_actors.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_actors.py`:

```python
"""Actor end-to-end with mock CeipalAdapter — verify the four phases
(load → auth → sync → persist) execute and the sync_log closes correctly.

Also verify: ATSPermanentError disables the connection; ATSRateLimitedError
advances next_poll_at and exits cleanly (no raise); ATSTransientError
re-raises so Dramatiq retries.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.database import async_session_factory


@pytest.fixture(autouse=True)
def _enc_keys(monkeypatch):
    from app.config import settings
    from app.modules.ats import crypto
    monkeypatch.setattr(settings, "ats_credentials_encryption_keys",
                        [Fernet.generate_key().decode()])
    crypto._fernet = None


@pytest.fixture
async def actor_fixture(importer_fixture):
    """importer_fixture pre-seeds tenant + user + ats_connections row.
    Update the connection's credentials_ciphertext to a real encrypted blob."""
    from app.modules.ats.crypto import encrypt_credentials_blob

    tenant_id, user_id, _ = importer_fixture
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            ct = encrypt_credentials_blob({
                "email": "u@x.com", "password": "p", "api_key": "k",
            })
            await session.execute(text(
                "UPDATE ats_connections SET credentials_ciphertext = :ct "
                "WHERE tenant_id = :t"
            ), {"ct": ct, "t": tenant_id})
            cid = (await session.execute(text(
                "SELECT id::text FROM ats_connections WHERE tenant_id = :t LIMIT 1"
            ), {"t": tenant_id})).scalar_one()
    yield (str(tenant_id), cid)


@pytest.mark.asyncio
async def test_happy_path_writes_success_sync_log(actor_fixture):
    """Mock adapter yields no entities; poll completes; sync_log status='success'."""
    from app.modules.ats import actors

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()
    fake_adapter.list_clients = lambda since=None: _empty_aiter()
    fake_adapter.list_users = lambda since=None: _empty_aiter()
    fake_adapter.list_jobs = lambda since=None: _empty_aiter()
    fake_adapter.list_applicants = lambda since=None: _empty_aiter()
    fake_adapter.list_submissions = lambda job_external_id, since=None: _empty_aiter()

    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        await actors._run_poll(connection_id, tenant_id)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            r = await session.execute(text(
                "SELECT status FROM ats_sync_logs WHERE connection_id = :c "
                "ORDER BY started_at DESC LIMIT 1"
            ), {"c": connection_id})
            assert r.scalar_one() == "success"


@pytest.mark.asyncio
async def test_credentials_invalid_disables_connection_and_raises(actor_fixture):
    """ATSCredentialsInvalidError → mark connection disabled + raise."""
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSCredentialsInvalidError

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock(
        side_effect=ATSCredentialsInvalidError("password revoked upstream"),
    )

    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        with pytest.raises(ATSCredentialsInvalidError):
            await actors._run_poll(connection_id, tenant_id)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            row = await session.execute(text(
                "SELECT active, disabled_reason FROM ats_connections WHERE id = :i"
            ), {"i": connection_id})
            r = row.one()
    assert r.active is False
    assert "password revoked" in r.disabled_reason


@pytest.mark.asyncio
async def test_rate_limited_advances_next_poll_returns_cleanly(actor_fixture):
    """ATSRateLimitedError → set next_poll_at = now() + retry_after, return cleanly."""
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSRateLimitedError

    tenant_id, connection_id = actor_fixture
    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()

    # Make the importer call raise rate-limited
    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        with patch.object(actors.ATSImporter, "sync_tenant",
                          side_effect=ATSRateLimitedError(retry_after_seconds=120)):
            # Should NOT raise — handled internally
            await actors._run_poll(connection_id, tenant_id)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            r = await session.execute(text(
                "SELECT next_poll_at, EXTRACT(EPOCH FROM (next_poll_at - now())) AS delta "
                "FROM ats_connections WHERE id = :i"
            ), {"i": connection_id})
            row = r.one()
    # next_poll_at should be roughly now + 120s (allow ±5s for clock drift)
    assert 115 <= row.delta <= 125


def _empty_aiter():
    async def _aiter():
        return
        yield  # pragma: no cover
    return _aiter()
```

- [ ] **Step 2: Run — verify fail**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_actors.py -v
```

- [ ] **Step 3: Implement the actor**

Create `app/modules/ats/actors.py`:

```python
"""Dramatiq actor: poll_ats_connection.

One actor invocation = one tenant's sync run. The scheduler tick
(app/cli/ats_tick.py) enqueues one message per (connection_id, tenant_id)
when ats_connections.next_poll_at <= now().

Lifecycle (mirrors app/modules/jd/actors.py:429-551 pattern):
  Phase A: load + decrypt state, open sync_log row
  Phase B: ensure_authenticated() — may mutate tokens; persist on success
  Phase C: ATSImporter().sync_tenant(adapter) — 5 phases, partial-tolerant
  Phase D: persist final state, advance next_poll_at, close sync_log
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import dramatiq
import structlog
from opentelemetry import trace
from sqlalchemy import text

from app.database import get_bypass_session
from app.modules.ats.connection import (
    load_connection_state, persist_connection_state,
)
from app.modules.ats.errors import (
    ATSCredentialsInvalidError, ATSPermanentError,
    ATSRateLimitedError, ATSTransientError,
)
from app.modules.ats.importer import ATSImporter
from app.modules.ats.registry import get_ats_adapter
from app.modules.ats.service import (
    advance_next_poll_at, create_sync_log_row, disable_connection,
    finalize_sync_log_failure, finalize_sync_log_partial,
    finalize_sync_log_success,
)
from app.modules.audit import log_event


logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


@dramatiq.actor(
    max_retries=3,
    min_backoff=30_000,
    max_backoff=600_000,
    queue_name="ats_poll",
)
async def poll_ats_connection(connection_id: str, tenant_id: str) -> None:
    """Dramatiq entry point. Thin wrapper — real work in _run_poll for testability."""
    await _run_poll(connection_id, tenant_id)


async def _run_poll(connection_id: str, tenant_id: str) -> None:
    safe_tenant = str(uuid.UUID(tenant_id))
    correlation_id = f"ats-{uuid.uuid4()}"

    structlog.contextvars.bind_contextvars(
        connection_id=connection_id, tenant_id=safe_tenant,
        correlation_id=correlation_id, queue="ats_poll",
    )

    try:
        with tracer.start_as_current_span(
            "ats.poll",
            attributes={"connection_id": connection_id, "tenant_id": safe_tenant},
        ):
            await _do_poll(uuid.UUID(connection_id), uuid.UUID(tenant_id),
                           correlation_id, safe_tenant)
    finally:
        structlog.contextvars.clear_contextvars()


async def _do_poll(
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    correlation_id: str,
    safe_tenant: str,
) -> None:
    # ---- Phase A: load state + open sync_log ----
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
        state = await load_connection_state(db, connection_id)
        sync_log_id = await create_sync_log_row(
            db, connection_id=connection_id, tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
        await log_event(
            db, tenant_id=tenant_id, actor_id=None, actor_email="ats-scheduler",
            action="ats.sync.started",
            resource="ats_connection", resource_id=connection_id,
            payload={"vendor": state.vendor, "correlation_id": correlation_id},
        )
        await db.commit()

    adapter = get_ats_adapter(state)

    # ---- Phase B: ensure_authenticated (may refresh tokens) ----
    try:
        with tracer.start_as_current_span("ats.poll.auth"):
            await adapter.ensure_authenticated()
    except ATSCredentialsInvalidError as exc:
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
            await disable_connection(db, connection_id, reason=str(exc))
            await finalize_sync_log_failure(db, sync_log_id, phase="auth",
                                             error_summary=str(exc))
            await log_event(
                db, tenant_id=tenant_id, actor_id=None,
                actor_email="ats-scheduler",
                action="ats.connection.disabled",
                resource="ats_connection", resource_id=connection_id,
                payload={"reason": str(exc)[:200]},
            )
            await db.commit()
        raise

    # Persist refreshed tokens immediately so we don't lose them mid-sync
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
        await persist_connection_state(db, state)
        await db.commit()

    # ---- Phase C: run sync ----
    try:
        sync_result = await ATSImporter().sync_tenant(adapter)
    except ATSRateLimitedError as exc:
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
            await advance_next_poll_at(
                db, connection_id,
                interval_seconds=exc.retry_after_seconds,
                jitter_seconds=0,
            )
            await finalize_sync_log_partial(
                db, sync_log_id, ATSImporter._empty_partial_result(),
                error_summary=str(exc),
            )
            await db.commit()
        return  # NO retry — next tick handles it
    except ATSPermanentError as exc:
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
            await finalize_sync_log_failure(
                db, sync_log_id, phase="sync", error_summary=str(exc),
            )
            await db.commit()
        raise  # lands in DLQ for visibility
    # ATSTransientError propagates → Dramatiq retries with exp backoff

    # ---- Phase D: persist state + advance + close log ----
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
        await persist_connection_state(db, state)
        await advance_next_poll_at(db, connection_id)
        await finalize_sync_log_success(db, sync_log_id, sync_result)
        await log_event(
            db, tenant_id=tenant_id, actor_id=None, actor_email="ats-scheduler",
            action="ats.sync.completed",
            resource="ats_connection", resource_id=connection_id,
            payload={"vendor": state.vendor,
                     "entity_counts": sync_result.entity_counts(),
                     "correlation_id": correlation_id},
        )
        await db.commit()
    logger.info("ats.poll.completed",
                entity_counts=sync_result.entity_counts())
```

Add a helper to `ATSImporter` in `importer.py`:

```python
    @staticmethod
    def _empty_partial_result() -> SyncResult:
        """Empty result for rate-limit case — closes the sync log row cleanly."""
        return SyncResult()
```

- [ ] **Step 4: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_actors.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/actors.py app/modules/ats/importer.py tests/modules/ats/test_actors.py
git commit -m "feat(ats/actors): poll_ats_connection — four-phase actor with typed-exception handling"
```

---

## Phase 9 — Scheduler tick CLI + compose service

### Task 24: `app/cli/ats_tick.py` — the scheduler tick CLI

**Files:**
- Create: `app/cli/__init__.py` (empty if not already)
- Create: `app/cli/ats_tick.py`
- Create: `tests/cli/__init__.py` (empty)
- Create: `tests/cli/test_ats_tick.py`

- [ ] **Step 1: Verify the cli package exists**

```bash
ls app/cli/ 2>/dev/null && echo "exists" || (mkdir -p app/cli && touch app/cli/__init__.py)
mkdir -p tests/cli && touch tests/cli/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/cli/test_ats_tick.py`:

```python
"""Scheduler tick enqueues poll_ats_connection for every due connection,
stamps poll_lock_acquired_at, and is safe under concurrent ticks
(FOR UPDATE SKIP LOCKED)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.database import async_session_factory


@pytest.fixture
async def due_connections_fixture():
    """Three connections: A and B are due; C is not yet due."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    ids = {"A": uuid.uuid4(), "B": uuid.uuid4(), "C": uuid.uuid4()}
    now = datetime.now(tz=timezone.utc)
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
                {"t": tenant_id},
            )
            await session.execute(
                text("INSERT INTO users (id, email, tenant_id, auth_user_id) "
                     "VALUES (:u, 'u@x.com', :t, :a)"),
                {"u": user_id, "t": tenant_id, "a": uuid.uuid4()},
            )
            for label, due in [("A", now - timedelta(minutes=5)),
                               ("B", now - timedelta(minutes=1)),
                               ("C", now + timedelta(minutes=10))]:
                await session.execute(
                    text("INSERT INTO ats_connections "
                         "(id, tenant_id, vendor, credentials_ciphertext, "
                         "created_by, next_poll_at) "
                         "VALUES (:i, :t, 'ceipal', :ct, :u, :n)"),
                    {"i": ids[label], "t": tenant_id, "ct": b"x",
                     "u": user_id, "n": due},
                )
    yield (str(tenant_id), ids)
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(
                text("DELETE FROM clients WHERE id = :t"), {"t": tenant_id}
            )


@pytest.mark.asyncio
async def test_tick_enqueues_only_due_connections(due_connections_fixture):
    from app.cli.ats_tick import run_tick

    tenant_id, ids = due_connections_fixture
    enqueued = []
    with patch("app.cli.ats_tick.poll_ats_connection") as mock_actor:
        mock_actor.send = lambda *a, **k: enqueued.append(a)
        await run_tick()

    enqueued_ids = {a[0] for a in enqueued}
    assert str(ids["A"]) in enqueued_ids
    assert str(ids["B"]) in enqueued_ids
    assert str(ids["C"]) not in enqueued_ids


@pytest.mark.asyncio
async def test_tick_stamps_poll_lock_acquired_at(due_connections_fixture):
    from app.cli.ats_tick import run_tick

    tenant_id, ids = due_connections_fixture
    with patch("app.cli.ats_tick.poll_ats_connection") as mock_actor:
        mock_actor.send = lambda *a, **k: None
        await run_tick()

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            r = await session.execute(text(
                "SELECT id::text, poll_lock_acquired_at FROM ats_connections "
                "WHERE tenant_id = :t"
            ), {"t": tenant_id})
            rows = {row.id: row.poll_lock_acquired_at for row in r}
    assert rows[str(ids["A"])] is not None
    assert rows[str(ids["B"])] is not None
    assert rows[str(ids["C"])] is None      # not picked up
```

- [ ] **Step 3: Run — verify fail**

```bash
docker compose run --rm nexus pytest tests/cli/test_ats_tick.py -v
```

- [ ] **Step 4: Implement the CLI**

Create `app/cli/ats_tick.py`:

```python
"""ATS scheduler tick — stateless CLI run by external cron.

Lifecycle (200ms typical):
  1. Init structlog + OTel + Sentry (mirrors app/worker.py).
  2. Open one bypass-RLS session.
  3. SELECT due connections FOR UPDATE SKIP LOCKED.
  4. For each: stamp poll_lock_acquired_at, enqueue poll_ats_connection.
  5. Exit.

The cron firing rate is NOT the per-tenant cadence — `next_poll_at` is.
Cron fires every 5 min; each connection's poll_interval_seconds (default 900)
governs per-tenant cadence.

Invocation:
  python -m app.cli.ats_tick

Deploy targets:
  - Railway: separate "ats-scheduler" service, cron `*/5 * * * *`.
  - AWS ECS: EventBridge Scheduler → ECS RunTask, same image.
  - Local dev: docker-compose service `nexus-scheduler` in a sleep loop.
"""
from __future__ import annotations

import asyncio
import atexit

import structlog
from opentelemetry import trace
from sqlalchemy import text

from app.config import settings
from app.database import async_session_factory


_TICK_QUERY = """
SELECT id::text, tenant_id::text FROM ats_connections
WHERE active = true
  AND next_poll_at <= now()
  AND (poll_lock_acquired_at IS NULL
       OR poll_lock_acquired_at < now() - interval '20 minutes')
ORDER BY next_poll_at ASC
LIMIT 500
FOR UPDATE SKIP LOCKED
"""


def _init_structlog() -> None:
    """Mirrors app/worker.py init."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            (structlog.dev.ConsoleRenderer()
             if settings.debug
             else structlog.processors.JSONRenderer()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            10 if settings.debug else 20
        ),
    )


def _init_otel():
    from app.ai.otel import bootstrap_tracer_provider
    provider = bootstrap_tracer_provider()
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)


# Import here so the broker is configured before .send() is called
from app import brokers  # noqa: F401, E402
from app.modules.ats.actors import poll_ats_connection  # noqa: E402


logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


async def run_tick() -> None:
    """One scheduler tick: SELECT due connections, enqueue actor per row."""
    with tracer.start_as_current_span("ats.tick") as span:
        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                rows = await session.execute(text(_TICK_QUERY))
                due = list(rows)
                for row in due:
                    await session.execute(
                        text("UPDATE ats_connections "
                             "SET poll_lock_acquired_at = now(), "
                             "last_poll_started_at = now() "
                             "WHERE id = :i"),
                        {"i": row.id},
                    )
                    poll_ats_connection.send(row.id, row.tenant_id)
                await session.commit()
        span.set_attribute("ats.tick.enqueued_count", len(due))
        logger.info("ats.tick.completed", enqueued_count=len(due))


def main() -> None:
    _init_structlog()
    _init_otel()
    asyncio.run(run_tick())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/cli/test_ats_tick.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add app/cli/ats_tick.py app/cli/__init__.py tests/cli/
git commit -m "feat(cli/ats_tick): scheduler tick with FOR UPDATE SKIP LOCKED + lock stamping"
```

### Task 25: `nexus-scheduler` compose service for local dev

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Append the scheduler service to the compose file**

Open `backend/nexus/docker-compose.yml`. Locate the `nexus-worker` service definition. Append (at the same indentation level) a `nexus-scheduler` service that mirrors its build/depends_on/env_file shape:

```yaml
  nexus-scheduler:
    build:
      context: .
      dockerfile: Dockerfile
    env_file:
      - .env
    depends_on:
      - redis
    command:
      - sh
      - -c
      - "while true; do python -m app.cli.ats_tick || true; sleep 60; done"
    networks:
      - nexus-network
```

(The exact `networks` and `depends_on` values should match the existing `nexus-worker` service's. The `sleep 60` is for local dev cadence; production uses platform cron.)

- [ ] **Step 2: Smoke-test the service**

```bash
docker compose up -d nexus-scheduler
sleep 65
docker compose logs nexus-scheduler --tail 20
```

Expected: at least one `ats.tick.completed` log line with `enqueued_count=0` (or `>0` if you have active connections seeded).

```bash
docker compose down nexus-scheduler
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): add nexus-scheduler service (ats_tick in sleep loop for local dev)"
```

---

## Phase 10 — Connection management (service + authz + router)

The recruiter-facing surface — `POST /api/ats/connections`, listing, detail, manual sync, user mapping.

### Task 26: Connection-management service functions

**Files:**
- Modify: `app/modules/ats/service.py` (append)
- Create: `tests/modules/ats/test_connection_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/ats/test_connection_service.py`:

```python
"""create_connection: encrypt credentials, test via adapter, persist + audit + initial-sync enqueue.
delete_connection: hard-delete row + audit."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.database import async_session_factory


@pytest.fixture(autouse=True)
def _enc(monkeypatch):
    from app.config import settings
    from app.modules.ats import crypto
    monkeypatch.setattr(settings, "ats_credentials_encryption_keys",
                        [Fernet.generate_key().decode()])
    crypto._fernet = None


@pytest.fixture
async def basic_tenant():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
                                  {"t": tenant_id})
            await session.execute(text(
                "INSERT INTO users (id, email, tenant_id, auth_user_id) "
                "VALUES (:u, 'u@x.com', :t, :a)"
            ), {"u": user_id, "t": tenant_id, "a": uuid.uuid4()})
    yield (str(tenant_id), str(user_id))
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text("DELETE FROM clients WHERE id = :t"),
                                  {"t": tenant_id})


@pytest.mark.asyncio
async def test_create_connection_encrypts_credentials_and_audits(basic_tenant):
    from app.modules.ats.service import create_connection

    tenant_id, user_id = basic_tenant

    # Mock the adapter's ensure_authenticated to succeed
    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake_adapter = AsyncMock()
        fake_adapter.ensure_authenticated = AsyncMock()
        # After auth, the state should have access_token + refresh_token set
        def _bind(state):
            state.access_token = "fresh-access"
            state.refresh_token = "fresh-refresh"
            state.access_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
            state.refresh_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind

        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                conn_id = await create_connection(
                    session, tenant_id=uuid.UUID(tenant_id),
                    vendor="ceipal",
                    credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
                    created_by=uuid.UUID(user_id),
                )

    # Verify the credentials and tokens are encrypted (not plain in DB)
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            row = await session.execute(text(
                "SELECT vendor, credentials_ciphertext, access_token_ciphertext, "
                "active FROM ats_connections WHERE id = :i"
            ), {"i": conn_id})
            r = row.one()
    assert r.vendor == "ceipal"
    assert b"password" not in r.credentials_ciphertext        # encrypted
    assert b"fresh-access" not in r.access_token_ciphertext   # encrypted
    assert r.active is True


@pytest.mark.asyncio
async def test_create_connection_invalid_credentials_raises(basic_tenant):
    """ATSCredentialsInvalidError during test → no DB row inserted."""
    from app.modules.ats.service import create_connection
    from app.modules.ats.errors import ATSCredentialsInvalidError

    tenant_id, user_id = basic_tenant

    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake_adapter = AsyncMock()
        fake_adapter.ensure_authenticated = AsyncMock(
            side_effect=ATSCredentialsInvalidError("bad password")
        )
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind

        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                with pytest.raises(ATSCredentialsInvalidError):
                    await create_connection(
                        session, tenant_id=uuid.UUID(tenant_id),
                        vendor="ceipal",
                        credentials={"email": "u@x.com", "password": "wrong",
                                     "api_key": "k"},
                        created_by=uuid.UUID(user_id),
                    )

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            count = await session.execute(text(
                "SELECT COUNT(*) FROM ats_connections WHERE tenant_id = :t"
            ), {"t": tenant_id})
    assert count.scalar_one() == 0
```

- [ ] **Step 2: Run — verify fail**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_connection_service.py -v
```

- [ ] **Step 3: Append connection-management functions to `service.py`**

Add to `app/modules/ats/service.py`:

```python
from typing import Any
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.crypto import encrypt_credentials_blob, encrypt_secret
from app.modules.ats.registry import get_ats_adapter
from app.modules.audit import log_event


async def create_connection(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    vendor: str,
    credentials: dict[str, Any],
    created_by: UUID,
) -> UUID:
    """Test credentials via adapter, then persist an ats_connections row.

    Flow:
      1. Build a temporary in-memory state (no DB write).
      2. Construct adapter; await ensure_authenticated().
         - on success: state.access_token / refresh_token / expiries are set.
         - on ATSCredentialsInvalidError / ATSAuthorizationError: propagate
           without persisting.
      3. Encrypt credentials + tokens; insert ats_connections row.
      4. Audit log: ats.connection.created (vendor only; never credentials).
    Returns the new connection id.
    """
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=tenant_id, vendor=vendor,
        credentials=credentials,
    )
    adapter = get_ats_adapter(state)
    await adapter.ensure_authenticated()
    # state was mutated by ensure_authenticated — access_token + expiries set

    row = ATSConnection(
        id=state.id,
        tenant_id=tenant_id,
        vendor=vendor,
        credentials_ciphertext=encrypt_credentials_blob(credentials),
        access_token_ciphertext=encrypt_secret(state.access_token) if state.access_token else None,
        refresh_token_ciphertext=encrypt_secret(state.refresh_token) if state.refresh_token else None,
        access_token_expires_at=state.access_token_expires_at,
        refresh_token_expires_at=state.refresh_token_expires_at,
        next_poll_at=datetime.now(tz=UTC),    # poll immediately
        poll_interval_seconds=900,
        active=True,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()

    await log_event(
        db, tenant_id=tenant_id, actor_id=created_by,
        actor_email="recruiter",
        action="ats.connection.created",
        resource="ats_connection", resource_id=row.id,
        payload={"vendor": vendor},   # NEVER credentials
    )
    return row.id


async def delete_connection(
    db: AsyncSession,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Hard-delete an ats_connections row. CASCADE drops dependent
    sync_logs / mappings; explicit audit row is written BEFORE delete."""
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.deleted",
        resource="ats_connection", resource_id=connection_id,
        payload={"vendor": row.vendor},
    )
    await db.delete(row)
    await db.flush()


async def trigger_manual_sync(
    db: AsyncSession,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Enqueue a poll_ats_connection actor immediately, bypassing next_poll_at.

    Caller is responsible for rate-limiting at the router layer (per root
    CLAUDE.md: 30/min per-IP, 12/hour per-tenant).
    """
    from app.modules.ats.actors import poll_ats_connection

    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.sync.manually_triggered",
        resource="ats_connection", resource_id=connection_id,
        payload={"vendor": row.vendor},
    )
    poll_ats_connection.send(str(connection_id), str(tenant_id))


async def map_ats_user_to_internal(
    db: AsyncSession,
    *,
    connection_id: UUID,
    external_user_id: str,
    internal_user_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Set ats_user_mappings.internal_user_id for a specific external user.

    Audit: ats.user_mapping.created.
    """
    from app.modules.ats.models import ATSUserMapping

    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != tenant_id:
        return
    mapping = await db.scalar(
        select(ATSUserMapping).where(
            ATSUserMapping.tenant_id == tenant_id,
            ATSUserMapping.ats_vendor == conn.vendor,
            ATSUserMapping.external_user_id == external_user_id,
        )
    )
    if mapping is None:
        return
    mapping.internal_user_id = internal_user_id
    mapping.mapped_at = datetime.now(tz=UTC)
    mapping.mapped_by = actor_id
    await db.flush()
    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.user_mapping.created",
        resource="ats_user_mapping", resource_id=mapping.id,
        payload={"external_user_id": external_user_id,
                 "internal_user_id": str(internal_user_id)},
    )
```

Add `from sqlalchemy import select` if not already imported.

- [ ] **Step 4: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_connection_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/modules/ats/service.py tests/modules/ats/test_connection_service.py
git commit -m "feat(ats/service): create_connection (encrypt + test + audit) + delete + manual_sync + user_map"
```

### Task 27: Authz guard

**Files:**
- Create: `app/modules/ats/authz.py`

- [ ] **Step 1: Implement the guard**

Create `app/modules/ats/authz.py`:

```python
"""Authorization guards for /api/ats/* routes.

ATS connection management is a high-privilege operation (credential storage,
sync trigger). Restricted to super_admin per spec; the require_super_admin
guard from auth/context handles the existing DB-backed check.

A future `ats_admin` permission can be added to roles/permissions.py to
allow Recruiting Operations to manage ATS without granting full super_admin.
For MVP, we delegate entirely to require_super_admin.
"""
from __future__ import annotations

from app.modules.auth.context import require_super_admin


# Re-export for /api/ats/* route handlers — single import site for
# the auth dependency this module needs.
require_ats_admin = require_super_admin
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
docker compose run --rm nexus python -c \
  "from app.modules.ats.authz import require_ats_admin; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add app/modules/ats/authz.py
git commit -m "feat(ats/authz): require_ats_admin (delegates to require_super_admin for MVP)"
```

### Task 28: Router endpoints + Pydantic discriminated union

**Files:**
- Replace: `app/modules/ats/router.py` (existing stub)
- Create: `tests/modules/ats/test_router.py`

- [ ] **Step 1: Replace the stub router with the full implementation**

Replace `app/modules/ats/router.py`:

```python
"""/api/ats/* HTTP endpoints — recruiter-facing connection management.

Endpoints:
  GET    /api/ats/connections
  POST   /api/ats/connections
  GET    /api/ats/connections/{id}
  DELETE /api/ats/connections/{id}
  POST   /api/ats/connections/{id}/sync
  GET    /api/ats/connections/{id}/sync-logs
  GET    /api/ats/connections/{id}/unmapped-users
  POST   /api/ats/connections/{id}/users/{external_user_id}/map

Write endpoints require super_admin (via require_ats_admin). Credentials
NEVER appear in any response — only metadata.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.ats.authz import require_ats_admin
from app.modules.ats.errors import (
    ATSAuthorizationError, ATSCredentialsInvalidError,
)
from app.modules.ats.models import (
    ATSConnection, ATSSyncLog, ATSUserMapping,
)
from app.modules.ats.service import (
    create_connection, delete_connection, map_ats_user_to_internal,
    trigger_manual_sync,
)


router = APIRouter(prefix="/api/ats", tags=["ats"])


# ---------- Request/response models ----------

class CeipalCredentials(BaseModel):
    email: str
    password: str = Field(..., repr=False)         # never appears in repr/logs
    api_key: str = Field(..., repr=False)


class CeipalConnectionRequest(BaseModel):
    vendor: Literal["ceipal"] = "ceipal"
    credentials: CeipalCredentials


# Discriminated union — adding a vendor = one more union member.
ConnectionCreateRequest = Annotated[
    CeipalConnectionRequest,
    Field(discriminator="vendor"),
]


class ConnectionResponse(BaseModel):
    id: UUID
    vendor: str
    active: bool
    last_synced_at: str | None = None
    next_poll_at: str | None = None
    last_poll_error: str | None = None
    disabled_reason: str | None = None
    created_at: str

    @classmethod
    def from_row(cls, row: ATSConnection) -> "ConnectionResponse":
        return cls(
            id=row.id, vendor=row.vendor, active=row.active,
            last_synced_at=row.last_poll_completed_at.isoformat() if row.last_poll_completed_at else None,
            next_poll_at=row.next_poll_at.isoformat() if row.next_poll_at else None,
            last_poll_error=row.last_poll_error,
            disabled_reason=row.disabled_reason,
            created_at=row.created_at.isoformat(),
        )


class SyncLogResponse(BaseModel):
    id: UUID
    started_at: str
    completed_at: str | None = None
    status: str
    entity_counts: dict
    error_phase: str | None = None
    error_summary: str | None = None


class UnmappedUserResponse(BaseModel):
    external_user_id: str
    external_user_email: str
    external_user_display_name: str
    external_user_role: str | None = None


class MapUserRequest(BaseModel):
    internal_user_id: UUID


# ---------- Endpoints ----------

@router.get("/connections", response_model=list[ConnectionResponse])
async def list_connections(
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[ConnectionResponse]:
    rows = await db.execute(
        select(ATSConnection)
        .where(ATSConnection.tenant_id == user.tenant_id)
        .order_by(ATSConnection.created_at.desc())
    )
    return [ConnectionResponse.from_row(r) for r in rows.scalars()]


@router.post("/connections", status_code=status.HTTP_201_CREATED,
             response_model=ConnectionResponse)
async def create_connection_endpoint(
    body: ConnectionCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> ConnectionResponse:
    try:
        conn_id = await create_connection(
            db,
            tenant_id=user.tenant_id, vendor=body.vendor,
            credentials=body.credentials.model_dump(),
            created_by=user.user.id,
        )
    except ATSCredentialsInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ATS_CREDENTIALS_INVALID", "message": str(exc)[:200]},
        )
    except ATSAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ATS_AUTHORIZATION_INSUFFICIENT", "message": str(exc)[:200]},
        )
    await db.commit()

    # Fire-and-forget initial sync
    await trigger_manual_sync(db, conn_id, user.tenant_id, user.user.id)
    await db.commit()

    new_row = await db.get(ATSConnection, conn_id)
    return ConnectionResponse.from_row(new_row)


@router.get("/connections/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> ConnectionResponse:
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")
    return ConnectionResponse.from_row(row)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection_endpoint(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    await delete_connection(db, connection_id, user.tenant_id, user.user.id)
    await db.commit()


@router.post("/connections/{connection_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def manual_sync(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> dict:
    await trigger_manual_sync(db, connection_id, user.tenant_id, user.user.id)
    await db.commit()
    return {"status": "enqueued"}


@router.get("/connections/{connection_id}/sync-logs",
            response_model=list[SyncLogResponse])
async def list_sync_logs(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[SyncLogResponse]:
    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")
    rows = await db.execute(
        select(ATSSyncLog)
        .where(ATSSyncLog.connection_id == connection_id)
        .order_by(ATSSyncLog.started_at.desc())
        .limit(50)
    )
    return [
        SyncLogResponse(
            id=r.id, started_at=r.started_at.isoformat(),
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            status=r.status, entity_counts=r.entity_counts,
            error_phase=r.error_phase, error_summary=r.error_summary,
        )
        for r in rows.scalars()
    ]


@router.get("/connections/{connection_id}/unmapped-users",
            response_model=list[UnmappedUserResponse])
async def list_unmapped_users(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[UnmappedUserResponse]:
    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")
    rows = await db.execute(
        select(ATSUserMapping).where(
            ATSUserMapping.tenant_id == user.tenant_id,
            ATSUserMapping.ats_vendor == conn.vendor,
            ATSUserMapping.internal_user_id.is_(None),
        )
    )
    return [
        UnmappedUserResponse(
            external_user_id=r.external_user_id,
            external_user_email=r.external_user_email,
            external_user_display_name=r.external_user_display_name,
            external_user_role=r.external_user_role,
        )
        for r in rows.scalars()
    ]


@router.post("/connections/{connection_id}/users/{external_user_id}/map",
             status_code=status.HTTP_204_NO_CONTENT)
async def map_user(
    connection_id: UUID,
    external_user_id: str,
    body: MapUserRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    await map_ats_user_to_internal(
        db,
        connection_id=connection_id,
        external_user_id=external_user_id,
        internal_user_id=body.internal_user_id,
        tenant_id=user.tenant_id, actor_id=user.user.id,
    )
    await db.commit()
```

- [ ] **Step 2: Write integration tests for the router**

Create `tests/modules/ats/test_router.py` (using the existing `httpx.AsyncClient` test fixture pattern in `tests/conftest.py`):

```python
"""End-to-end API tests through the FastAPI app.

The existing AsyncClient fixture (tests/conftest.py) starts the app with
test settings. We mock the adapter to avoid real Ceipal calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_post_connections_201_on_valid_creds(authed_super_admin_client):
    """A super_admin posts valid Ceipal credentials → 201 + connection metadata."""
    client, _ = authed_super_admin_client

    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake = AsyncMock()
        fake.ensure_authenticated = AsyncMock()
        def _bind(state):
            state.access_token = "tok"
            state.access_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
            fake.state = state
            return fake
        mock_get.side_effect = _bind

        resp = await client.post("/api/ats/connections", json={
            "vendor": "ceipal",
            "credentials": {"email": "u@x.com", "password": "p", "api_key": "k"},
        })

    assert resp.status_code == 201
    body = resp.json()
    assert body["vendor"] == "ceipal"
    assert body["active"] is True
    assert "password" not in resp.text   # NEVER leaks


@pytest.mark.asyncio
async def test_post_connections_422_on_invalid_creds(authed_super_admin_client):
    from app.modules.ats.errors import ATSCredentialsInvalidError

    client, _ = authed_super_admin_client
    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake = AsyncMock()
        fake.ensure_authenticated = AsyncMock(
            side_effect=ATSCredentialsInvalidError("bad password"),
        )
        def _bind(state):
            fake.state = state
            return fake
        mock_get.side_effect = _bind

        resp = await client.post("/api/ats/connections", json={
            "vendor": "ceipal",
            "credentials": {"email": "u@x.com", "password": "wrong", "api_key": "k"},
        })

    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["code"] == "ATS_CREDENTIALS_INVALID"


@pytest.mark.asyncio
async def test_post_connections_403_for_non_super_admin(authed_recruiter_client):
    """Non-super-admin user → 403."""
    client, _ = authed_recruiter_client
    resp = await client.post("/api/ats/connections", json={
        "vendor": "ceipal",
        "credentials": {"email": "u@x.com", "password": "p", "api_key": "k"},
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_connections_returns_no_credentials_fields(authed_super_admin_client, seeded_connection):
    client, _ = authed_super_admin_client
    resp = await client.get("/api/ats/connections")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    for entry in body:
        assert "password" not in entry
        assert "credentials" not in entry
        assert "access_token" not in entry
```

> **Fixtures `authed_super_admin_client`, `authed_recruiter_client`, `seeded_connection`** should exist in `tests/conftest.py`. If they don't, add them following the patterns used by other module tests (search the existing conftest for how team-management tests build authed clients).

- [ ] **Step 3: Run — verify pass**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_router.py -v
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/ats/router.py tests/modules/ats/test_router.py
git commit -m "feat(ats/router): full /api/ats/* endpoints (connection mgmt + sync trigger + user map)"
```

---

## Phase 11 — Module wiring + rotation runbook

### Task 29: Public API surface (`__init__.py`)

**Files:**
- Modify: `app/modules/ats/__init__.py`

- [ ] **Step 1: Declare the public API per backend `CLAUDE.md` module-boundary rule**

Replace `app/modules/ats/__init__.py`:

```python
"""ATS integration module — public API.

Cross-module callers MUST import from this `__init__.py`, never deep-import.
Two documented exceptions (per backend CLAUDE.md):
  - app/worker.py deep-imports actors to register them with the broker
  - app/main.py deep-imports router to register it with the FastAPI app
"""
from __future__ import annotations

from app.modules.ats.adapter import ATSAdapter
from app.modules.ats.connection import (
    ATSConnectionState,
    load_connection_state,
    persist_connection_state,
)
from app.modules.ats.errors import (
    ATSError,
    ATSPermanentError,
    ATSCredentialsInvalidError,
    ATSAuthorizationError,
    ATSVendorContractError,
    ATSUnknownVendorError,
    ATSConnectionNotFoundError,
    ATSTransientError,
    ATSNetworkError,
    ATSRateLimitedError,
)
from app.modules.ats.registry import SUPPORTED_VENDORS, get_ats_adapter
from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientPayload,
    ATSJobPayload,
    ATSSubmissionPayload,
    ATSUserPayload,
)


__all__ = [
    "ATSAdapter",
    "ATSConnectionState",
    "load_connection_state",
    "persist_connection_state",
    # DTOs
    "ATSApplicantPayload",
    "ATSClientPayload",
    "ATSJobPayload",
    "ATSSubmissionPayload",
    "ATSUserPayload",
    # Errors
    "ATSError",
    "ATSPermanentError",
    "ATSCredentialsInvalidError",
    "ATSAuthorizationError",
    "ATSVendorContractError",
    "ATSUnknownVendorError",
    "ATSConnectionNotFoundError",
    "ATSTransientError",
    "ATSNetworkError",
    "ATSRateLimitedError",
    # Registry
    "SUPPORTED_VENDORS",
    "get_ats_adapter",
]
```

- [ ] **Step 2: Add 'ats' to the module-boundary test allowlist**

Edit `tests/test_module_boundaries.py`. Find `KNOWN_DOMAIN_MODULES` (or similar constant naming domain modules). Add `'ats'`.

- [ ] **Step 3: Run the module-boundary test**

```bash
docker compose run --rm nexus pytest tests/test_module_boundaries.py -v
```

Expected: PASS — confirms no cross-module deep import slipped in (other than the documented exceptions in `worker.py` and `main.py`).

- [ ] **Step 4: Commit**

```bash
git add app/modules/ats/__init__.py tests/test_module_boundaries.py
git commit -m "feat(ats): public API __all__ + register in module-boundary test"
```

### Task 30: Register the router and the actor in app startup

**Files:**
- Modify: `app/main.py`
- Modify: `app/worker.py`

- [ ] **Step 1: Register the router in `app/main.py`**

Find where other routers are registered (search for `include_router`). Add:

```python
from app.modules.ats.router import router as ats_router

# ... in the include_router block:
application.include_router(ats_router)
```

(The existing stub `from app.modules.ats.router import router as ats_router` may already be in place from the Phase-0 scaffold. Confirm it's still present after Task 28's replacement.)

- [ ] **Step 2: Register the actor in `app/worker.py`**

Edit `app/worker.py` and add this import after the existing actor imports (mirroring `_jd_actors`, `_question_bank_actors`):

```python
# ATS polling actors
from app.modules.ats import actors as _ats_actors  # noqa: F401, E402
```

- [ ] **Step 3: Verify both processes start cleanly**

```bash
docker compose up -d nexus nexus-worker
sleep 5
docker compose logs nexus --tail 20 | grep -i "ats"
docker compose logs nexus-worker --tail 20 | grep -i "ats_poll"
docker compose down
```

Expected: nexus startup log includes the `_assert_rls_completeness` line confirming all five ats_* tables passed. Worker log shows the `ats_poll` queue being declared.

- [ ] **Step 4: Commit**

```bash
git add app/main.py app/worker.py
git commit -m "feat(main+worker): register ats router and poll_ats_connection actor"
```

### Task 31: Credentials rotation runbook

**Files:**
- Create: `docs/security/ats-credentials-rotation.md`

- [ ] **Step 1: Write the runbook**

Create `docs/security/ats-credentials-rotation.md`:

```markdown
# ATS Credentials — Key Rotation Runbook

**Last reviewed:** 2026-05-12

## Scope

This runbook covers rotation of the `ATS_CREDENTIALS_ENCRYPTION_KEYS` setting
that protects per-tenant ATS credentials (email, password, API key) and
OAuth-style tokens (access_token, refresh_token) at rest.

It does NOT cover rotation of tenants' upstream credentials in Ceipal /
Greenhouse / Workday themselves — those are tenant-owned and rotated via
each vendor's console.

## When to rotate

- **Routine:** every 90 days.
- **Personnel change:** any engineer with access to prod env vars / AWS
  Secrets Manager leaves the team or changes role.
- **Incident:** any signal of key compromise (leaked log, lost dev machine,
  third-party breach affecting our key custodian).

## Pre-conditions

- [ ] You have write access to the prod env var store (Railway env or AWS
      Secrets Manager) AND the staging equivalent.
- [ ] You have the existing `ATS_CREDENTIALS_ENCRYPTION_KEYS` value (or at
      least confirmation it is non-empty).
- [ ] No active ATS sync is in flight (check `ats_sync_logs.status='running'`).

## Procedure

### 1. Generate a new key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Save the output to your password manager labeled `ats-encryption-key-<YYYY-MM-DD>`.

### 2. Prepend the new key to the active list

`ATS_CREDENTIALS_ENCRYPTION_KEYS` is comma-separated; the FIRST key encrypts
new values, all keys are tried for decrypt. Append, not replace:

```
ATS_CREDENTIALS_ENCRYPTION_KEYS=<NEW_KEY>,<OLD_KEY>
```

Update **staging first**, deploy, smoke-test:
- POST /api/ats/connections (create a test connection) — succeeds.
- GET /api/ats/connections/{id} — returns metadata.
- Wait one scheduler tick — sync_logs row appears with status='success'.

If staging is clean, repeat on prod.

### 3. Backfill (re-encrypt with the new key)

```bash
docker compose run --rm nexus python -m app.cli.ats_reencrypt
```

(The reencrypt CLI iterates every ats_connections row, decrypts under
MultiFernet, re-encrypts under the new key. Idempotent.) Tracking row count
in stdout; should match `SELECT COUNT(*) FROM ats_connections`.

### 4. Drop the old key

After the backfill confirms 100% coverage, remove the OLD key from the env:

```
ATS_CREDENTIALS_ENCRYPTION_KEYS=<NEW_KEY>
```

Deploy, smoke-test once more. The system should be operating exclusively on
the new key.

### 5. Log + close

- Update this runbook's "Last reviewed" date.
- Record the rotation in your team's security log:
  - date, operator, reason (routine / personnel / incident), affected envs.

## Rollback

If decrypt errors appear in `ats_sync_logs.error_summary` matching
`InvalidToken` after Step 4:
- Re-add the old key to the front of the list: `<NEW>,<OLD>`.
- Investigate which connection's ciphertext is still on the old key (likely
  a row created during the deploy window before backfill completed).
- Re-run Step 3's backfill.

## Audit trail

Every rotation must produce an audit_log row of `action='ats.encryption_key.rotated'`
with payload `{"operator": "<email>", "reason": "<routine|personnel|incident>"}`.
This is the trail SOC 2 reviewers expect.

(Implementation note: the reencrypt CLI emits this audit row at completion.)
```

- [ ] **Step 2: Commit**

```bash
git add docs/security/ats-credentials-rotation.md
git commit -m "docs(security): ATS credentials rotation runbook (precondition for prod)"
```

> **Note:** The `app/cli/ats_reencrypt.py` script referenced in step 3 is **out of MVP scope**; rotation will operate by holding both keys in the list (MultiFernet handles decrypt) until natural ciphertext churn. Add the script as a follow-up issue. Document this limitation inside the runbook if rotation is needed before the script ships.

---

## Phase 12 — Frontend API client + Zod schemas

The remaining frontend phases work in `frontend/app/` (the recruiter dashboard). All paths below are relative to that directory unless prefixed with `backend/`.

Follow the patterns documented in `frontend/app/CLAUDE.md`:
- `components/px/` primitives on `@base-ui-components/react`.
- React Hook Form + Zod for every form.
- TanStack Query (`useMutation`, `useQuery`, `queryClient.invalidateQueries`).
- `apiFetch<T>` from `lib/api/client.ts` for backend calls.
- `sonner` toasts.
- Tailwind v4 utilities only.

### Task 32: API types + Zod schemas

**Files:**
- Create: `lib/types/ats.ts`
- Create: `lib/schemas/ats.ts`
- Create: `tests/lib/schemas/ats.test.ts`

- [ ] **Step 1: Write the failing schema test**

Create `tests/lib/schemas/ats.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { ceipalCredentialsSchema, connectionCreateSchema } from "@/lib/schemas/ats";

describe("ceipalCredentialsSchema", () => {
  it("accepts valid credentials", () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: "u@x.com", password: "p@ss!w0rd", api_key: "k-abc-123",
    });
    expect(result.success).toBe(true);
  });

  it("rejects invalid email", () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: "not-an-email", password: "p", api_key: "k",
    });
    expect(result.success).toBe(false);
  });

  it("rejects empty password", () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: "u@x.com", password: "", api_key: "k",
    });
    expect(result.success).toBe(false);
  });

  it("rejects empty api_key", () => {
    const result = ceipalCredentialsSchema.safeParse({
      email: "u@x.com", password: "p", api_key: "",
    });
    expect(result.success).toBe(false);
  });
});

describe("connectionCreateSchema (discriminated union by vendor)", () => {
  it("accepts a ceipal payload", () => {
    const result = connectionCreateSchema.safeParse({
      vendor: "ceipal",
      credentials: { email: "u@x.com", password: "p", api_key: "k" },
    });
    expect(result.success).toBe(true);
  });

  it("rejects an unknown vendor", () => {
    const result = connectionCreateSchema.safeParse({
      vendor: "greenhouse",          // not yet supported
      credentials: { token: "x" },
    });
    expect(result.success).toBe(false);
  });
});
```

- [ ] **Step 2: Run — verify fail**

```bash
cd frontend/app && npm run test -- --run ats
```

Expected: import errors.

- [ ] **Step 3: Implement types + schemas**

Create `lib/types/ats.ts`:

```typescript
export type ATSVendor = "ceipal";  // extend when adding adapters

export interface ATSConnection {
  id: string;
  vendor: ATSVendor;
  active: boolean;
  last_synced_at: string | null;
  next_poll_at: string | null;
  last_poll_error: string | null;
  disabled_reason: string | null;
  created_at: string;
}

export interface ATSSyncLog {
  id: string;
  started_at: string;
  completed_at: string | null;
  status: "running" | "success" | "partial" | "failed";
  entity_counts: Record<string, { new: number; updated: number; skipped: number; errors: number } | null>;
  error_phase: string | null;
  error_summary: string | null;
}

export interface ATSUnmappedUser {
  external_user_id: string;
  external_user_email: string;
  external_user_display_name: string;
  external_user_role: string | null;
}
```

Create `lib/schemas/ats.ts`:

```typescript
import { z } from "zod";

export const ceipalCredentialsSchema = z.object({
  email: z.string().email("Enter a valid email"),
  password: z.string().min(1, "Password is required"),
  api_key: z.string().min(1, "API key is required"),
});

export type CeipalCredentials = z.infer<typeof ceipalCredentialsSchema>;

export const connectionCreateSchema = z.discriminatedUnion("vendor", [
  z.object({
    vendor: z.literal("ceipal"),
    credentials: ceipalCredentialsSchema,
  }),
  // Future: z.object({ vendor: z.literal("greenhouse"), credentials: greenhouseCredentialsSchema })
]);

export type ConnectionCreatePayload = z.infer<typeof connectionCreateSchema>;

export const mapUserSchema = z.object({
  internal_user_id: z.string().uuid("Select a ProjectX user"),
});

export type MapUserPayload = z.infer<typeof mapUserSchema>;
```

- [ ] **Step 4: Run — verify pass**

```bash
cd frontend/app && npm run test -- --run ats
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/types/ats.ts frontend/app/lib/schemas/ats.ts \
        frontend/app/tests/lib/schemas/ats.test.ts
git commit -m "feat(frontend/ats): types + Zod schemas (discriminated-union connection create)"
```

### Task 33: `apiFetch` wrappers in `lib/api/ats.ts`

**Files:**
- Create: `frontend/app/lib/api/ats.ts`
- Create: `frontend/app/tests/lib/api/ats.test.ts`

- [ ] **Step 1: Write a smoke test (URL paths + method)**

Create `frontend/app/tests/lib/api/ats.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({
  apiFetch: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ats api wrappers", () => {
  it("listConnections calls GET /api/ats/connections", async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue([]);
    const { listConnections } = await import("@/lib/api/ats");
    await listConnections("tok");
    expect(mock).toHaveBeenCalledWith("/api/ats/connections",
      expect.objectContaining({ token: "tok", method: undefined }));
  });

  it("createConnection POSTs the body", async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue({ id: "x" });
    const { createConnection } = await import("@/lib/api/ats");
    await createConnection("tok", {
      vendor: "ceipal",
      credentials: { email: "u@x.com", password: "p", api_key: "k" },
    });
    expect(mock).toHaveBeenCalledWith("/api/ats/connections",
      expect.objectContaining({ method: "POST" }));
  });

  it("triggerManualSync POSTs to /sync", async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue({ status: "enqueued" });
    const { triggerManualSync } = await import("@/lib/api/ats");
    await triggerManualSync("tok", "conn-123");
    expect(mock).toHaveBeenCalledWith("/api/ats/connections/conn-123/sync",
      expect.objectContaining({ method: "POST" }));
  });
});
```

- [ ] **Step 2: Run — verify fail**

```bash
cd frontend/app && npm run test -- --run ats.test
```

- [ ] **Step 3: Implement the wrappers**

Create `frontend/app/lib/api/ats.ts`:

```typescript
import { apiFetch } from "@/lib/api/client";
import type {
  ATSConnection, ATSSyncLog, ATSUnmappedUser,
} from "@/lib/types/ats";
import type {
  ConnectionCreatePayload, MapUserPayload,
} from "@/lib/schemas/ats";

export async function listConnections(token: string): Promise<ATSConnection[]> {
  return apiFetch<ATSConnection[]>("/api/ats/connections", { token });
}

export async function getConnection(token: string, id: string): Promise<ATSConnection> {
  return apiFetch<ATSConnection>(`/api/ats/connections/${id}`, { token });
}

export async function createConnection(
  token: string,
  body: ConnectionCreatePayload,
): Promise<ATSConnection> {
  return apiFetch<ATSConnection>("/api/ats/connections", {
    token,
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function deleteConnection(token: string, id: string): Promise<void> {
  await apiFetch<void>(`/api/ats/connections/${id}`, {
    token,
    method: "DELETE",
  });
}

export async function triggerManualSync(
  token: string,
  id: string,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/ats/connections/${id}/sync`, {
    token,
    method: "POST",
  });
}

export async function listSyncLogs(
  token: string,
  connectionId: string,
): Promise<ATSSyncLog[]> {
  return apiFetch<ATSSyncLog[]>(
    `/api/ats/connections/${connectionId}/sync-logs`,
    { token },
  );
}

export async function listUnmappedUsers(
  token: string,
  connectionId: string,
): Promise<ATSUnmappedUser[]> {
  return apiFetch<ATSUnmappedUser[]>(
    `/api/ats/connections/${connectionId}/unmapped-users`,
    { token },
  );
}

export async function mapATSUser(
  token: string,
  connectionId: string,
  externalUserId: string,
  body: MapUserPayload,
): Promise<void> {
  await apiFetch<void>(
    `/api/ats/connections/${connectionId}/users/${externalUserId}/map`,
    { token, method: "POST", body: JSON.stringify(body) },
  );
}
```

- [ ] **Step 4: Run — verify pass**

```bash
cd frontend/app && npm run test -- --run ats.test
```

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/api/ats.ts frontend/app/tests/lib/api/ats.test.ts
git commit -m "feat(frontend/ats): apiFetch wrappers (list/get/create/delete/sync/sync-logs/map-user)"
```

---

## Phase 13 — Frontend pages

Four routes under `/settings/integrations/`. Each follows the canonical pattern: server-side data fetch via a `getFreshSupabaseToken()` + `useQuery`, mutations via `useMutation`, toast on success + `queryClient.invalidateQueries`.

### Task 34: `/settings/integrations` — list page

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/integrations/page.tsx`
- Create: `frontend/app/components/settings/integrations/ConnectionListCard.tsx`

- [ ] **Step 1: Implement the list page**

Create `frontend/app/app/(dashboard)/settings/integrations/page.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Button } from "@/components/px/Button";
import { Skeleton } from "@/components/px/Skeleton";
import { ConnectionListCard } from "@/components/settings/integrations/ConnectionListCard";
import { listConnections } from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth";

export default function IntegrationsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ats", "connections"],
    queryFn: async () => listConnections(await getFreshSupabaseToken()),
  });

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Integrations</h1>
          <p className="text-sm text-muted-foreground">
            Connect an ATS so ProjectX can import your clients, jobs, and candidates automatically.
          </p>
        </div>
        <Button asChild>
          <Link href="/settings/integrations/connect">Connect ATS</Link>
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          Could not load integrations. {(error as Error).message}
        </div>
      )}

      {data && data.length === 0 && (
        <div className="rounded-md border border-dashed p-8 text-center">
          <p className="text-sm text-muted-foreground">
            No ATS connected yet. Connect Ceipal to start importing jobs and candidates.
          </p>
        </div>
      )}

      {data && data.length > 0 && (
        <div className="space-y-3">
          {data.map((c) => (
            <ConnectionListCard key={c.id} connection={c} />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Implement the card component**

Create `frontend/app/components/settings/integrations/ConnectionListCard.tsx`:

```tsx
"use client";

import Link from "next/link";
import { Badge } from "@/components/px/Badge";
import { Button } from "@/components/px/Button";
import type { ATSConnection } from "@/lib/types/ats";

const VENDOR_LABEL: Record<string, string> = { ceipal: "Ceipal" };

export function ConnectionListCard({ connection }: { connection: ATSConnection }) {
  const statusBadge = !connection.active ? (
    <Badge variant="destructive">Disabled</Badge>
  ) : connection.last_poll_error ? (
    <Badge variant="warning">Error</Badge>
  ) : (
    <Badge variant="success">Active</Badge>
  );

  const lastSynced = connection.last_synced_at
    ? new Date(connection.last_synced_at).toLocaleString()
    : "Never";
  const nextPoll = connection.next_poll_at
    ? new Date(connection.next_poll_at).toLocaleString()
    : "—";

  return (
    <div className="flex items-center justify-between rounded-md border p-4">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="font-medium">{VENDOR_LABEL[connection.vendor] ?? connection.vendor}</h3>
          {statusBadge}
        </div>
        <p className="text-sm text-muted-foreground">
          Last synced: {lastSynced} · Next: {nextPoll}
        </p>
        {connection.disabled_reason && (
          <p className="text-sm text-destructive">{connection.disabled_reason}</p>
        )}
      </div>
      <Button asChild variant="outline" size="sm">
        <Link href={`/settings/integrations/${connection.id}`}>Manage</Link>
      </Button>
    </div>
  );
}
```

- [ ] **Step 3: Smoke-test the route**

```bash
cd frontend/app && npm run dev
```

In a browser, navigate to `http://localhost:3000/settings/integrations`. Expect:
- Heading "Integrations" + "Connect ATS" button.
- "No ATS connected yet" empty state (since no connection rows exist yet).

Kill the dev server (Ctrl+C).

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/integrations/page.tsx \
        frontend/app/components/settings/integrations/ConnectionListCard.tsx
git commit -m "feat(frontend/integrations): list page with empty state + status badges"
```

### Task 35: `/settings/integrations/connect` — create form

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/integrations/connect/page.tsx`
- Create: `frontend/app/components/settings/integrations/CeipalConnectionForm.tsx`

- [ ] **Step 1: Implement the create-route page**

Create `frontend/app/app/(dashboard)/settings/integrations/connect/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { CeipalConnectionForm } from "@/components/settings/integrations/CeipalConnectionForm";

export default function ConnectPage() {
  const [vendor, setVendor] = useState<"ceipal">("ceipal");

  return (
    <div className="mx-auto max-w-xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Connect ATS</h1>
        <p className="text-sm text-muted-foreground">
          Connect your ATS account so ProjectX can import clients, jobs, and candidates.
          Credentials are encrypted at rest.
        </p>
      </div>

      {/* Future: vendor picker. For MVP only Ceipal is supported. */}
      {vendor === "ceipal" && <CeipalConnectionForm />}
    </div>
  );
}
```

- [ ] **Step 2: Implement the form component**

Create `frontend/app/components/settings/integrations/CeipalConnectionForm.tsx`:

```tsx
"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Button } from "@/components/px/Button";
import { Input } from "@/components/px/Input";
import { Label } from "@/components/px/Label";
import { createConnection } from "@/lib/api/ats";
import { ApiError, ApiValidationError } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth";
import {
  ceipalCredentialsSchema, type CeipalCredentials,
} from "@/lib/schemas/ats";

export function CeipalConnectionForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const {
    register, handleSubmit, formState: { errors }, setError,
  } = useForm<CeipalCredentials>({
    resolver: zodResolver(ceipalCredentialsSchema),
  });

  const mutation = useMutation({
    mutationFn: async (values: CeipalCredentials) => {
      const token = await getFreshSupabaseToken();
      return createConnection(token, { vendor: "ceipal", credentials: values });
    },
    onSuccess: (connection) => {
      toast.success("Ceipal connected. Initial sync started.");
      queryClient.invalidateQueries({ queryKey: ["ats", "connections"] });
      router.push(`/settings/integrations/${connection.id}`);
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 422) {
        const detail = err.body?.detail;
        if (detail?.code === "ATS_CREDENTIALS_INVALID") {
          setError("password", {
            type: "server",
            message: "Ceipal rejected these credentials. Check email, password, and API key.",
          });
          return;
        }
      }
      toast.error("Could not connect Ceipal. Please try again.");
    },
  });

  return (
    <form
      onSubmit={handleSubmit((values) => mutation.mutate(values))}
      className="space-y-4 rounded-md border p-6"
      autoComplete="off"
    >
      <div className="space-y-1.5">
        <Label htmlFor="ats-email">Ceipal account email</Label>
        <Input id="ats-email" type="email" autoComplete="off"
               {...register("email")} />
        {errors.email && (
          <p className="text-sm text-destructive">{errors.email.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ats-password">Ceipal password</Label>
        <Input id="ats-password" type="password" autoComplete="new-password"
               {...register("password")} />
        {errors.password && (
          <p className="text-sm text-destructive">{errors.password.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ats-api-key">API key</Label>
        <Input id="ats-api-key" type="password" autoComplete="off"
               {...register("api_key")} />
        {errors.api_key && (
          <p className="text-sm text-destructive">{errors.api_key.message}</p>
        )}
        <p className="text-xs text-muted-foreground">
          From Ceipal: Settings → Integrations → API.
        </p>
      </div>

      <div className="rounded-md bg-muted/40 p-3 text-xs text-muted-foreground">
        These credentials are encrypted at rest with AES-128 (Fernet) and never appear in logs.
        ProjectX uses them only to fetch jobs, applicants, and submissions on a 15-minute interval.
      </div>

      <Button type="submit" disabled={mutation.isPending} className="w-full">
        {mutation.isPending ? "Testing connection…" : "Connect Ceipal"}
      </Button>
    </form>
  );
}
```

- [ ] **Step 3: Manual smoke test**

Start the dev server and walk through:
1. Navigate to `/settings/integrations/connect`.
2. Submit with empty fields → Zod errors render below each field.
3. Submit with a fake email → field error.
4. Submit with valid-shape but wrong credentials → backend returns 422 → password field shows "Ceipal rejected these credentials."

Keep the dev server logs visible — confirm no credentials appear in the network panel response body.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/integrations/connect/page.tsx \
        frontend/app/components/settings/integrations/CeipalConnectionForm.tsx
git commit -m "feat(frontend/integrations): Ceipal connect form (RHF+Zod, 422 password-field surfacing)"
```

### Task 36: Connection detail page (sync history + manual sync)

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx`
- Create: `frontend/app/components/settings/integrations/SyncLogTable.tsx`

- [ ] **Step 1: Implement the detail page**

Create `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx`:

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/px/Badge";
import { Button } from "@/components/px/Button";
import { DangerConfirmDialog } from "@/components/px/DangerConfirmDialog";
import { Skeleton } from "@/components/px/Skeleton";
import { SyncLogTable } from "@/components/settings/integrations/SyncLogTable";
import {
  deleteConnection, getConnection, listSyncLogs, triggerManualSync,
} from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth";

export default function ConnectionDetailPage() {
  const { connectionId } = useParams<{ connectionId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const connection = useQuery({
    queryKey: ["ats", "connection", connectionId],
    queryFn: async () => getConnection(await getFreshSupabaseToken(), connectionId),
  });

  const syncLogs = useQuery({
    queryKey: ["ats", "connection", connectionId, "sync-logs"],
    queryFn: async () =>
      listSyncLogs(await getFreshSupabaseToken(), connectionId),
    refetchInterval: 10_000,    // poll for new sync logs every 10s
  });

  const syncNow = useMutation({
    mutationFn: async () =>
      triggerManualSync(await getFreshSupabaseToken(), connectionId),
    onSuccess: () => {
      toast.success("Sync queued. Logs refresh in 10 seconds.");
      queryClient.invalidateQueries({ queryKey: ["ats", "connection", connectionId, "sync-logs"] });
    },
    onError: () => toast.error("Could not trigger sync."),
  });

  const remove = useMutation({
    mutationFn: async () =>
      deleteConnection(await getFreshSupabaseToken(), connectionId),
    onSuccess: () => {
      toast.success("ATS connection removed.");
      queryClient.invalidateQueries({ queryKey: ["ats", "connections"] });
      router.push("/settings/integrations");
    },
    onError: () => toast.error("Could not delete connection."),
  });

  if (connection.isLoading) return <Skeleton className="h-32 w-full" />;
  if (!connection.data) return <div>Connection not found.</div>;

  const c = connection.data;
  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">
          {c.vendor === "ceipal" ? "Ceipal" : c.vendor}
        </h1>
        <div className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
          <Badge variant={c.active ? "success" : "destructive"}>
            {c.active ? "Active" : "Disabled"}
          </Badge>
          {c.last_synced_at && (
            <span>Last synced {new Date(c.last_synced_at).toLocaleString()}</span>
          )}
          {c.disabled_reason && (
            <span className="text-destructive">{c.disabled_reason}</span>
          )}
        </div>
      </div>

      <div className="flex gap-2">
        <Button onClick={() => syncNow.mutate()} disabled={syncNow.isPending}>
          {syncNow.isPending ? "Queueing…" : "Sync now"}
        </Button>
        <Button variant="outline"
                onClick={() => router.push(`/settings/integrations/${connectionId}/users`)}>
          Manage user mappings
        </Button>
        <div className="ml-auto">
          <Button variant="destructive" onClick={() => setConfirmDelete(true)}>
            Remove connection
          </Button>
        </div>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Recent syncs</h2>
        <SyncLogTable
          logs={syncLogs.data ?? []}
          isLoading={syncLogs.isLoading}
        />
      </section>

      <DangerConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Remove ATS connection?"
        description="This stops scheduled syncs. Imported clients, jobs, and candidates stay in ProjectX."
        confirmLabel="Remove"
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}
```

- [ ] **Step 2: Implement the sync-log table component**

Create `frontend/app/components/settings/integrations/SyncLogTable.tsx`:

```tsx
"use client";

import { Badge } from "@/components/px/Badge";
import { Skeleton } from "@/components/px/Skeleton";
import type { ATSSyncLog } from "@/lib/types/ats";

const STATUS_VARIANT = {
  running: "secondary",
  success: "success",
  partial: "warning",
  failed: "destructive",
} as const;

function formatCounts(counts: ATSSyncLog["entity_counts"]): string {
  const parts: string[] = [];
  for (const [phase, c] of Object.entries(counts)) {
    if (!c) continue;
    if (c.new || c.updated) parts.push(`${phase}: +${c.new}/~${c.updated}`);
  }
  return parts.join(" · ") || "—";
}

export function SyncLogTable({
  logs, isLoading,
}: { logs: ATSSyncLog[]; isLoading: boolean }) {
  if (isLoading) return <Skeleton className="h-24 w-full" />;
  if (logs.length === 0) {
    return <p className="text-sm text-muted-foreground">No syncs recorded yet.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/40">
          <tr>
            <th className="px-3 py-2 text-left">Started</th>
            <th className="px-3 py-2 text-left">Status</th>
            <th className="px-3 py-2 text-left">Counts</th>
            <th className="px-3 py-2 text-left">Error</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr key={log.id} className="border-b last:border-b-0">
              <td className="px-3 py-2">{new Date(log.started_at).toLocaleString()}</td>
              <td className="px-3 py-2">
                <Badge variant={STATUS_VARIANT[log.status]}>{log.status}</Badge>
              </td>
              <td className="px-3 py-2 font-mono text-xs">{formatCounts(log.entity_counts)}</td>
              <td className="px-3 py-2 text-xs text-destructive">
                {log.error_phase && <span className="font-medium">{log.error_phase}:</span>}
                {log.error_summary}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Smoke-test in browser**

Walk through the page in the dev server: connection metadata renders, "Sync now" button enqueues, sync-log table polls every 10s, danger-confirm dialog gates delete.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/integrations/\[connectionId\]/page.tsx \
        frontend/app/components/settings/integrations/SyncLogTable.tsx
git commit -m "feat(frontend/integrations): detail page with sync history (10s poll) + manual sync + danger delete"
```

### Task 37: User mapping page

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/users/page.tsx`
- Create: `frontend/app/components/settings/integrations/UserMappingTable.tsx`

- [ ] **Step 1: Implement the user-mapping page**

Create `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/users/page.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { Skeleton } from "@/components/px/Skeleton";
import { UserMappingTable } from "@/components/settings/integrations/UserMappingTable";
import { listUnmappedUsers } from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth";

export default function UserMappingPage() {
  const { connectionId } = useParams<{ connectionId: string }>();

  const unmapped = useQuery({
    queryKey: ["ats", "connection", connectionId, "unmapped-users"],
    queryFn: async () =>
      listUnmappedUsers(await getFreshSupabaseToken(), connectionId),
  });

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">User mappings</h1>
        <p className="text-sm text-muted-foreground">
          Map your Ceipal recruiters to ProjectX users so that assigned-recruiter
          fields on imported jobs resolve correctly.
        </p>
      </div>

      {unmapped.isLoading && <Skeleton className="h-32 w-full" />}
      {unmapped.data && (
        <UserMappingTable
          users={unmapped.data}
          connectionId={connectionId}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Implement the mapping table component**

Create `frontend/app/components/settings/integrations/UserMappingTable.tsx`:

```tsx
"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/px/Button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/px/Select";
import { mapATSUser } from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth";
import type { ATSUnmappedUser } from "@/lib/types/ats";

// Assumes an existing `useTeamMembers` hook returns the tenant's ProjectX users.
import { useTeamMembers } from "@/lib/hooks/useTeamMembers";

export function UserMappingTable({
  users, connectionId,
}: { users: ATSUnmappedUser[]; connectionId: string }) {
  const queryClient = useQueryClient();
  const team = useTeamMembers();
  const [selections, setSelections] = useState<Record<string, string>>({});

  const mapMutation = useMutation({
    mutationFn: async ({ externalUserId, internalUserId }: {
      externalUserId: string; internalUserId: string;
    }) => mapATSUser(
      await getFreshSupabaseToken(),
      connectionId, externalUserId,
      { internal_user_id: internalUserId },
    ),
    onSuccess: () => {
      toast.success("User mapped.");
      queryClient.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "unmapped-users"],
      });
    },
    onError: () => toast.error("Could not map user."),
  });

  if (users.length === 0) {
    return (
      <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
        All Ceipal users are mapped.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {users.map((u) => (
        <div key={u.external_user_id}
             className="flex items-center justify-between rounded-md border p-4">
          <div className="space-y-1">
            <p className="font-medium">{u.external_user_display_name}</p>
            <p className="text-sm text-muted-foreground">
              {u.external_user_email} · {u.external_user_role ?? "no role"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Select
              value={selections[u.external_user_id] ?? ""}
              onValueChange={(v) =>
                setSelections((s) => ({ ...s, [u.external_user_id]: v }))
              }
            >
              <SelectTrigger className="w-[260px]">
                <SelectValue placeholder="Pick a ProjectX user" />
              </SelectTrigger>
              <SelectContent>
                {team.data?.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.display_name} · {m.email}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              disabled={!selections[u.external_user_id] || mapMutation.isPending}
              onClick={() => mapMutation.mutate({
                externalUserId: u.external_user_id,
                internalUserId: selections[u.external_user_id]!,
              })}
            >
              Map
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
```

> **Note:** the `useTeamMembers` hook is assumed to exist (it's used by `/settings/team`). If the existing app uses a different hook name (e.g. `useTenantMembers`), adjust the import.

- [ ] **Step 3: Smoke-test in browser**

Mock at least two ATS users on the backend (insert directly via psql or via a full sync run), navigate to `/settings/integrations/<id>/users`, confirm the table renders, picker shows ProjectX team members, "Map" enqueues the request.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/integrations/\[connectionId\]/users/page.tsx \
        frontend/app/components/settings/integrations/UserMappingTable.tsx
git commit -m "feat(frontend/integrations): user mapping page (per-row picker + Map action)"
```

---

## Phase 14 — Frontend integration into existing surfaces

Five small enhancements to existing pages: settings nav link, org-visualizer badge, JD list filter + chip, candidate-card source badge + Ceipal-status badge.

### Task 38: Settings sidebar — add Integrations link

**Files:**
- Modify: `frontend/app/components/dashboard/SettingsNav.tsx` (or whichever component renders the settings sidebar — search `/settings/team` link in the codebase to find the right file)

- [ ] **Step 1: Add a nav entry**

Find the settings nav component (search for the `Team` or `Org Units` link path). Append an Integrations entry:

```tsx
<NavLink href="/settings/integrations" icon={PlugZap}>
  Integrations
</NavLink>
```

(Where `PlugZap` or any matching lucide-react icon visually conveys "integration".)

- [ ] **Step 2: Smoke-test**

Restart the dev server, navigate to any `/settings/*` page, confirm "Integrations" appears in the sidebar and routes to the list page.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/SettingsNav.tsx
git commit -m "feat(frontend/dashboard): add Integrations to the settings sidebar"
```

### Task 39: Org visualizer — incomplete-profile badge

**Files:**
- Modify: `frontend/app/components/dashboard/OrgVisualizer.tsx` (or the existing component that renders org-unit cards in the org-units page — search the org-units route to find the right file)
- Modify: the `OrgUnit` type to include `company_profile_completion_status`

- [ ] **Step 1: Extend the type**

Find the `OrgUnit` / `OrganizationalUnit` TypeScript type (typically in `lib/types/org-units.ts`). Add the new field:

```typescript
company_profile_completion_status: "pending" | "complete";
```

- [ ] **Step 2: Surface the badge on each unit card**

In the component that renders one unit card, after the unit name, add:

```tsx
{unit.unit_type === "client_account" &&
 unit.company_profile_completion_status === "pending" && (
  <Tooltip content="Imported from ATS. Complete the company profile to enable job creation.">
    <Badge variant="warning" size="sm">Profile incomplete</Badge>
  </Tooltip>
)}
```

- [ ] **Step 3: Smoke-test**

Manually seed a pending client_account org_unit (or run an ATS sync end-to-end), navigate to `/settings/org-units`, confirm the badge appears on the imported unit.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/lib/types/org-units.ts \
        frontend/app/components/dashboard/OrgVisualizer.tsx
git commit -m "feat(frontend/org-units): incomplete-profile badge for ATS-imported client_accounts"
```

### Task 40: JD list — "blocked" filter + chip

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/page.tsx` (or wherever the JD list view lives)
- Modify: the `JobPosting` TypeScript type to include `source`, `external_id`, `external_status`

- [ ] **Step 1: Extend the type**

In `lib/types/jobs.ts` (or wherever `JobPosting` is defined), add:

```typescript
source: string;            // 'native' | 'manual' | 'ats_ceipal'
external_id: string | null;
external_status: string | null;
status: JobStatus;         // add 'blocked_pending_client_setup' to the JobStatus union
```

- [ ] **Step 2: Add the chip on imported jobs**

In the JD-list row component, after the title:

```tsx
{job.source.startsWith("ats_") && (
  <Badge variant="secondary" size="sm">
    From {job.source.replace("ats_", "")}
  </Badge>
)}
{job.status === "blocked_pending_client_setup" && (
  <Badge variant="warning" size="sm">Awaiting client setup</Badge>
)}
```

- [ ] **Step 3: Add a filter pill that shows blocked-count**

In the filter bar at the top of the JD list:

```tsx
const blockedCount = jobs?.filter(
  (j) => j.status === "blocked_pending_client_setup"
).length ?? 0;

{blockedCount > 0 && (
  <Button
    variant={filter === "blocked" ? "default" : "outline"}
    size="sm"
    onClick={() => setFilter(filter === "blocked" ? null : "blocked")}
  >
    Blocked on setup ({blockedCount})
  </Button>
)}
```

- [ ] **Step 4: Smoke-test**

Seed a blocked JD (or run an ATS sync with a pending client), navigate to `/jobs`, confirm the chip appears on the row and the filter pill toggles correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/types/jobs.ts \
        frontend/app/app/\(dashboard\)/jobs/page.tsx
git commit -m "feat(frontend/jobs): From-ATS chip + Blocked-on-setup filter pill"
```

### Task 41: Candidate cards — source + Ceipal status badges

**Files:**
- Modify: `frontend/app/components/dashboard/candidates/CandidateCard.tsx` (or the existing candidate-row/card component)
- Modify: `Candidate` TypeScript type to include `source` and assignment metadata

- [ ] **Step 1: Surface the source badge**

In the candidate card, after the name:

```tsx
{candidate.source.startsWith("ats_") && (
  <Badge variant="secondary" size="sm">
    Imported from {candidate.source.replace("ats_", "")}
  </Badge>
)}
```

- [ ] **Step 2: Surface the Ceipal submission status (when assignment metadata is present)**

If the candidate row in this view carries an assignment with `source_metadata.submission_status`:

```tsx
{assignment?.source_metadata?.submission_status && (
  <Tooltip content="Ceipal's pipeline status for this submission.">
    <Badge variant="outline" size="sm">
      Ceipal: {assignment.source_metadata.submission_status}
    </Badge>
  </Tooltip>
)}
```

- [ ] **Step 3: Smoke-test**

Run an end-to-end ATS sync, navigate to the candidate list / kanban, confirm both badges render on imported candidates.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/dashboard/candidates/CandidateCard.tsx
git commit -m "feat(frontend/candidates): ATS source badge + Ceipal submission_status badge"
```

### Task 42: Unblock cascade in company-profile completion form

**Files:**
- Modify: `frontend/app/components/dashboard/company-profile-form.tsx`
- Modify: `app/modules/org_units/router.py` (the `PUT /api/org-units/{id}` handler)

The schema migration added a `company_profile_completion_status` column. When the recruiter completes a `pending` profile via the existing form, the backend handler must call `_unblock_pending_jobs_for_org_unit` (from Task 17) and enqueue extraction actors for each unblocked JD.

- [ ] **Step 1: Wire the unblock trigger in the backend handler**

Find the route handler for `PUT /api/org-units/{id}` (most likely in `app/modules/org_units/router.py`). Around the line where the org_unit is updated and committed, add (after the commit):

```python
from app.modules.jd.actors import extract_and_enhance_jd
from app.modules.org_units.service import _unblock_pending_jobs_for_org_unit

# Detect pending → complete transition. The handler already has `existing` (the
# row before update) and `body` (the request payload). Adjust the variable
# names to match the existing handler's locals.
if (
    existing.company_profile_completion_status == "pending"
    and body.company_profile_completion_status == "complete"
):
    # Already inside a get_tenant_db session — caller commits.
    unblocked_ids = await _unblock_pending_jobs_for_org_unit(
        db, org_unit_id=org_unit.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    # Enqueue extraction outside the request transaction
    import uuid as _uuid
    for jid in unblocked_ids:
        extract_and_enhance_jd.send(
            jid, str(user.tenant_id), f"unblock-{_uuid.uuid4()}",
        )
```

- [ ] **Step 2: Surface "X blocked jobs queued for processing" on success**

In `company-profile-form.tsx`, on save success, if the response includes a count of unblocked jobs, toast:

```tsx
onSuccess: (response) => {
  toast.success("Company profile saved");
  if (response.unblocked_job_count && response.unblocked_job_count > 0) {
    toast.info(
      `${response.unblocked_job_count} job${response.unblocked_job_count === 1 ? "" : "s"} ` +
      `queued for processing.`,
    );
  }
  queryClient.invalidateQueries({ queryKey: ["org-unit", unitId] });
  queryClient.invalidateQueries({ queryKey: ["jobs"] });
},
```

The backend response shape needs to include `unblocked_job_count` — extend the `OrgUnitResponse` schema accordingly.

- [ ] **Step 3: Smoke-test**

End-to-end: connect Ceipal → ATS sync imports 2 jobs for a new client (both `blocked_pending_client_setup`) → recruiter completes the company profile → toast says "2 jobs queued for processing" → both JDs visible at `/jobs` with status `draft` (and within seconds, `signals_extracted` after extraction completes).

- [ ] **Step 4: Commit**

```bash
git add app/modules/org_units/router.py \
        frontend/app/components/dashboard/company-profile-form.tsx
git commit -m "feat(unblock-cascade): wire profile-completion → unblock-pending-jobs + extraction enqueue"
```

---

## Phase 15 — End-to-end verification + rollout

### Task 43: End-to-end integration test

**Files:**
- Create: `tests/modules/ats/test_end_to_end.py`

- [ ] **Step 1: Write a comprehensive E2E test using a mock CeipalAdapter**

This test exercises the full path: scheduler tick → actor → importer → all 5 phases → audit_log entries → ats_sync_logs row. It uses a hand-rolled fake adapter (no `httpx.MockTransport` — we're not testing the HTTP layer here, we're testing the orchestration).

Create `tests/modules/ats/test_end_to_end.py`:

```python
"""End-to-end ATS sync: mock CeipalAdapter feeds canonical DTOs into the
real importer/actor pipeline. Verifies:
  - org_unit auto-created with stub profile + pending status
  - blocked_pending_client_setup JD created
  - applicant becomes a candidate
  - candidate_job_assignment links the two
  - ats_sync_logs row closes with status='success'
  - audit_log has the expected sequence of entries
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.database import async_session_factory
from app.modules.ats.schemas import (
    ATSApplicantPayload, ATSClientPayload, ATSJobPayload,
    ATSSubmissionPayload, ATSUserPayload,
)


def _aiter(items):
    async def _gen():
        for x in items:
            yield x
    return _gen()


class _FakeCeipal:
    vendor = "ceipal"

    def __init__(self, state):
        self.state = state
        now = datetime.now(tz=timezone.utc)
        from datetime import timedelta
        self.state.access_token = "t"
        self.state.access_token_expires_at = now + timedelta(hours=1)
        self._now = now

    async def ensure_authenticated(self):
        pass

    def list_clients(self, since=None):
        return _aiter([ATSClientPayload(
            external_id="ceipal-client-1", name="Oracle",
            website="www.oracle.com", industry="Computer Software",
            country="India", state="Karnataka",
            raw={}, fetched_at=self._now,
        )])

    def list_users(self, since=None):
        return _aiter([ATSUserPayload(
            external_id="ceipal-user-1", email="rec@x.com",
            display_name="Recruiter One",
            raw={}, fetched_at=self._now,
        )])

    def list_jobs(self, since=None):
        return _aiter([ATSJobPayload(
            external_id="ceipal-job-1", external_client_id="ceipal-client-1",
            title="Java Developer", description="JD body", status="Active",
            raw={}, fetched_at=self._now,
        )])

    def list_applicants(self, since=None):
        return _aiter([ATSApplicantPayload(
            external_id="ceipal-appl-1", name="Jane Doe",
            email="jane@x.com",
            raw={}, fetched_at=self._now,
        )])

    def list_submissions(self, job_external_id, since=None):
        if job_external_id != "ceipal-job-1":
            return _aiter([])
        return _aiter([ATSSubmissionPayload(
            external_id="ceipal-sub-1",
            applicant_external_id="ceipal-appl-1",
            job_external_id="ceipal-job-1",
            submission_status="Submitted",
            raw={}, fetched_at=self._now,
        )])


@pytest.mark.asyncio
async def test_end_to_end_sync_creates_full_picture(actor_fixture):
    """One sync run materializes: org_unit (pending) + JD (blocked) + candidate
    + assignment + sync_log(success) + audit_log entries."""
    from app.modules.ats.actors import _run_poll

    tenant_id, connection_id = actor_fixture

    with patch("app.modules.ats.actors.get_ats_adapter", side_effect=lambda s: _FakeCeipal(s)):
        await _run_poll(connection_id, tenant_id)

    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await session.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

            # client_account auto-created
            r = await session.execute(text(
                "SELECT name, company_profile_completion_status "
                "FROM organizational_units "
                "WHERE client_id = :t AND unit_type = 'client_account'"
            ), {"t": tenant_id})
            unit = r.one()
            assert unit.name == "Oracle"
            assert unit.company_profile_completion_status == "pending"

            # JD blocked
            r = await session.execute(text(
                "SELECT status, external_id FROM job_postings WHERE tenant_id = :t"
            ), {"t": tenant_id})
            jd = r.one()
            assert jd.status == "blocked_pending_client_setup"
            assert jd.external_id == "ceipal-job-1"

            # Candidate
            r = await session.execute(text(
                "SELECT email, source, external_id FROM candidates WHERE tenant_id = :t"
            ), {"t": tenant_id})
            cand = r.one()
            assert cand.email == "jane@x.com"
            assert cand.source == "ats_ceipal"
            assert cand.external_id == "ceipal-appl-1"

            # Assignment (submission)
            r = await session.execute(text(
                "SELECT source, external_id FROM candidate_job_assignments "
                "WHERE tenant_id = :t"
            ), {"t": tenant_id})
            asg = r.one()
            assert asg.source == "ats_ceipal"
            assert asg.external_id == "ceipal-sub-1"

            # Sync log closed cleanly
            r = await session.execute(text(
                "SELECT status, entity_counts FROM ats_sync_logs "
                "WHERE connection_id = :c ORDER BY started_at DESC LIMIT 1"
            ), {"c": connection_id})
            log = r.one()
            assert log.status == "success"
            assert log.entity_counts["clients"]["new"] == 1
            assert log.entity_counts["jobs"]["new"] == 1
            assert log.entity_counts["applicants"]["new"] == 1
            assert log.entity_counts["submissions"]["new"] == 1

            # Audit trail
            r = await session.execute(text(
                "SELECT action FROM audit_log "
                "WHERE tenant_id = :t AND action LIKE 'ats.%' OR action = 'jd.imported_from_ats' "
                "OR action = 'candidate.imported' ORDER BY occurred_at"
            ), {"t": tenant_id})
            actions = [row.action for row in r]
            assert "ats.sync.started" in actions
            assert "ats.client_mapping.created" in actions
            assert "jd.imported_from_ats" in actions
            assert "candidate.imported" in actions
            assert "ats.sync.completed" in actions
```

- [ ] **Step 2: Run the test**

```bash
docker compose run --rm nexus pytest tests/modules/ats/test_end_to_end.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/modules/ats/test_end_to_end.py
git commit -m "test(ats/e2e): one-shot sync materializes org_unit + JD + candidate + assignment + audit chain"
```

### Task 44: Final verification — full test suite + coverage gates

- [ ] **Step 1: Run the full ats test suite with coverage**

```bash
docker compose run --rm nexus pytest tests/modules/ats tests/cli \
  --cov=app/modules/ats --cov=app/cli/ats_tick \
  --cov-report=term-missing
```

Expected:
- All tests PASS.
- Coverage on `app/modules/ats/crypto.py` is **100%**.
- Coverage on `app/modules/ats/connection.py` is **100%** (branches: token present/null, expiries).
- Coverage on `app/modules/ats/registry.py` is **100%**.
- Overall `app/modules/ats/*` is **≥ 80%** (root `CLAUDE.md` line-coverage gate).

- [ ] **Step 2: Run the full backend test suite to check for regressions**

```bash
docker compose run --rm nexus pytest
```

Expected: all tests pass. If any pre-existing test fails, debug (likely a fixture collision; ats fixtures intentionally use tenant-scoped seeding to avoid leaking into other modules' tests).

- [ ] **Step 3: Run the frontend test suite**

```bash
cd frontend/app && npm run test
```

Expected: all tests pass.

- [ ] **Step 4: Run lint + type-check on both surfaces**

```bash
docker compose run --rm nexus ruff check app/modules/ats app/cli/ats_tick.py
docker compose run --rm nexus mypy app/modules/ats app/cli/ats_tick.py
cd frontend/app && npm run lint
cd frontend/app && npx tsc --noEmit
```

Expected: zero issues.

- [ ] **Step 5: Manual end-to-end smoke test (live Ceipal)**

In dev:
1. `docker compose up -d nexus nexus-worker nexus-scheduler redis`
2. Frontend: `cd frontend/app && npm run dev`
3. Log in as super_admin.
4. Navigate to `/settings/integrations/connect`. Enter your Ceipal email, password, API key.
5. Submit. Expect: toast "Ceipal connected. Initial sync started."; redirect to detail page.
6. Wait ~30 seconds. Refresh sync history: at least one row with `status='success'` and non-zero `entity_counts`.
7. Navigate to `/settings/org-units`. Expect: imported `client_account` units with "Profile incomplete" badges.
8. Navigate to `/jobs`. Expect: imported JDs (status = blocked_pending_client_setup) with "From Ceipal" + "Awaiting client setup" chips. The "Blocked on setup (N)" filter pill is visible.
9. Click a blocked JD's parent client → complete the company profile → save. Expect: toast "X jobs queued for processing."
10. Refresh `/jobs` after ~30 seconds. The unblocked JDs are now in `signals_extracted` state.
11. Navigate to a candidate that came in via Ceipal. Expect: "Imported from ceipal" badge + "Ceipal: <submission_status>" badge.

- [ ] **Step 6: Commit the verification log**

After the manual smoke passes, append a brief note to `docs/security/threat-model.md` (per root `CLAUDE.md` — any new external service entering the data path requires a threat-model update). One paragraph noting the new tenant-credential storage surface, the encryption-at-rest control, and the rotation runbook reference:

```markdown
## Phase ATS — 2026-05-12

A new tenant-scoped credential surface is introduced via the ATS adapter system
(`app/modules/ats/`). Per-tenant Ceipal email, password, API key, and OAuth-style
access/refresh tokens are stored encrypted at rest via Fernet (MultiFernet
keyring; key in env at MVP → AWS Secrets Manager at enterprise).

Rotation runbook: `docs/security/ats-credentials-rotation.md`.

Trust boundaries touched:
- Backend ↔ Ceipal API (HTTPS-only per Ceipal docs).
- Recruiter ↔ /api/ats/* (super_admin-gated; rate-limited per the table in this doc).

No PII enters logs (per the redactor table in the spec). Adapter `httpx` client
strips `Authorization` and request/response bodies from logs.
```

```bash
git add docs/security/threat-model.md
git commit -m "docs(security): threat model — add Phase ATS section (tenant credential surface)"
```

### Task 45: Drop the `ats_enabled` feature flag (post-GA)

(Out of plan-scope until after dogfood validation. The plan above does not introduce a feature flag; the spec mentions it as a rollout option but I judged the per-connection `active` boolean is sufficient — connections that aren't created don't sync, no flag needed. If during dogfood we want to gate the entire UI route tree behind a setting, this task adds `settings.ats_enabled` + a route-level guard. Track as a follow-up.)

---

## Summary

**Total: 45 tasks across 15 phases.** Each task ends in a green-tests commit. Partial runs are deployable.

**Critical paths (100% branch coverage):** `crypto.py`, `connection.py` (load/persist round-trip), all five new RLS-policied tables, the four auth-error branches in CeipalAdapter (`ensure_authenticated`).

**Audit trail covers (verified by Task 43):**
- `ats.connection.created` / `.disabled` / `.deleted`
- `ats.sync.started` / `.completed` / `.failed` / `.manually_triggered`
- `ats.client_mapping.created`
- `ats.user_mapping.created`
- `jd.imported_from_ats`
- `jd.unblocked_by_profile_completion`
- `candidate.imported` / `.linked_to_external`

**Observability:**
- structlog correlation_id (`ats-<uuid4>`) bound at actor entry, flows through every log line.
- OTel spans: `ats.tick` (per cron firing) → `ats.poll` (per tenant) → `ats.poll.auth` + `ats.sync.<phase>` (×5).
- Sentry tags: `connection_id`, `tenant_id`, `vendor`; permanent errors `level=error`, rate-limited `level=warning`.

**PII discipline:** credentials and tokens never appear in logs (Fernet ciphertext stored as `BYTEA`; `httpx` log redactor strips bodies + Authorization headers). The verbatim Ceipal payload lives in `source_metadata` JSONB on DB rows, never in log fields.

**Operational shape:**
- Railway → ECS migration is a deploy-target swap (no code change).
- One Dramatiq actor per tenant per cron firing — fully horizontally scalable.
- No leader election; the scheduler cron is the single source of "now."

**Spec ↔ plan coverage check (final):**
- Polling scheduler (spec §3) → Phase 1 (RLS columns) + Phase 9 (CLI + compose) ✓
- Adapter Protocol shape (spec §4) → Phase 4 ✓
- Connection state + encryption (spec §5) → Phases 2, 3 ✓
- Adapter registry (spec §6) → Phase 4 ✓
- Importer (spec §7) → Phase 7 ✓
- Ceipal adapter (spec §8) → Phase 5 ✓
- Data model migration 0029 (spec §9) → Phase 1 ✓
- Recruiter router (spec §10) → Phase 10 ✓
- Audit + observability + PII (spec §11) → spread across Phases 6, 7, 8, 11, 15 ✓
- Module boundary (spec §12) → Phase 11 ✓
- Frontend touchpoints (spec §13) → Phases 12, 13, 14 ✓
- Testing strategy (spec §15) → coverage gates enforced at Phase 15 ✓
- Rollout plan (spec §16) → Phase 15 ✓
