# Engine Failure Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the silent-failure gap when the interview engine's `entrypoint()` crashes before `session.start()` completes — every failure leaves a durable, observable trace and every consumer (candidate, recruiter) can see what happened.

**Architecture:** Wrap `entrypoint()` body in a single try/except (Approach A) that calls a dedicated `_handle_entrypoint_failure()`; add a background apscheduler-driven reaper as belt-and-suspenders for cases the in-process handler can't catch (SIGKILL/OOM/never-dispatched); surface `state='error'` + `error_code` on both frontends via the existing `useSessionOutcome` LK-attribute path plus a new HTTP state-poll fallback.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, Dramatiq (workers — not used here), apscheduler 3.10+ (new dep), PostgreSQL 16 (asyncpg + advisory locks), pydantic v2, structlog, LiveKit Agents SDK. Frontend: Next.js 16 App Router, React 19, TypeScript strict, TanStack Query v5, Vitest + Testing Library, `@livekit/components-react`.

**Spec:** `docs/superpowers/specs/2026-05-16-engine-failure-handling-design.md`

---

## Phase 0 — Cleanup prerequisites

### Task 0.1: Extract `TranscriptEntry` to a leaf module to fix the circular import

**Files:**
- Create: `backend/nexus/app/modules/interview_runtime/transcript_entry.py`
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py` (re-export for backward compat)
- Modify: `backend/nexus/tests/interview_runtime/test_schemas.py` (remove the workaround pre-import)
- Test: `backend/nexus/tests/interview_runtime/test_schemas.py` (re-run cleanly)

The current claim in `schemas.py` that "leaf-direct imports avoid the cycle" is wrong — `engine.models.__init__` still runs and imports `speaker`, which imports `TranscriptEntry` back. Moving `TranscriptEntry` into its own leaf module that engine.models can import without re-entering the runtime package's `__init__.py` is the actual fix.

- [ ] **Step 1: Confirm the cycle exists pre-change**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_schemas.py::TestQuestionKindField::test_question_kind_defaults_to_technical_depth -v` (with the workaround imports at the top of `test_schemas.py` temporarily commented out)

Expected: `ImportError: cannot import name 'TranscriptEntry' from partially initialized module 'app.modules.interview_runtime'`

Restore the workaround imports before proceeding to Step 2 — leaving them broken would block other tests in the same dir.

- [ ] **Step 2: Create the leaf module**

Create `backend/nexus/app/modules/interview_runtime/transcript_entry.py`:

```python
"""TranscriptEntry — extracted into a leaf module to break the circular
import between interview_runtime.schemas and engine.models.speaker.

interview_engine.models.speaker imports TranscriptEntry. Re-importing
from interview_runtime.schemas would re-enter the partially-initialized
package. Importing from this leaf module bypasses the cycle entirely.

interview_runtime.schemas re-exports TranscriptEntry for backward
compatibility — existing callers don't need to change.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TranscriptEntry(BaseModel):
    """A single utterance in the interview transcript."""

    role: Literal["agent", "candidate"]
    text: str
    timestamp_ms: int = Field(
        ge=0,
        description="Milliseconds since session start.",
    )
    question_id: str | None = None
```

- [ ] **Step 3: Update `schemas.py` to re-export from the leaf module**

Modify `backend/nexus/app/modules/interview_runtime/schemas.py`:

Replace the existing `TranscriptEntry` class definition (search for `class TranscriptEntry(BaseModel):` — it's roughly at line 257) with a re-export:

```python
# TranscriptEntry was moved to a leaf module in 2026-05-16 to break the
# circular import with engine.models.speaker. Re-exported here so existing
# callers (`from app.modules.interview_runtime.schemas import TranscriptEntry`)
# keep working.
from app.modules.interview_runtime.transcript_entry import TranscriptEntry  # noqa: F401
```

Verify that `engine.models.speaker.py` imports from the new path. Find the current import (`from app.modules.interview_runtime import TranscriptEntry` per the earlier ImportError trace) and change it to:

```python
from app.modules.interview_runtime.transcript_entry import TranscriptEntry
```

- [ ] **Step 4: Remove the workaround imports from test_schemas.py**

Modify `backend/nexus/tests/interview_runtime/test_schemas.py`:

Delete the three workaround imports added in the prior fix:

```python
# DELETE THESE LINES — no longer needed after the leaf-module extraction:
from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot  # noqa: F401
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot  # noqa: F401
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot  # noqa: F401
```

Keep the `pytest` and `app.modules.interview_runtime.schemas` imports as-is.

- [ ] **Step 5: Run the test file and a smoke test of the engine package**

```bash
docker compose run --rm nexus pytest tests/interview_runtime/test_schemas.py -v
docker compose run --rm nexus python -c "from app.modules.interview_engine.models.speaker import TranscriptEntry; print(TranscriptEntry.__module__)"
docker compose run --rm nexus pytest tests/interview_runtime/ -q
docker compose run --rm nexus pytest tests/interview_engine/ -q
```

Expected:
- `test_schemas.py`: 8 passed (5 existing + 3 from the prior fix)
- `python -c …`: prints `app.modules.interview_runtime.transcript_entry`
- `tests/interview_runtime/`: all green
- `tests/interview_engine/`: all green

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/transcript_entry.py backend/nexus/app/modules/interview_runtime/schemas.py backend/nexus/app/modules/interview_engine/models/speaker.py backend/nexus/tests/interview_runtime/test_schemas.py
git commit -m "$(cat <<'EOF'
refactor(interview_runtime): extract TranscriptEntry to leaf module

Breaks the circular import between interview_runtime.schemas and
engine.models.speaker. The previous "leaf-direct" claim in schemas.py
didn't avoid the cycle because Python still runs the package's __init__.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.2: Clean up the stuck dev session row

**Files:**
- Manual SQL (no migration; dev-only one-off documented in implementation PR description)

The session `c795c0b4-08eb-4939-ae6c-393ae19f651c` is stuck in `state='active'` after the 2026-05-16 incident. With the reaper landing in Phase 3, this would auto-resolve, but we clean it up explicitly now so it doesn't interfere with subsequent test runs.

- [ ] **Step 1: Confirm the row is present and stuck**

Run:

```bash
docker compose exec postgres psql -U postgres -d postgres -c "SELECT id, state, state_changed_at, started_at, agent_completed_at FROM sessions WHERE id = 'c795c0b4-08eb-4939-ae6c-393ae19f651c';"
```

Expected: one row, `state='active'`, no `agent_completed_at`.

If no row exists (already cleaned up by the user), skip the rest of this task.

- [ ] **Step 2: Manually transition the row**

Run:

```bash
docker compose exec postgres psql -U postgres -d postgres -c "UPDATE sessions SET state='cancelled', state_changed_at=NOW() WHERE id = 'c795c0b4-08eb-4939-ae6c-393ae19f651c' AND state='active';"
```

Note: using `cancelled` rather than `error` because the migration that adds the CHECK constraint on `error_code` lands in Phase 1 — at this point a literal `state='error'` row would mean we'd want an `error_code` set, which the production handler doesn't yet write. `cancelled` is a valid terminal state and matches the semantics ("this was abandoned, not a runtime error").

Expected output: `UPDATE 1`

- [ ] **Step 3: No commit needed (dev DB only)**

Document the manual SQL in the eventual implementation-PR description under a "Pre-existing data cleanup" section.

---

## Phase 1 — Backend foundation

### Task 1.1: Migration 0039 — CHECK constraint on `sessions.error_code`

**Files:**
- Create: `backend/nexus/migrations/versions/0039_session_error_code_check.py`

- [ ] **Step 1: Confirm head revision is 0038**

Run: `docker compose run --rm nexus alembic current`

Expected: `0038_advance_signals_confirmed_to_pipeline_built (head)`

If not, investigate before proceeding — `down_revision` must match the actual head.

- [ ] **Step 2: Write the migration**

Create `backend/nexus/migrations/versions/0039_session_error_code_check.py`:

```python
"""sessions.error_code CHECK constraint.

Pins error_code to the enumerated taxonomy defined in
app/modules/session/error_codes.py. The Literal there and the CHECK
here must move together — adding a value to one without the other
breaks INSERT/UPDATE.

Dev DB has zero non-null error_code rows at write time, so no backfill
needed.
"""
from __future__ import annotations

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sessions
          ADD CONSTRAINT sessions_error_code_check
          CHECK (
            error_code IS NULL OR error_code IN (
              'engine_session_config_invalid',
              'engine_company_profile_missing',
              'engine_question_bank_not_ready',
              'engine_room_join_failed',
              'engine_internal_error',
              'engine_unresponsive'
            )
          )
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN sessions.error_code IS
        'Coded reason for state=error. See app/modules/session/error_codes.py for taxonomy.'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_error_code_check")
    op.execute("COMMENT ON COLUMN sessions.error_code IS NULL")
```

- [ ] **Step 3: Apply the migration**

Run: `docker compose run --rm nexus alembic upgrade head`

Expected output ends with: `Running upgrade 0038 -> 0039, sessions.error_code CHECK constraint`

- [ ] **Step 4: Verify the constraint exists**

Run:

```bash
docker compose exec postgres psql -U postgres -d postgres -c "\d sessions" | grep -i error_code
```

Expected: column listed; output below the column list shows `"sessions_error_code_check" CHECK (...)`.

Also verify rejection:

```bash
docker compose exec postgres psql -U postgres -d postgres -c "UPDATE sessions SET error_code='not_a_valid_code' WHERE id IS NOT NULL;" 2>&1 | head -5
```

Expected: error mentioning `violates check constraint "sessions_error_code_check"`. Zero rows updated.

- [ ] **Step 5: Verify downgrade works**

Run: `docker compose run --rm nexus alembic downgrade -1 && docker compose run --rm nexus alembic upgrade head`

Expected: clean downgrade + re-upgrade, no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/migrations/versions/0039_session_error_code_check.py
git commit -m "$(cat <<'EOF'
migrate: 0039 — CHECK constraint on sessions.error_code

Pins error_code to the 6-value taxonomy defined in
app/modules/session/error_codes.py (next commit).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.2: `error_codes.py` — `ErrorCode` literal + `classify_engine_exception`

**Files:**
- Create: `backend/nexus/app/modules/session/error_codes.py`
- Test: `backend/nexus/tests/session/test_error_codes.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/session/test_error_codes.py`:

```python
"""Tests for app.modules.session.error_codes.classify_engine_exception."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
)
from app.modules.session.error_codes import classify_engine_exception


class _TinyModel(BaseModel):
    """Used to produce a real pydantic ValidationError for the test."""

    name: str = Field(min_length=5)


@pytest.mark.parametrize(
    ("exc_factory", "expected"),
    [
        (lambda: CompanyProfileMissingError("missing"), "engine_company_profile_missing"),
        (lambda: QuestionBankNotReadyError("not ready"), "engine_question_bank_not_ready"),
        # Real pydantic ValidationError — caught by module-path check, not by isinstance,
        # so classify_engine_exception doesn't have to import pydantic_core internals.
        (lambda: _force_validation_error(), "engine_session_config_invalid"),
        (lambda: RuntimeError("kaboom"), "engine_internal_error"),
        (lambda: ValueError("nope"), "engine_internal_error"),
    ],
)
def test_classify_engine_exception(exc_factory, expected):
    exc = exc_factory()
    assert classify_engine_exception(exc) == expected


def _force_validation_error():
    try:
        _TinyModel(name="x")
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("expected ValidationError to be raised")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/session/test_error_codes.py -v`

Expected: `ImportError: cannot import name 'classify_engine_exception' from 'app.modules.session.error_codes'` (the module doesn't exist yet).

- [ ] **Step 3: Write the minimal implementation**

Create `backend/nexus/app/modules/session/error_codes.py`:

```python
"""Error code taxonomy for engine-driven session failures.

The Literal values here are pinned by a CHECK constraint on
sessions.error_code (migration 0039). Adding a value requires:
  1. Update the Literal.
  2. Update the CHECK constraint via a new migration.
  3. Update the two frontend label maps:
     - frontend/session/components/interview/lib/session-error-messages.ts
     - frontend/app/components/dashboard/tracker/session-error-labels.ts
"""
from __future__ import annotations

from typing import Literal

from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
)

ErrorCode = Literal[
    "engine_session_config_invalid",
    "engine_company_profile_missing",
    "engine_question_bank_not_ready",
    "engine_room_join_failed",
    "engine_internal_error",
    "engine_unresponsive",
]


def classify_engine_exception(exc: BaseException) -> ErrorCode:
    """Map an exception raised during entrypoint to an ErrorCode.

    Order matters — more-specific types first. Default catch-all is
    engine_internal_error.

    Pydantic ValidationError is identified by module-path inspection
    rather than isinstance so this module doesn't have to import
    pydantic_core's internal class hierarchy (which differs between
    pydantic 2.x minor versions).
    """
    if isinstance(exc, CompanyProfileMissingError):
        return "engine_company_profile_missing"
    if isinstance(exc, QuestionBankNotReadyError):
        return "engine_question_bank_not_ready"
    if type(exc).__name__ == "ValidationError" and type(exc).__module__.startswith(
        "pydantic"
    ):
        return "engine_session_config_invalid"
    # TODO(verify-at-implementation): LiveKit ctx.connect() raises
    # ConnectError / asyncio.TimeoutError — once we observe the actual
    # exception types in dev, add an isinstance check that maps them to
    # engine_room_join_failed. Until then, those land in engine_internal_error.
    return "engine_internal_error"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/session/test_error_codes.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/session/error_codes.py backend/nexus/tests/session/test_error_codes.py
git commit -m "$(cat <<'EOF'
feat(session): error code taxonomy + classifier

ErrorCode Literal pinned by the migration-0039 CHECK constraint.
classify_engine_exception maps engine-raised exceptions to the
6-value enumeration the rest of the failure path consumes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.3: `transition_to_error()` service function

**Files:**
- Modify: `backend/nexus/app/modules/session/service.py` (append the new function)
- Test: `backend/nexus/tests/session/test_transition_to_error.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/session/test_transition_to_error.py`:

```python
"""Tests for transition_to_error — atomic state→error transition."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.session.models import Session as SessionRow
from app.modules.session.service import transition_to_error
from tests.conftest import seed_minimal_session  # see conftest helper in step 3


@pytest.mark.asyncio
async def test_active_to_error_returns_true_and_writes_audit(db_bypass):
    """state='active' → 'error' transition succeeds; audit row written."""
    session, tenant_id = await seed_minimal_session(db_bypass, state="active")

    won = await transition_to_error(
        db_bypass,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_internal_error",
        correlation_id="corr-1",
        reason="engine_entrypoint",
    )
    await db_bypass.commit()

    assert won is True

    refreshed = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_internal_error"

    audit = (await db_bypass.execute(
        select(AuditLog).where(
            AuditLog.resource == "session",
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalar_one()
    assert audit.payload["error_code"] == "engine_internal_error"
    assert audit.payload["reason"] == "engine_entrypoint"
    assert audit.payload["correlation_id"] == "corr-1"


@pytest.mark.asyncio
async def test_consented_to_error_succeeds(db_bypass):
    session, tenant_id = await seed_minimal_session(db_bypass, state="consented")

    won = await transition_to_error(
        db_bypass,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_session_config_invalid",
        correlation_id="corr-2",
        reason="engine_entrypoint",
    )
    await db_bypass.commit()

    assert won is True
    refreshed = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_session_config_invalid"


@pytest.mark.asyncio
async def test_completed_state_is_not_clobbered(db_bypass):
    """A completed session must NOT be transitioned to error (no-clobber)."""
    session, tenant_id = await seed_minimal_session(db_bypass, state="completed")

    won = await transition_to_error(
        db_bypass,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_internal_error",
        correlation_id="corr-3",
        reason="reaper",
    )
    await db_bypass.commit()

    assert won is False

    refreshed = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "completed"
    assert refreshed.error_code is None

    # No audit row written.
    rows = (await db_bypass.execute(
        select(AuditLog).where(
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_error_state_is_idempotent_noop(db_bypass):
    """Calling transition_to_error on an already-errored row is a clean no-op."""
    session, tenant_id = await seed_minimal_session(db_bypass, state="error")

    won = await transition_to_error(
        db_bypass,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_unresponsive",
        correlation_id="corr-4",
        reason="reaper",
    )
    await db_bypass.commit()

    assert won is False

    audit_count = (await db_bypass.execute(
        select(AuditLog).where(
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert audit_count == []
```

- [ ] **Step 2: Add a `seed_minimal_session` helper to conftest**

The helper inserts a minimal `sessions` row plus the parent assignment/candidate/job rows it needs to satisfy FKs. Place this in `backend/nexus/tests/conftest.py` near the other `seed_*` helpers:

```python
async def seed_minimal_session(
    db: AsyncSession,
    *,
    state: str = "active",
) -> tuple[Session, uuid.UUID]:
    """Insert a sessions row + its FK chain. Returns (session, tenant_id).

    Used by tests that only care about session state transitions and don't
    need real candidate/job data — the FK rows are minimal stubs.
    """
    tenant_id = await create_test_client(db)
    org_unit = await create_test_org_unit(db, tenant_id=tenant_id)
    job = await create_test_job(db, tenant_id=tenant_id, org_unit_id=org_unit.id)
    candidate = await create_test_candidate(db, tenant_id=tenant_id)
    assignment = await create_test_assignment(
        db, tenant_id=tenant_id, candidate_id=candidate.id, job_posting_id=job.id,
    )
    stage = await create_test_stage(db, tenant_id=tenant_id, job_posting_id=job.id)
    session = Session(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state=state,
        state_changed_at=datetime.now(UTC),
    )
    db.add(session)
    await db.flush()
    return session, tenant_id
```

If the helper functions called above (`create_test_client`, `create_test_org_unit`, etc.) don't exist or have different names, find the existing seed helpers in `tests/conftest.py` and `tests/interview_runtime/test_service.py` and use those instead. The goal is a minimal session row plus FKs.

- [ ] **Step 3: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/session/test_transition_to_error.py -v`

Expected: `ImportError: cannot import name 'transition_to_error' from 'app.modules.session.service'`

- [ ] **Step 4: Implement `transition_to_error`**

Append to `backend/nexus/app/modules/session/service.py`:

```python
# At the top of the file, ADD these imports if not already present:
from datetime import UTC, datetime
from typing import Literal
from sqlalchemy import update
from app.modules.audit import log_event
from app.modules.session.error_codes import ErrorCode
from app.modules.session.models import Session as SessionRow


async def transition_to_error(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    error_code: ErrorCode,
    correlation_id: str,
    reason: Literal["engine_entrypoint", "reaper"],
) -> bool:
    """Atomic state -> 'error' transition. Returns True if this call won.

    Gated on state IN ('consented', 'active') so we never clobber a
    completed / cancelled / already-error row. The boolean return lets
    the reaper distinguish 'I just claimed this stuck row' from
    'someone else transitioned it first.'

    Caller MUST be on a bypass-RLS session. Audit row is written through
    log_event in the same transaction; the caller commits.
    """
    now = datetime.now(UTC)
    res = await db.execute(
        update(SessionRow)
        .where(
            SessionRow.id == session_id,
            SessionRow.tenant_id == tenant_id,
            SessionRow.state.in_(["consented", "active"]),
        )
        .values(
            state="error",
            error_code=error_code,
            state_changed_at=now,
        )
    )
    if res.rowcount == 0:
        return False

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=None,
        actor_email=None,
        action="session.errored",
        resource="session",
        resource_id=session_id,
        payload={
            "error_code": error_code,
            "reason": reason,
            "correlation_id": correlation_id,
        },
    )
    return True
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/session/test_transition_to_error.py -v`

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/session/service.py backend/nexus/tests/session/test_transition_to_error.py backend/nexus/tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(session): transition_to_error — atomic state→error UPDATE + audit

Gated on state IN ('consented','active') so completed/cancelled rows
are never clobbered. Returns True on first transition, False on
subsequent calls (idempotent for the reaper's repeat-tick case).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Engine entrypoint handler

### Task 2.1: Refactor `entrypoint()` to extract `_run_entrypoint()`

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

This is **pure code motion** — no behavior change. The current `entrypoint()` body (everything from `async with get_bypass_session() as db:` down through `await session.start(...)`) moves into a new private async helper. Phase 2.2 then wraps the call site in try/except. Splitting the refactor from the wrap keeps the diff reviewable.

- [ ] **Step 1: Confirm existing engine tests pass**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -q`

Expected: all green. Record the count for comparison after the refactor.

- [ ] **Step 2: Extract `_run_entrypoint`**

Modify `backend/nexus/app/modules/interview_engine/agent.py:237 entrypoint(...)`. The new shape:

```python
@server.rtc_session(agent_name=settings.engine_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint."""
    metadata = json.loads(ctx.job.metadata or "{}")

    session_id_str = metadata["session_id"]
    tenant_id_str = metadata["tenant_id"]
    correlation_id = metadata.get("correlation_id", session_id_str)
    session_uuid = uuid.UUID(session_id_str)
    tenant_uuid = uuid.UUID(tenant_id_str)

    structlog.contextvars.bind_contextvars(
        session_id=session_id_str,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
    )
    log.info("engine.dispatch.received", agent_name=settings.engine_agent_name)

    await _run_entrypoint(ctx, session_uuid, tenant_uuid, correlation_id)


async def _run_entrypoint(
    ctx: JobContext,
    session_uuid: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """The body of the per-session entrypoint.

    Extracted from `entrypoint()` so Phase 2.2 can wrap the call site
    with a try/except. Pure code motion — no behavior change inside.
    """
    async with get_bypass_session() as db:
        session_config = await build_session_config(
            db,
            session_id=session_uuid,
            tenant_id=tenant_uuid,
        )
        tenant_settings = await get_tenant_settings(db, tenant_uuid)
    log.info(
        "engine.config.fetched",
        question_count=len(session_config.stage.questions),
        stage_type=session_config.stage.stage_type,
        candidate_name=session_config.candidate.name,
        job_title=session_config.job_title,
    )

    # … entire body from current line 268 (the ctx.connect() comment block)
    # through the final `await session.start(...)` call at the end of
    # the current entrypoint moves here verbatim …
```

When making this edit, keep the entire body word-for-word. Replace `tenant_id_str = metadata["tenant_id"]` references inside the body — `tenant_uuid` is now an argument; `tenant_id_str = str(tenant_uuid)` if any internal log statement still wants the string form.

The variable rename from `session_id = metadata["session_id"]` to `session_uuid` means any internal `session_id` references inside the body (there are several — e.g. in the `EventCollector(session_id=session_id, ...)` construction) need to consume `str(session_uuid)`. Easiest: add `session_id = str(session_uuid)` as the first line of `_run_entrypoint`'s body to preserve the existing internal name and avoid touching every reference.

- [ ] **Step 3: Run engine tests — same count as Step 1**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -q`

Expected: same number of tests pass as Step 1. If a test fails, this is a code-motion regression — diff the touched function carefully.

- [ ] **Step 4: Run the broader test suite to catch import-time regressions**

Run: `docker compose run --rm nexus pytest tests/ -q --ignore=tests/interview_engine -x --tb=short` (the `-x` stops on first failure; we want to spot accidental import breakage).

If green, proceed. If not, the refactor introduced an import-time error — revisit Step 2.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
refactor(interview_engine): extract _run_entrypoint from entrypoint()

Pure code motion. The body of entrypoint() moves into a private async
helper so the next commit can wrap the call site in a failure handler.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.2: Add `_handle_entrypoint_failure()` and wrap the call site

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

- [ ] **Step 1: Add the handler + the helper for outcome publish**

Modify `backend/nexus/app/modules/interview_engine/agent.py`. Add these near the bottom of the file (before any other private helpers), and update `entrypoint()` to call them:

First, update the top-of-file imports:

```python
# ADD these to the existing import block:
from app.modules.session.error_codes import classify_engine_exception
from app.modules.session.service import transition_to_error
```

Then add the helpers near the other `_handle_*` helpers:

```python
async def _handle_entrypoint_failure(
    *,
    exc: Exception,
    ctx: JobContext,
    session_id: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """Single failure handler for every pre-session.start crash path.

    Order:
      1. Classify exception -> ErrorCode.
      2. Transition session row to state='error' (durable truth).
      3. Best-effort publish session_outcome='error' to the LK room.

    DB transition is first so the candidate's HTTP fallback poll wins
    even if the room/attribute publish fails. The caller re-raises so
    LiveKit's existing crash signal is preserved.
    """
    error_code = classify_engine_exception(exc)
    log.error(
        "engine.entrypoint.failed",
        error_code=error_code,
        error_type=type(exc).__name__,
        error=str(exc),
    )

    async with get_bypass_session() as db:
        await transition_to_error(
            db,
            session_id=session_id,
            tenant_id=tenant_uuid,
            error_code=error_code,
            correlation_id=correlation_id,
            reason="engine_entrypoint",
        )
        await db.commit()

    await _best_effort_publish_outcome_attribute(ctx)


async def _best_effort_publish_outcome_attribute(ctx: JobContext) -> None:
    """Publish session_outcome='error' to the LK room if at all possible.

    Tries connecting first — the failure may have happened before
    ctx.connect() ran. Swallows every exception (logged at warning):
    if we can't publish the attribute, the candidate's HTTP-poll
    fallback path will surface the failure.
    """
    try:
        if not ctx.room.isconnected():
            await ctx.connect()
        await ctx.room.local_participant.set_attributes(
            {"session_outcome": "error"},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "engine.entrypoint.outcome_publish_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
```

Then wrap the `_run_entrypoint(...)` call in `entrypoint()`:

```python
@server.rtc_session(agent_name=settings.engine_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint."""
    metadata = json.loads(ctx.job.metadata or "{}")

    session_id_str = metadata["session_id"]
    tenant_id_str = metadata["tenant_id"]
    correlation_id = metadata.get("correlation_id", session_id_str)
    session_uuid = uuid.UUID(session_id_str)
    tenant_uuid = uuid.UUID(tenant_id_str)

    structlog.contextvars.bind_contextvars(
        session_id=session_id_str,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
    )
    log.info("engine.dispatch.received", agent_name=settings.engine_agent_name)

    try:
        await _run_entrypoint(ctx, session_uuid, tenant_uuid, correlation_id)
    except Exception as exc:
        await _handle_entrypoint_failure(
            exc=exc,
            ctx=ctx,
            session_id=session_uuid,
            tenant_uuid=tenant_uuid,
            correlation_id=correlation_id,
        )
        raise  # preserves LiveKit's existing "job crashed" log
```

- [ ] **Step 2: Run engine tests**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -q`

Expected: same green count as before. The handler isn't invoked on the success path; existing tests should remain unchanged.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(interview_engine): durable failure handler for entrypoint crashes

Wraps _run_entrypoint() in a try/except that transitions the session row
to state='error' with a coded error_code, writes an audit row, and
best-effort publishes session_outcome='error' to the LK room. Re-raises
to preserve LiveKit's existing job-crashed log.

Closes the silent-failure class observed in the 2026-05-16 incident
(session c795c0b4-08eb-…).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.3: Regression test for entrypoint failure

**Files:**
- Test: `backend/nexus/tests/interview_engine/test_entrypoint_failure.py`

This is the highest-leverage test in the plan — it makes the original bug class non-recurring.

- [ ] **Step 1: Write the failing test (it'll pass once Task 2.2 is in place — we run it to confirm)**

Create `backend/nexus/tests/interview_engine/test_entrypoint_failure.py`:

```python
"""Regression tests for the engine entrypoint failure handler.

Verifies that an uncaught exception inside _run_entrypoint produces:
  1. A DB transition to state='error' with the right error_code.
  2. An audit row recording the failure.
  3. A best-effort session_outcome='error' attribute publish.

The original incident (2026-05-16, session c795c0b4-08eb-…) was a
pydantic ValidationError raised by build_session_config that crashed
the engine silently. This test fixes that bug class as
non-regressable.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.interview_engine.agent import (
    _handle_entrypoint_failure,
    _best_effort_publish_outcome_attribute,
)
from app.modules.session.models import Session as SessionRow
from tests.conftest import seed_minimal_session


class _StubModel(BaseModel):
    name: str = Field(min_length=5)


def _make_validation_error() -> Exception:
    try:
        _StubModel(name="x")
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("expected ValidationError")


def _fake_job_context() -> MagicMock:
    """A minimal stand-in for livekit.agents.JobContext."""
    ctx = MagicMock()
    ctx.room = MagicMock()
    ctx.room.isconnected = MagicMock(return_value=True)
    ctx.room.local_participant.set_attributes = AsyncMock()
    ctx.connect = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_validation_error_during_build_session_config(
    db_bypass, monkeypatch,
):
    """The actual 2026-05-16 bug shape — durable failure path engaged."""
    session, tenant_id = await seed_minimal_session(db_bypass, state="active")
    await db_bypass.commit()

    ctx = _fake_job_context()

    await _handle_entrypoint_failure(
        exc=_make_validation_error(),
        ctx=ctx,
        session_id=session.id,
        tenant_uuid=tenant_id,
        correlation_id="corr-vald",
    )

    refreshed = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_session_config_invalid"

    audit = (await db_bypass.execute(
        select(AuditLog).where(
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalar_one()
    assert audit.payload["error_code"] == "engine_session_config_invalid"
    assert audit.payload["reason"] == "engine_entrypoint"
    assert audit.payload["correlation_id"] == "corr-vald"

    ctx.room.local_participant.set_attributes.assert_awaited_once_with(
        {"session_outcome": "error"}
    )


@pytest.mark.asyncio
async def test_outcome_publish_failure_is_swallowed(monkeypatch):
    """If set_attributes raises, the handler logs and moves on — does NOT propagate."""
    ctx = _fake_job_context()
    ctx.room.local_participant.set_attributes.side_effect = RuntimeError("LK boom")

    # Must not raise.
    await _best_effort_publish_outcome_attribute(ctx)


@pytest.mark.asyncio
async def test_pre_connect_failure_still_writes_db_row(db_bypass):
    """Handler runs DB transition even when ctx.connect() raises later."""
    session, tenant_id = await seed_minimal_session(db_bypass, state="consented")
    await db_bypass.commit()

    ctx = _fake_job_context()
    ctx.room.isconnected.return_value = False
    ctx.connect.side_effect = RuntimeError("no router to room")

    await _handle_entrypoint_failure(
        exc=RuntimeError("connect failed upstream"),
        ctx=ctx,
        session_id=session.id,
        tenant_uuid=tenant_id,
        correlation_id="corr-conn",
    )

    refreshed = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_internal_error"
    # set_attributes was never reached because connect() raised first.
    ctx.room.local_participant.set_attributes.assert_not_awaited()
```

- [ ] **Step 2: Run the tests**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_entrypoint_failure.py -v`

Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/interview_engine/test_entrypoint_failure.py
git commit -m "$(cat <<'EOF'
test(interview_engine): regression test for entrypoint failure handler

Locks down the durable-failure-path contract for the bug class
observed in the 2026-05-16 incident: any exception during
_run_entrypoint must transition the DB row, write audit, and
best-effort publish the outcome attribute.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Reaper

### Task 3.1: Reaper settings + apscheduler dependency

**Files:**
- Modify: `backend/nexus/pyproject.toml` (add apscheduler dep)
- Modify: `backend/nexus/app/config.py` (add reaper settings)
- Modify: `backend/nexus/.env.example` (document the three knobs)

- [ ] **Step 1: Add the apscheduler dependency**

In `backend/nexus/pyproject.toml`, find the `[project] dependencies = [...]` block and add:

```toml
"apscheduler>=3.10,<4",
```

apscheduler 3.x is stable and battle-tested; 4.x is a major rewrite still in beta — pin to 3.x for now.

Run: `docker compose run --rm nexus uv sync` (or whatever's the standard dep-install command in this repo).

- [ ] **Step 2: Add reaper settings**

In `backend/nexus/app/config.py`, append to the `Settings` class (look for the other engine_* fields and place these near them):

```python
    # Stuck-session reaper. Sweeps state='active' sessions whose
    # state_changed_at is older than reaper_stuck_threshold_seconds and
    # transitions them to state='error' with error_code='engine_unresponsive'.
    # Disabled by setting reaper_enabled=false (tests do this).
    reaper_enabled: bool = True
    reaper_interval_seconds: int = 300   # how often the scheduler ticks
    reaper_stuck_threshold_seconds: int = 900  # 15 min — typical AI screen is 30 min
```

- [ ] **Step 3: Document the knobs**

In `backend/nexus/.env.example`, append a new section:

```bash
# Stuck-session reaper.
# Disable in tests / for local dev that doesn't want wallclock-driven sweeps.
# REAPER_ENABLED=true
# REAPER_INTERVAL_SECONDS=300         # how often the scheduler ticks
# REAPER_STUCK_THRESHOLD_SECONDS=900  # 15 min idle on state='active' -> error
```

- [ ] **Step 4: Sanity-check config loads**

Run: `docker compose run --rm nexus python -c "from app.config import settings; print('reaper:', settings.reaper_enabled, settings.reaper_interval_seconds, settings.reaper_stuck_threshold_seconds)"`

Expected: `reaper: True 300 900`

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/pyproject.toml backend/nexus/uv.lock backend/nexus/app/config.py backend/nexus/.env.example
git commit -m "$(cat <<'EOF'
feat(config): add reaper settings + apscheduler dependency

Three knobs: reaper_enabled (default True), reaper_interval_seconds
(default 300), reaper_stuck_threshold_seconds (default 900). The
reaper module itself lands in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.2: `reaper.py` module + tests

**Files:**
- Create: `backend/nexus/app/modules/session/reaper.py`
- Test: `backend/nexus/tests/session/test_reaper.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/session/test_reaper.py`:

```python
"""Tests for the stuck-session reaper."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.session.models import Session as SessionRow
from app.modules.session.reaper import run_stuck_session_reaper
from tests.conftest import seed_minimal_session


@pytest.mark.asyncio
async def test_only_stuck_active_sessions_transition(db_bypass, monkeypatch):
    """state='active' past threshold -> error. Within threshold stays active.
    Completed stays completed.
    """
    # Backdate by 20 minutes (threshold is 15).
    past = datetime.now(UTC) - timedelta(minutes=20)
    recent = datetime.now(UTC) - timedelta(minutes=5)

    stuck, t_stuck = await seed_minimal_session(db_bypass, state="active")
    stuck.state_changed_at = past

    fresh, t_fresh = await seed_minimal_session(db_bypass, state="active")
    fresh.state_changed_at = recent

    done, t_done = await seed_minimal_session(db_bypass, state="completed")
    done.state_changed_at = past

    await db_bypass.commit()

    await run_stuck_session_reaper()

    refreshed_stuck = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == stuck.id)
    )).scalar_one()
    refreshed_fresh = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == fresh.id)
    )).scalar_one()
    refreshed_done = (await db_bypass.execute(
        select(SessionRow).where(SessionRow.id == done.id)
    )).scalar_one()

    assert refreshed_stuck.state == "error"
    assert refreshed_stuck.error_code == "engine_unresponsive"
    assert refreshed_fresh.state == "active"  # within threshold
    assert refreshed_done.state == "completed"


@pytest.mark.asyncio
async def test_reaper_writes_audit_row(db_bypass):
    past = datetime.now(UTC) - timedelta(minutes=20)
    stuck, tenant_id = await seed_minimal_session(db_bypass, state="active")
    stuck.state_changed_at = past
    await db_bypass.commit()

    await run_stuck_session_reaper()

    audit = (await db_bypass.execute(
        select(AuditLog).where(
            AuditLog.resource_id == stuck.id,
            AuditLog.action == "session.errored",
        )
    )).scalar_one()
    assert audit.payload["error_code"] == "engine_unresponsive"
    assert audit.payload["reason"] == "reaper"


@pytest.mark.asyncio
async def test_reaper_is_idempotent_across_back_to_back_runs(db_bypass):
    past = datetime.now(UTC) - timedelta(minutes=20)
    stuck, _ = await seed_minimal_session(db_bypass, state="active")
    stuck.state_changed_at = past
    await db_bypass.commit()

    await run_stuck_session_reaper()
    await run_stuck_session_reaper()  # second tick — no-op

    audit_rows = (await db_bypass.execute(
        select(AuditLog).where(
            AuditLog.resource_id == stuck.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert len(audit_rows) == 1  # only the first tick wrote the audit


@pytest.mark.asyncio
async def test_concurrent_reapers_advisory_lock(db_bypass):
    """Two concurrent run_stuck_session_reaper calls — only one sweeps."""
    past = datetime.now(UTC) - timedelta(minutes=20)
    stuck, _ = await seed_minimal_session(db_bypass, state="active")
    stuck.state_changed_at = past
    await db_bypass.commit()

    # Run both concurrently — the second should hit the lock-contention
    # branch and return immediately without writing.
    await asyncio.gather(
        run_stuck_session_reaper(),
        run_stuck_session_reaper(),
    )

    audit_rows = (await db_bypass.execute(
        select(AuditLog).where(
            AuditLog.resource_id == stuck.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert len(audit_rows) == 1  # only one sweep, only one audit
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/session/test_reaper.py -v`

Expected: `ImportError: cannot import name 'run_stuck_session_reaper' from 'app.modules.session.reaper'`

- [ ] **Step 3: Implement the reaper**

Create `backend/nexus/app/modules/session/reaper.py`:

```python
"""Stuck-session reaper.

Single-flight via pg_try_advisory_lock — concurrent ticks across replicas
return immediately on lock contention. Targets state='active' rows whose
state_changed_at is older than reaper_stuck_threshold_seconds AND have no
agent_completed_at — the empirical signature of an engine that died
without ever transitioning the session itself.

The in-process entrypoint handler (Phase 2) covers the happy-error path
where the engine catches its own exception. This reaper covers the cases
the in-process handler can't: SIGKILL/OOM/process crash before the
try/except runs, LK Cloud dispatch never arriving, etc.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy import text as sql_text

from app.config import settings
from app.database import get_bypass_session
from app.modules.session.models import Session as SessionRow
from app.modules.session.service import transition_to_error

log = structlog.get_logger("session.reaper")

_REAPER_LOCK_KEY = "stuck_session_reaper"


async def run_stuck_session_reaper() -> None:
    """One tick of the stuck-session sweeper."""
    async with get_bypass_session() as db:
        acquired = (
            await db.execute(
                sql_text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": _REAPER_LOCK_KEY},
            )
        ).scalar_one()
        if not acquired:
            log.debug("reaper.lock.contended")
            return

        try:
            cutoff = datetime.now(UTC) - timedelta(
                seconds=settings.reaper_stuck_threshold_seconds
            )
            stuck = (
                await db.execute(
                    select(SessionRow.id, SessionRow.tenant_id).where(
                        SessionRow.state == "active",
                        SessionRow.state_changed_at < cutoff,
                        SessionRow.agent_completed_at.is_(None),
                    )
                )
            ).all()

            transitioned = 0
            for row in stuck:
                won = await transition_to_error(
                    db,
                    session_id=row.id,
                    tenant_id=row.tenant_id,
                    error_code="engine_unresponsive",
                    correlation_id=f"reaper-{row.id}",
                    reason="reaper",
                )
                if won:
                    transitioned += 1
            await db.commit()

            log.info(
                "reaper.tick",
                stuck_found=len(stuck),
                transitioned=transitioned,
                threshold_seconds=settings.reaper_stuck_threshold_seconds,
            )
        finally:
            await db.execute(
                sql_text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": _REAPER_LOCK_KEY},
            )
            await db.commit()
```

- [ ] **Step 4: Run the tests**

Run: `docker compose run --rm nexus pytest tests/session/test_reaper.py -v`

Expected: 4 passed.

If the concurrency test (`test_concurrent_reapers_advisory_lock`) flakes, increase the test threshold or sequence the calls deliberately — advisory-lock ordering in asyncio.gather is not strictly deterministic. The invariant is "only one wrote the audit," not "which one wrote it."

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/session/reaper.py backend/nexus/tests/session/test_reaper.py
git commit -m "$(cat <<'EOF'
feat(session): stuck-session reaper

Sweeps state='active' rows older than reaper_stuck_threshold_seconds
and transitions them to state='error' with
error_code='engine_unresponsive'. Single-flight via
pg_try_advisory_lock so multi-replica deployments don't double-sweep.

Catches the SIGKILL/OOM/never-dispatched cases the in-process
entrypoint handler can't.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.3: Wire the scheduler into the FastAPI lifespan

**Files:**
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Add the import and scheduler wiring**

In `backend/nexus/app/main.py`, add at the top:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.modules.session.reaper import run_stuck_session_reaper
```

Then modify the `lifespan(app: FastAPI)` function (around line 195) — extend it without changing the existing startup ordering. Add the scheduler wiring AFTER `_assert_rls_completeness()` and BEFORE the `yield`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # … existing startup logic up through _assert_rls_completeness …
    await _assert_rls_completeness()

    # Stuck-session reaper. AsyncIO-scheduled, single-flight via pg advisory
    # lock (see app/modules/session/reaper.py). max_instances=1 + coalesce=True
    # protects against the rare "tick fired while previous tick still running"
    # case (e.g. very slow DB).
    scheduler: AsyncIOScheduler | None = None
    if settings.reaper_enabled:
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            run_stuck_session_reaper,
            trigger="interval",
            seconds=settings.reaper_interval_seconds,
            id="stuck_session_reaper",
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        app.state.reaper_scheduler = scheduler
        log.info(
            "reaper.scheduler.started",
            interval_seconds=settings.reaper_interval_seconds,
            stuck_threshold_seconds=settings.reaper_stuck_threshold_seconds,
        )

    try:
        yield
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("reaper.scheduler.stopped")
```

If `log` isn't already in scope at module level in `main.py`, the existing imports + structlog setup will reveal the correct logger name — match the existing logging idiom in the same file (search for `structlog.get_logger` to confirm).

- [ ] **Step 2: Sanity-check startup**

Run: `docker compose up --build nexus -d && docker compose logs --tail=50 nexus`

Expected log line: `reaper.scheduler.started interval_seconds=300 stuck_threshold_seconds=900`

Tear it down: `docker compose down`

- [ ] **Step 3: Verify graceful shutdown**

Run: `docker compose up nexus -d && sleep 5 && docker compose stop nexus && docker compose logs --tail=20 nexus | grep -i reaper`

Expected: `reaper.scheduler.stopped` appears in the shutdown logs.

- [ ] **Step 4: Run the full backend test suite**

Run: `docker compose run --rm -e REAPER_ENABLED=false nexus pytest tests/ -q`

Expected: all green. The `REAPER_ENABLED=false` env disables the wallclock-driven sweeper for the test process so it doesn't interleave with the reaper-specific tests' setup.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/main.py
git commit -m "$(cat <<'EOF'
feat(main): wire the stuck-session reaper into FastAPI lifespan

AsyncIOScheduler with one job (run_stuck_session_reaper, interval =
reaper_interval_seconds). max_instances=1 + coalesce=True guards
against tick-overrun. Disabled via REAPER_ENABLED=false in tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — Candidate state endpoint

### Task 4.1: Schema + endpoint

**Files:**
- Modify: `backend/nexus/app/modules/session/schemas.py` (add response model)
- Modify: `backend/nexus/app/modules/session/router.py` (add endpoint)

- [ ] **Step 1: Add the response schema**

In `backend/nexus/app/modules/session/schemas.py`, append:

```python
class CandidateSessionStateResponse(BaseModel):
    """Minimal state snapshot for the candidate's fallback poll.

    No PII — only the state machine values + error code + timestamp.
    """
    model_config = ConfigDict(from_attributes=True)
    state: SessionState
    error_code: str | None
    state_changed_at: datetime
```

The `error_code` is typed as `str | None` (not the `ErrorCode` Literal from
`error_codes.py`) so the frontend can receive a forward-compatible string
in case backend rolls a new code before frontend ships support — same
pattern the FE's `isSessionOutcome` guard uses for version skew.

- [ ] **Step 2: Add the endpoint**

In `backend/nexus/app/modules/session/router.py`, append a new route to
`candidate_session_router` (after the existing `/rejoin` route, before
the `session_router` block):

```python
@candidate_session_router.get("/state", response_model=CandidateSessionStateResponse)
async def get_candidate_session_state_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> CandidateSessionStateResponse:
    """Minimal state read for the candidate's fallback poll.

    Auth: candidate JWT in path (verified by middleware). Tenant-scoped
    via the verified token's claims. Returns state + error_code only —
    no transcript, no questions, no PII.

    Rate-limited at 12/min/IP and 12/min/token (declared in proxy/rate
    limiter config — root CLAUDE.md "Rate Limiting & Abuse Posture").
    """
    session_id = _candidate_session_id(request)
    tenant_id = uuid.UUID(request.state.candidate_token_payload["tenant_id"])
    row = (
        await db.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Same opacity as build_session_config — cross-tenant token sees
        # the same 404 as a never-existed session.
        raise HTTPException(status_code=404, detail="session_not_found")
    return CandidateSessionStateResponse.model_validate(row)
```

The `_candidate_session_id(request)` helper already exists in router.py at line 46; the
import for `SessionRow` and `select` may need to be added at the top of the file —
follow the existing import block.

If `Depends(get_bypass_db)` isn't the standard dep here (some files use
`get_tenant_db`), match what the other candidate-session endpoints in the
same file use. The candidate flow uses `get_bypass_db` because the request
has no recruiter auth context.

- [ ] **Step 3: Smoke-check the endpoint loads**

Run: `docker compose run --rm nexus python -c "from app.modules.session.router import candidate_session_router; routes = [r.path for r in candidate_session_router.routes]; print('\\n'.join(routes))"`

Expected output includes `/api/candidate-session/{token}/state`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/session/schemas.py backend/nexus/app/modules/session/router.py
git commit -m "$(cat <<'EOF'
feat(session): GET /api/candidate-session/{token}/state

Minimal state snapshot for the candidate frontend's fallback poll.
Returns state + error_code + state_changed_at — no PII, no transcript.

Used by frontend/session's useSessionStateFallback hook when the
engine's LK-attribute publish never lands (pre-room-connect failures).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4.2: Endpoint tests

**Files:**
- Test: `backend/nexus/tests/session/test_candidate_state_endpoint.py`

- [ ] **Step 1: Write the tests**

Create `backend/nexus/tests/session/test_candidate_state_endpoint.py`:

```python
"""Tests for GET /api/candidate-session/{token}/state."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.modules.session.models import Session as SessionRow
from tests.conftest import (
    mint_candidate_session_token,
    seed_minimal_session,
)


@pytest.mark.asyncio
async def test_state_happy_path(client: AsyncClient, db_bypass):
    session, tenant_id = await seed_minimal_session(db_bypass, state="active")
    await db_bypass.commit()

    token = mint_candidate_session_token(
        session_id=session.id, tenant_id=tenant_id,
    )
    resp = await client.get(f"/api/candidate-session/{token}/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "active"
    assert body["error_code"] is None


@pytest.mark.asyncio
async def test_state_after_transition_to_error(client: AsyncClient, db_bypass):
    session, tenant_id = await seed_minimal_session(db_bypass, state="active")
    # Manually transition (we have full control here).
    session.state = "error"
    session.error_code = "engine_session_config_invalid"
    await db_bypass.commit()

    token = mint_candidate_session_token(
        session_id=session.id, tenant_id=tenant_id,
    )
    resp = await client.get(f"/api/candidate-session/{token}/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "error"
    assert body["error_code"] == "engine_session_config_invalid"


@pytest.mark.asyncio
async def test_state_cross_tenant_returns_404(client: AsyncClient, db_bypass):
    """Token with mismatched tenant must NOT see the session (404, not 401)."""
    session, real_tenant = await seed_minimal_session(db_bypass, state="active")
    await db_bypass.commit()

    wrong_tenant = uuid.uuid4()
    token = mint_candidate_session_token(
        session_id=session.id, tenant_id=wrong_tenant,
    )
    resp = await client.get(f"/api/candidate-session/{token}/state")
    assert resp.status_code in (401, 404)  # token might fail signature check first
```

The helper `mint_candidate_session_token` may not exist; if so, port the candidate-JWT-minting code from existing session tests (`tests/test_middleware_candidate_single_use.py` is the canonical reference per CLAUDE.md). Place the helper next to `seed_minimal_session` in `conftest.py`.

- [ ] **Step 2: Run the tests**

Run: `docker compose run --rm nexus pytest tests/session/test_candidate_state_endpoint.py -v`

Expected: 3 passed.

If the cross-tenant test returns 401 (the middleware rejects the token before reaching the handler), the test's assertion (`in (401, 404)`) covers both shapes — the security invariant is "no information leakage about whether the session exists," and 401 satisfies that.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/session/test_candidate_state_endpoint.py backend/nexus/tests/conftest.py
git commit -m "$(cat <<'EOF'
test(session): cover /api/candidate-session/{token}/state endpoint

Happy path, error-state read-back, cross-tenant denial. The last
asserts no information leakage about whether the session exists.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — Frontend session: error UX

### Task 5.1: API client `getState()`

**Files:**
- Modify: `frontend/session/lib/api/candidate-session.ts`

- [ ] **Step 1: Add the type + method**

In `frontend/session/lib/api/candidate-session.ts`, find the existing types block (PreCheckResponse etc.) and add:

```typescript
export interface CandidateSessionState {
  state: 'created' | 'pre_check' | 'consented' | 'active' | 'completed' | 'cancelled' | 'error'
  error_code: string | null
  state_changed_at: string  // ISO-8601
}
```

Then add the method to `candidateSessionApi`:

```typescript
export const candidateSessionApi = {
  // … existing methods …

  /**
   * Minimal state snapshot for the post-/start fallback poll. Used by
   * useSessionStateFallback to surface engine failures that crashed
   * before publishing the session_outcome LK room attribute.
   */
  async getState(token: string): Promise<CandidateSessionState> {
    return fetchJson(`/api/candidate-session/${token}/state`)
  },
}
```

Match the existing `fetchJson` wrapper style — if methods use `apiFetch<T>` or some other helper, copy that idiom.

- [ ] **Step 2: Type-check**

Run: `cd frontend/session && npm run type-check`

Expected: clean (no errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/session/lib/api/candidate-session.ts
git commit -m "$(cat <<'EOF'
feat(session-api): getState() for the post-/start fallback poll

Returns state + error_code; consumed by useSessionStateFallback
(next commit) to surface engine failures the LK-attribute path
can't see (pre-room-connect crashes).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5.2: Error-code copy map

**Files:**
- Create: `frontend/session/components/interview/lib/session-error-messages.ts`

- [ ] **Step 1: Write the file**

Create `frontend/session/components/interview/lib/session-error-messages.ts`:

```typescript
/**
 * Candidate-facing copy for each backend error_code.
 *
 * Backend taxonomy lives in
 * `backend/nexus/app/modules/session/error_codes.py::ErrorCode`. Adding a
 * code there requires adding an entry here in the same PR — the FALLBACK
 * below is the safety net for forward-compat (backend rolls a new code
 * before this frontend ships support).
 */

interface ErrorCopy {
  headline: string
  body: string
}

export const SESSION_ERROR_COPY: Record<string, ErrorCopy> = {
  engine_session_config_invalid: {
    headline: 'We hit a configuration issue',
    body: "Your interview couldn't be set up correctly. Your recruiter has been notified and will send a new invite.",
  },
  engine_company_profile_missing: {
    headline: "Your interview isn't fully set up",
    body: 'Some company information is missing. Your recruiter will reach out shortly.',
  },
  engine_question_bank_not_ready: {
    headline: "Your interview isn't fully set up",
    body: "The questions for this interview aren't ready yet. Your recruiter will reach out shortly.",
  },
  engine_room_join_failed: {
    headline: 'Something went wrong on our side',
    body: "We couldn't connect to your interview room. Your recruiter will resend the invite.",
  },
  engine_internal_error: {
    headline: 'Something went wrong on our side',
    body: 'Your recruiter has been notified and will resend the invite.',
  },
  engine_unresponsive: {
    headline: "Your interview didn't start",
    body: 'The interview was abandoned without progress. Your recruiter will reach out to reschedule.',
  },
}

const FALLBACK: ErrorCopy = {
  headline: 'Something went wrong',
  body: 'Your recruiter will be in touch with next steps.',
}

export function copyForErrorCode(code: string | null | undefined): ErrorCopy {
  if (!code) return FALLBACK
  return SESSION_ERROR_COPY[code] ?? FALLBACK
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend/session && npm run type-check`

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/session/components/interview/lib/session-error-messages.ts
git commit -m "$(cat <<'EOF'
feat(session-fe): error-code copy map for candidate-facing screen

Six entries matching the backend ErrorCode taxonomy plus a FALLBACK
for forward-compat (backend rolls new code before FE ships support).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5.3: `useSessionStateFallback` hook

**Files:**
- Create: `frontend/session/components/interview/app/hooks/use-session-state-fallback.ts`
- Test: `frontend/session/tests/hooks/use-session-state-fallback.test.ts`

- [ ] **Step 1: Write the test**

Create `frontend/session/tests/hooks/use-session-state-fallback.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

import { useSessionStateFallback } from '@/components/interview/app/hooks/use-session-state-fallback'
import { candidateSessionApi } from '@/lib/api/candidate-session'

vi.mock('@/lib/api/candidate-session')

const mockedGetState = vi.mocked(candidateSessionApi.getState)

beforeEach(() => {
  vi.useFakeTimers()
  mockedGetState.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useSessionStateFallback', () => {
  it('polls every 5 seconds while enabled', async () => {
    mockedGetState.mockResolvedValue({
      state: 'active',
      error_code: null,
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    renderHook(() => useSessionStateFallback('tok-1', true))

    // First tick is immediate.
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(1))

    vi.advanceTimersByTime(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(2))

    vi.advanceTimersByTime(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(3))
  })

  it('stops polling once a terminal state is seen', async () => {
    mockedGetState
      .mockResolvedValueOnce({
        state: 'active',
        error_code: null,
        state_changed_at: '2026-05-16T12:00:00Z',
      })
      .mockResolvedValueOnce({
        state: 'error',
        error_code: 'engine_internal_error',
        state_changed_at: '2026-05-16T12:00:05Z',
      })

    renderHook(() => useSessionStateFallback('tok-1', true))

    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(1))
    vi.advanceTimersByTime(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(2))

    // Should NOT call again — terminal state stops the loop.
    vi.advanceTimersByTime(5000)
    expect(mockedGetState).toHaveBeenCalledTimes(2)
  })

  it('surfaces error_code from the response', async () => {
    mockedGetState.mockResolvedValue({
      state: 'error',
      error_code: 'engine_session_config_invalid',
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    const { result } = renderHook(() => useSessionStateFallback('tok-1', true))
    await waitFor(() => {
      expect(result.current?.state).toBe('error')
      expect(result.current?.error_code).toBe('engine_session_config_invalid')
    })
  })

  it('keeps polling through network errors', async () => {
    mockedGetState
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({
        state: 'active',
        error_code: null,
        state_changed_at: '2026-05-16T12:00:05Z',
      })

    renderHook(() => useSessionStateFallback('tok-1', true))

    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(1))
    vi.advanceTimersByTime(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(2))
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend/session && npm run test -- tests/hooks/use-session-state-fallback.test.ts`

Expected: `Cannot find module '@/components/interview/app/hooks/use-session-state-fallback'`

- [ ] **Step 3: Write the hook**

Create `frontend/session/components/interview/app/hooks/use-session-state-fallback.ts`:

```typescript
'use client'

import { useEffect, useState } from 'react'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import type { CandidateSessionState } from '@/lib/api/candidate-session'

const POLL_INTERVAL_MS = 5000
const TERMINAL_STATES = new Set(['error', 'completed', 'cancelled'])

/**
 * Polls `/api/candidate-session/{token}/state` every 5s while enabled.
 *
 * Stops polling once a terminal state is observed (error / completed /
 * cancelled). Mirrors the engine's `session_outcome` room attribute path
 * for the case where the engine crashed before publishing the attribute
 * (pre-room-connect failures). Network errors keep the loop alive —
 * transient failures shouldn't blind the candidate to a real error.
 *
 * Used in concert with useSessionOutcome; OutcomeWatcher renders the
 * error screen from whichever surfaces a terminal value first.
 */
export function useSessionStateFallback(
  token: string,
  enabled: boolean,
): CandidateSessionState | null {
  const [state, setState] = useState<CandidateSessionState | null>(null)

  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      if (stopped) return
      try {
        const next = await candidateSessionApi.getState(token)
        if (stopped) return
        setState(next)
        if (TERMINAL_STATES.has(next.state)) {
          return  // terminal — exit the loop
        }
      } catch {
        // Network/transient — keep polling. 4xx (e.g. token superseded)
        // is surfaced by the existing token-error landing path elsewhere.
      }
      if (!stopped) {
        timer = setTimeout(tick, POLL_INTERVAL_MS)
      }
    }

    tick()

    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [token, enabled])

  return state
}
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend/session && npm run test -- tests/hooks/use-session-state-fallback.test.ts`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/session/components/interview/app/hooks/use-session-state-fallback.ts frontend/session/tests/hooks/use-session-state-fallback.test.ts
git commit -m "$(cat <<'EOF'
feat(session-fe): useSessionStateFallback — HTTP poll for engine failures

Polls /state every 5s once the candidate clicks Start. Stops on
terminal state. Mirrors the engine's session_outcome attribute path
for cases where the engine crashed before publishing the attribute.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5.4: `SessionErrorScreen` component

**Files:**
- Create: `frontend/session/components/interview/screens/session-error-screen.tsx`
- Test: `frontend/session/tests/components/interview/session-error-screen.test.tsx`

- [ ] **Step 1: Write the test**

Create `frontend/session/tests/components/interview/session-error-screen.test.tsx`:

```typescript
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { SessionErrorScreen } from '@/components/interview/screens/session-error-screen'

describe('SessionErrorScreen', () => {
  it.each([
    ['engine_session_config_invalid', 'configuration issue'],
    ['engine_company_profile_missing', "isn't fully set up"],
    ['engine_question_bank_not_ready', "isn't fully set up"],
    ['engine_room_join_failed', 'Something went wrong'],
    ['engine_internal_error', 'Something went wrong'],
    ['engine_unresponsive', "didn't start"],
  ])('renders the right copy for %s', (code, expectedFragment) => {
    render(<SessionErrorScreen errorCode={code} sessionId="sess-1" />)
    expect(screen.getByText(new RegExp(expectedFragment, 'i'))).toBeInTheDocument()
  })

  it('renders fallback copy for unknown codes', () => {
    render(<SessionErrorScreen errorCode="future_code_not_yet_known" sessionId="sess-1" />)
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
  })

  it('renders fallback copy when errorCode is null (LK-attribute path)', () => {
    render(<SessionErrorScreen errorCode={null} sessionId="sess-1" />)
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
  })

  it('shows the session id in the footer for support correlation', () => {
    render(<SessionErrorScreen errorCode={null} sessionId="sess-12345" />)
    expect(screen.getByText(/sess-12345/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test**

Run: `cd frontend/session && npm run test -- tests/components/interview/session-error-screen.test.tsx`

Expected: cannot find module — the file doesn't exist yet.

- [ ] **Step 3: Implement the component**

Create `frontend/session/components/interview/screens/session-error-screen.tsx`:

```typescript
'use client'

import { copyForErrorCode } from '../lib/session-error-messages'

interface Props {
  errorCode: string | null
  sessionId: string
}

/**
 * Terminal error screen shown when a session ends in state='error'.
 *
 * Two information paths into this screen:
 *   1. LK `session_outcome='error'` attribute (real-time, no code).
 *   2. HTTP `/state` poll showing state='error' (carries the error_code).
 *
 * Path 1 renders with errorCode=null and falls back to generic copy.
 * Path 2 renders with the full code and shows code-specific copy.
 *
 * No retry button — recruiter-driven retry per the failure-handling
 * spec (2026-05-16). The recruiter sees the failure on their tracker
 * and resends the invite using the existing scheduler flow.
 */
export function SessionErrorScreen({ errorCode, sessionId }: Props) {
  const { headline, body } = copyForErrorCode(errorCode)

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 px-4">
      <div className="max-w-lg w-full rounded-lg border border-zinc-200 bg-white p-8 shadow-sm">
        <h1 className="text-2xl font-semibold text-zinc-900">
          {headline}
        </h1>
        <p className="mt-3 text-sm text-zinc-700">
          {body}
        </p>
        <p className="mt-6 text-xs text-zinc-500">
          You can close this window. If you need help, reach out to
          your recruiter and include this reference: <span className="font-mono">{sessionId}</span>.
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend/session && npm run test -- tests/components/interview/session-error-screen.test.tsx`

Expected: 9 passed (6 parametrized + 3 standalone).

- [ ] **Step 5: Commit**

```bash
git add frontend/session/components/interview/screens/session-error-screen.tsx frontend/session/tests/components/interview/session-error-screen.test.tsx
git commit -m "$(cat <<'EOF'
feat(session-fe): SessionErrorScreen — candidate-facing failure UI

Renders error-code-specific copy via session-error-messages. No retry
button (recruiter-driven retry per the failure-handling spec). Shows
session id in the footer for support correlation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5.5: Wire precedence into OutcomeWatcher

**Files:**
- Modify: `frontend/session/components/interview/app/app.tsx` (or the file containing OutcomeWatcher — find it via grep first)
- Test: `frontend/session/tests/components/interview/outcome-precedence.test.tsx`

- [ ] **Step 1: Locate OutcomeWatcher**

Run: `grep -rn "OutcomeWatcher\|isSessionOutcome\|useSessionOutcome" frontend/session/components/`

The exhaustive switch on `SessionOutcome` is in there — that's the integration point.

- [ ] **Step 2: Read the existing component**

Open the file from Step 1 (likely `components/interview/app/app.tsx`) and find the `OutcomeWatcher` function or component. Note:
- Whether it renders a per-outcome screen via switch or just sets state.
- Where the wizard renders the children (so we know where to insert the error screen).

- [ ] **Step 3: Add the fallback hook + precedence rule**

In the file that owns the live-session container (likely `app.tsx` or `LiveSessionShell.tsx`), import the hook + screen and add the precedence rule. Find the existing `useSessionOutcome()` call and add alongside:

```typescript
import { useSessionStateFallback } from './hooks/use-session-state-fallback'
import { SessionErrorScreen } from '../screens/session-error-screen'

// … inside the component, somewhere after the existing useSessionOutcome() call …
const outcome = useSessionOutcome()
const fallbackState = useSessionStateFallback(token, /* enabled */ true)

// Precedence: LK attribute first (lower-latency), HTTP poll fallback.
const isTerminalError =
  outcome === 'error' ||
  fallbackState?.state === 'error'

if (isTerminalError) {
  // Prefer the polled error_code (carries full taxonomy). LK attribute
  // only carries the outcome string, no code -> null.
  const errorCode = fallbackState?.state === 'error'
    ? fallbackState.error_code
    : null
  return <SessionErrorScreen errorCode={errorCode} sessionId={sessionId} />
}
```

The `token` and `sessionId` references depend on what's already in scope in that file. Match the existing variable names.

If the existing `OutcomeWatcher` uses an exhaustive switch with `_exhaustive: never`, the `'error'` branch already exists — replace its body with the same `<SessionErrorScreen … />` render so both paths converge on the same screen.

- [ ] **Step 4: Write the composition test**

Create `frontend/session/tests/components/interview/outcome-precedence.test.tsx`:

```typescript
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// Mock the underlying hooks so we can drive precedence deterministically.
vi.mock('@/components/interview/app/hooks/use-session-outcome', () => ({
  useSessionOutcome: vi.fn(),
}))
vi.mock('@/components/interview/app/hooks/use-session-state-fallback', () => ({
  useSessionStateFallback: vi.fn(),
}))

import { useSessionOutcome } from '@/components/interview/app/hooks/use-session-outcome'
import { useSessionStateFallback } from '@/components/interview/app/hooks/use-session-state-fallback'

// Import the component that integrates both. Replace with the actual
// component name found in Task 5.5 Step 1.
import { LiveSessionShell } from '@/components/interview/app/app'  // adjust path

describe('outcome precedence', () => {
  it('LK attribute wins when it surfaces error', async () => {
    vi.mocked(useSessionOutcome).mockReturnValue('error')
    vi.mocked(useSessionStateFallback).mockReturnValue(null)

    render(<LiveSessionShell token="tok" sessionId="sess-1" /* …other required props… */ />)
    await waitFor(() => {
      // Generic copy because errorCode=null on the LK-attribute path.
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    })
  })

  it('HTTP fallback wins when LK attribute never arrives', async () => {
    vi.mocked(useSessionOutcome).mockReturnValue(null)
    vi.mocked(useSessionStateFallback).mockReturnValue({
      state: 'error',
      error_code: 'engine_session_config_invalid',
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    render(<LiveSessionShell token="tok" sessionId="sess-1" /* …other required props… */ />)
    await waitFor(() => {
      expect(screen.getByText(/configuration issue/i)).toBeInTheDocument()
    })
  })
})
```

The `LiveSessionShell` import path and props will need to match the actual component found in Step 1. If the component requires more than `token` / `sessionId` to render (e.g. a LiveKit Room context), wrap it in a minimal provider here — see existing tests under `tests/components/interview/` for the pattern.

- [ ] **Step 5: Run the tests**

Run: `cd frontend/session && npm run test -- tests/components/interview/outcome-precedence.test.tsx`

Expected: 2 passed.

- [ ] **Step 6: Run the full FE test suite to catch ripple effects**

Run: `cd frontend/session && npm run test`

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add frontend/session/components/interview/app/app.tsx frontend/session/tests/components/interview/outcome-precedence.test.tsx
git commit -m "$(cat <<'EOF'
feat(session-fe): outcome precedence — LK attribute first, HTTP fallback second

LiveSessionShell consumes both useSessionOutcome and
useSessionStateFallback and renders <SessionErrorScreen/> on the
first terminal-error signal from either path.

Closes the candidate-side half of the failure-handling spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6 — Frontend app: recruiter tracker

### Task 6.1: Extend kanban query + schema with `error_code`

**Files:**
- Modify: `backend/nexus/app/modules/candidates/schemas.py` (add field)
- Modify: `backend/nexus/app/modules/candidates/service.py:549-574` (extend the latest-state query)

- [ ] **Step 1: Extend the schema**

In `backend/nexus/app/modules/candidates/schemas.py`, find `KanbanCandidateCard` (line 154) and add the new field below the existing `latest_session_state`:

```python
class KanbanCandidateCard(BaseModel):
    # … existing fields …
    latest_session_state: str | None = None
    latest_session_error_code: str | None = None  # populated when latest_session_state == 'error'
    # … rest of existing fields …
```

Keep the column near `latest_session_state` — they travel together.

- [ ] **Step 2: Extend the query**

In `backend/nexus/app/modules/candidates/service.py:549-574`, replace the existing `Phase 3C: resolve latest_session_state per assignment` block with:

```python
    # Resolve latest session state + error_code per assignment in one extra
    # query. Subquery gets MAX(created_at) per assignment_id; outer join
    # retrieves the matching state + error_code columns.
    #
    # 'Latest' is the latest session for the assignment overall — even if
    # the candidate has moved past the failed stage, the historical error
    # is still useful context. (If we want per-current-stage scoping
    # later, gate on Session.stage_id == assignment.current_stage_id.)
    assignment_ids = {a.id for a in assignments}
    latest_session_by_assignment: dict[UUID, tuple[str, str | None]] = {}
    if assignment_ids:
        max_created = (
            select(
                Session.assignment_id.label("aid"),
                func.max(Session.created_at).label("max_ts"),
            )
            .where(Session.assignment_id.in_(assignment_ids))
            .group_by(Session.assignment_id)
            .subquery()
        )
        rows = (await db.execute(
            select(Session.assignment_id, Session.state, Session.error_code)
            .join(
                max_created,
                and_(
                    Session.assignment_id == max_created.c.aid,
                    Session.created_at == max_created.c.max_ts,
                ),
            )
        )).all()
        latest_session_by_assignment = {
            aid: (state, error_code) for aid, state, error_code in rows
        }
```

Then change the cards builder at line ~589 to pass both fields:

```python
        cards_by_stage.setdefault(a.current_stage_id, []).append(
            KanbanCandidateCard(
                # … existing fields …
                latest_session_state=(
                    latest_session_by_assignment.get(a.id, (None, None))[0]
                ),
                latest_session_error_code=(
                    latest_session_by_assignment.get(a.id, (None, None))[1]
                ),
                # … rest of existing fields …
            )
        )
```

- [ ] **Step 3: Run the candidates test suite**

Run: `docker compose run --rm nexus pytest tests/candidates/ -v -q`

Expected: all green. If a test asserts on `latest_session_state=None` keyed strictly off the old structure, update it to include the new `latest_session_error_code=None`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/candidates/schemas.py backend/nexus/app/modules/candidates/service.py
git commit -m "$(cat <<'EOF'
feat(candidates): expose latest_session_error_code on kanban cards

Extends the existing latest_session_state projection with the
matching error_code so the recruiter tracker can render a labeled
"Failed: <reason>" badge instead of just a generic error pill.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6.2: Extend `SessionStatusBadge` with labeled error

**Files:**
- Create: `frontend/app/components/dashboard/tracker/session-error-labels.ts`
- Modify: `frontend/app/components/dashboard/candidates/SessionStatusBadge.tsx`
- Modify: `frontend/app/lib/api/candidates.ts` (kanban response type — find the response shape and add the field)
- Test: `frontend/app/tests/components/candidates/session-status-badge.test.tsx`

- [ ] **Step 1: Create the labels map**

Create `frontend/app/components/dashboard/tracker/session-error-labels.ts`:

```typescript
/**
 * Recruiter-facing labels for backend error_code values.
 *
 * Backend taxonomy: app/modules/session/error_codes.py::ErrorCode.
 * Adding a code there requires adding an entry here in the same PR.
 * Unknown codes fall back to "Failed" (forward-compat).
 */

export const SESSION_ERROR_LABELS: Record<string, string> = {
  engine_session_config_invalid: 'Configuration error',
  engine_company_profile_missing: 'Company profile incomplete',
  engine_question_bank_not_ready: 'Question bank not ready',
  engine_room_join_failed: "Couldn't reach interview room",
  engine_internal_error: 'Internal error',
  engine_unresponsive: 'Interview never started',
}

export function labelForErrorCode(code: string | null | undefined): string {
  if (!code) return 'Failed'
  return SESSION_ERROR_LABELS[code] ?? 'Failed'
}
```

- [ ] **Step 2: Extend the kanban API type**

In `frontend/app/lib/api/candidates.ts` (or wherever `KanbanCandidateCard` is typed on the FE), add:

```typescript
export interface KanbanCandidateCard {
  // … existing fields …
  latest_session_state: string | null
  latest_session_error_code: string | null
  // … rest of existing fields …
}
```

- [ ] **Step 3: Write the badge test**

Create `frontend/app/tests/components/candidates/session-status-badge.test.tsx`:

```typescript
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { SessionStatusBadge } from '@/components/dashboard/candidates/SessionStatusBadge'

describe('SessionStatusBadge', () => {
  it.each([
    ['active', null, /live/i],
    ['completed', null, /completed/i],
    ['cancelled', null, /cancelled/i],
    [null, null, /not invited/i],
  ])('state=%s renders %s', (state, errorCode, pattern) => {
    render(<SessionStatusBadge state={state} errorCode={errorCode as string | null} />)
    expect(screen.getByText(pattern)).toBeInTheDocument()
  })

  it.each([
    ['engine_session_config_invalid', /configuration error/i],
    ['engine_company_profile_missing', /company profile incomplete/i],
    ['engine_question_bank_not_ready', /question bank not ready/i],
    ['engine_room_join_failed', /couldn't reach/i],
    ['engine_internal_error', /internal error/i],
    ['engine_unresponsive', /interview never started/i],
  ])('state=error error_code=%s renders %s', (code, pattern) => {
    render(<SessionStatusBadge state="error" errorCode={code} />)
    expect(screen.getByText(new RegExp(`failed.*${pattern.source}`, 'i'))).toBeInTheDocument()
  })

  it('state=error with unknown code falls back to generic "Failed"', () => {
    render(<SessionStatusBadge state="error" errorCode="future_unknown_code" />)
    expect(screen.getByText(/^failed$/i)).toBeInTheDocument()
  })

  it('state=error with null error_code falls back to generic "Failed"', () => {
    render(<SessionStatusBadge state="error" errorCode={null} />)
    expect(screen.getByText(/^failed$/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 4: Run the test (it should fail — the badge doesn't accept errorCode yet)**

Run: `cd frontend/app && npm run test -- tests/components/candidates/session-status-badge.test.tsx`

Expected: failures around the `errorCode` prop / labeled rendering.

- [ ] **Step 5: Extend the badge**

Modify `frontend/app/components/dashboard/candidates/SessionStatusBadge.tsx`:

```typescript
'use client'

import type { SessionState } from '@/lib/api/scheduler'
import { labelForErrorCode } from '@/components/dashboard/tracker/session-error-labels'

interface Props {
  state: SessionState | string | null
  errorCode?: string | null
}

const STATE_STYLES: Record<Exclude<SessionState, 'error'>, { label: string; className: string }> = {
  created: { label: 'Invited', className: 'px-chip soft' },
  pre_check: { label: 'Pre-check', className: 'px-chip ai' },
  consented: { label: 'Consented', className: 'px-chip ai' },
  active: { label: 'Live', className: 'px-chip ok' },
  completed: { label: 'Completed', className: 'px-chip ok' },
  cancelled: { label: 'Cancelled', className: 'px-chip caution' },
}

const NOT_INVITED_STYLE = { label: 'Not invited', className: 'px-chip soft' }

export function SessionStatusBadge({ state, errorCode = null }: Props) {
  if (state === 'error') {
    const label = errorCode
      ? `Failed: ${labelForErrorCode(errorCode)}`
      : 'Failed'
    return (
      <span
        className="px-chip danger"
        style={{ height: 18, padding: '0 7px', fontSize: 10.5 }}
        title={errorCode ?? undefined}
      >
        {label}
      </span>
    )
  }

  const entry = state
    ? (STATE_STYLES as Record<string, { label: string; className: string }>)[state as string] ?? NOT_INVITED_STYLE
    : NOT_INVITED_STYLE
  return (
    <span
      className={entry.className}
      style={{ height: 18, padding: '0 7px', fontSize: 10.5 }}
    >
      {entry.label}
    </span>
  )
}
```

- [ ] **Step 6: Find every existing call site of `<SessionStatusBadge state=…/>` and add the `errorCode` prop**

Run: `grep -rn "SessionStatusBadge" frontend/app/components/ frontend/app/app/`

For each call site, add `errorCode={card.latest_session_error_code}` (or the equivalent based on the data shape at that call site). If a call site doesn't have an `error_code` value in scope (legacy data), pass `null` explicitly.

- [ ] **Step 7: Run the tests + type-check**

```bash
cd frontend/app
npm run type-check
npm run test -- tests/components/candidates/session-status-badge.test.tsx
```

Expected: type-check clean; badge tests all passing.

- [ ] **Step 8: Commit**

```bash
git add frontend/app/components/dashboard/tracker/session-error-labels.ts frontend/app/components/dashboard/candidates/SessionStatusBadge.tsx frontend/app/lib/api/candidates.ts frontend/app/tests/components/candidates/session-status-badge.test.tsx
# plus any modified call sites:
git add $(grep -rln "SessionStatusBadge" frontend/app/components/ frontend/app/app/ | xargs)
git commit -m "$(cat <<'EOF'
feat(app-fe): labeled error badge on SessionStatusBadge

Renders "Failed: <human-readable error_code>" when state=error.
Unknown codes fall back to "Failed". The error_code mapping lives
in components/dashboard/tracker/session-error-labels.ts so the
existing card components consume one source of truth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6.3: Verify tracker page renders end-to-end

**Files:**
- Verification only (no code change)

- [ ] **Step 1: Run dev servers**

```bash
docker compose up -d
cd frontend/app && npm run dev &
```

- [ ] **Step 2: Seed an errored session in the dev DB**

```bash
docker compose exec postgres psql -U postgres -d postgres <<'SQL'
UPDATE sessions
   SET state = 'error',
       error_code = 'engine_session_config_invalid',
       state_changed_at = NOW()
 WHERE id IN (
   SELECT id FROM sessions
    WHERE state = 'active'
   ORDER BY created_at DESC
   LIMIT 1
 );
SQL
```

If no `state='active'` rows exist, transition any session manually (it must be a real session row tied to a candidate visible on a tracker board).

- [ ] **Step 3: Open the tracker page in a browser**

Navigate to `http://localhost:3000/tracker/<jobId>` for the job whose candidate you just transitioned.

Expected: the candidate's card shows a red `Failed: Configuration error` badge. Hover tooltip shows the raw `engine_session_config_invalid` code.

- [ ] **Step 4: Reset the seeded row**

```bash
docker compose exec postgres psql -U postgres -d postgres -c "UPDATE sessions SET state='cancelled', error_code=NULL, state_changed_at=NOW() WHERE state='error' AND error_code='engine_session_config_invalid';"
```

No code change → no commit.

---

## Phase 7 — End-to-end verification

### Task 7.1: Manual E2E walkthrough (per spec Testing §10)

**Files:**
- Verification only

- [ ] **Step 1: Branch off `feature/tracker-page` for a scratch repro**

```bash
git checkout -b scratch/verify-failure-handling
```

- [ ] **Step 2: Temporarily reintroduce the original bug**

Edit `backend/nexus/app/modules/interview_runtime/schemas.py` and revert `CompanyContext.about` to its caps:

```python
about: str = Field(min_length=30, max_length=500)
```

- [ ] **Step 3: Restart the backend**

```bash
docker compose restart nexus nexus-worker
```

- [ ] **Step 4: Run a candidate session against the Workato profile**

Use the existing tracker flow: move a candidate to "Bot Screening" stage on a job under the Workato org_unit, follow the invite link, click Start.

- [ ] **Step 5: Confirm every signal lands**

Check each:
- [ ] Engine logs `engine.entrypoint.failed error_code=engine_session_config_invalid`
- [ ] `SELECT state, error_code FROM sessions WHERE id = '<the new session>';` returns `error / engine_session_config_invalid`
- [ ] `SELECT action FROM audit_log WHERE resource_id = '<the new session>';` includes `session.errored`
- [ ] Candidate browser renders `<SessionErrorScreen/>` with "Configuration issue" copy (give it up to ~10s — first the LK-attribute path, then the HTTP poll, depending on which lands)
- [ ] Recruiter tracker (`/tracker/<jobId>`) shows `Failed: Configuration error` badge on the candidate card

- [ ] **Step 6: Revert and re-verify success path**

```bash
git checkout backend/nexus/app/modules/interview_runtime/schemas.py
docker compose restart nexus nexus-worker
```

Re-send the invite (recruiter side), have the candidate go through Start again. Confirm the agent joins, says the first question, and the session proceeds normally — no errored state, no badge.

- [ ] **Step 7: Discard the scratch branch and document the walkthrough**

```bash
git checkout feature/tracker-page
git branch -D scratch/verify-failure-handling
```

In the implementation-PR description, paste the checklist from Step 5 with each line ✅'d to document the verified end-to-end flow. This is the contract: "we manually verified this end-to-end."

---

## Post-implementation hygiene

- [ ] **Run the full backend suite once more:** `docker compose run --rm -e REAPER_ENABLED=false nexus pytest tests/ -q`
- [ ] **Run the frontend session suite:** `cd frontend/session && npm run test`
- [ ] **Run the frontend app suite:** `cd frontend/app && npm run test`
- [ ] **Confirm `npm run type-check` is clean in both frontends**
- [ ] **Tail `docker compose logs nexus` for 15+ minutes after deploy to dev to observe a real reaper tick. Expect:** `reaper.tick stuck_found=0 transitioned=0 threshold_seconds=900` repeated every 5 min.

---

## Spec coverage check

| Spec section | Plan task(s) |
|---|---|
| Goals 1–4 | Tasks 2.2, 3.2, 5.4–5.5, 6.2 |
| Error code taxonomy + migration | Tasks 1.1, 1.2 |
| Engine entrypoint handler (Approach A) | Tasks 2.1, 2.2, 2.3 |
| `transition_to_error` shared service | Task 1.3 |
| Reaper (apscheduler + advisory lock) | Tasks 3.1, 3.2, 3.3 |
| Candidate `/state` endpoint | Tasks 4.1, 4.2 |
| Candidate FE error UX (screen, hook, precedence) | Tasks 5.1–5.5 |
| Recruiter tracker error badge | Tasks 6.1, 6.2, 6.3 |
| Pre-existing: circular import | Task 0.1 |
| Pre-existing: stuck dev row | Task 0.2 |
| Testing strategy items 1–9 | Tests within Tasks 1.2, 1.3, 2.3, 3.2, 4.2, 5.3, 5.4, 5.5, 6.2 |
| Testing strategy item 10 (manual E2E) | Task 7.1 |
| Rollout sequencing | The plan ordering itself (migration first, backend handler+reaper next, FE last) |
