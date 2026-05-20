# Candidate Session Proctoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-recorded proctoring layer to the live candidate interview — fullscreen lock, tab/focus/keyboard/devtools detection, escalation, and a distinct `terminated` session outcome.

**Architecture:** A new `tenant_settings`-configured policy is delivered to the frontend in the `/start` & `/rejoin` responses. A new backend-authoritative endpoint `POST /api/candidate-session/{token}/proctoring/event` records each violation on the `sessions` row and, on a hard violation or soft-threshold breach, transitions the session `active → terminated`, sets `proctoring_outcome`, and best-effort cancels the LiveKit room. The frontend mounts a lazy `<ProctoringGuard>` (five detector hooks + a controller + two overlays) only inside the live session.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + asyncpg (backend); Next.js 16 / React 19 / TypeScript / Tailwind v4 / `motion` / `sonner` / Vitest + Testing Library (frontend); LiveKit.

**Spec:** `docs/superpowers/specs/2026-05-21-candidate-session-proctoring-design.md`

> **Migration-number correction:** the spec says migration `0042`; the live Alembic head is **`0042` (`0042_question_difficulty.py`)**, so this plan uses **`0043`**. (CLAUDE.md's "head is 0041" line is stale.)

---

## Severity taxonomy (shared, backend-authoritative)

| kind | severity | effect |
|---|---|---|
| `tab_switch` | hard | immediate end |
| `focus_loss` | hard | immediate end |
| `fullscreen_abandoned` | hard | immediate end |
| `devtools` | hard | immediate end |
| `fullscreen_exit` | soft | warn + count |
| `keyboard` | soft | warn + count |

Soft escalation: terminate when **cumulative soft count > `proctoring_soft_violation_limit`** (default 3 → 4th soft ends).

---

## Task 1: Migration `0043_session_proctoring`

**Files:**
- Create: `backend/nexus/migrations/versions/0043_session_proctoring.py`

- [ ] **Step 1: Write the migration**

Create `backend/nexus/migrations/versions/0043_session_proctoring.py`:

```python
"""session proctoring — violation log + terminated state + tenant config

Adds:
  * sessions.proctoring_violations  (JSONB NOT NULL DEFAULT '[]')
  * sessions.proctoring_outcome     (TEXT NULL — terminating reason)
  * sessions.proctoring_violation_count (INTEGER NOT NULL DEFAULT 0)
  * sessions_state_check            (+ 'terminated' value)
  * tenant_settings.proctoring_enabled              (BOOLEAN NOT NULL DEFAULT true)
  * tenant_settings.proctoring_soft_violation_limit (INTEGER NOT NULL DEFAULT 3)
  * tenant_settings.proctoring_fullscreen_grace_seconds (INTEGER NOT NULL DEFAULT 10)

No new tables → no new RLS policy pair (both sessions and tenant_settings
already carry tenant_isolation + service_bypass; new columns inherit).

Revision ID: 0043
Revises: 0042
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_STATES_NEW = (
    "'created','pre_check','consented','active','completed','cancelled','error','terminated'"
)
_STATES_OLD = (
    "'created','pre_check','consented','active','completed','cancelled','error'"
)


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "proctoring_violations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("sessions", sa.Column("proctoring_outcome", sa.Text(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column(
            "proctoring_violation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_state_check")
    op.execute(
        f"ALTER TABLE public.sessions ADD CONSTRAINT sessions_state_check "
        f"CHECK (state IN ({_STATES_NEW}))"
    )

    op.add_column(
        "tenant_settings",
        sa.Column("proctoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "tenant_settings",
        sa.Column(
            "proctoring_soft_violation_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.add_column(
        "tenant_settings",
        sa.Column(
            "proctoring_fullscreen_grace_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("10"),
        ),
    )


def downgrade() -> None:
    # Re-tightening the CHECK requires no 'terminated' rows remain.
    op.execute("UPDATE public.sessions SET state='cancelled' WHERE state='terminated'")
    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_state_check")
    op.execute(
        f"ALTER TABLE public.sessions ADD CONSTRAINT sessions_state_check "
        f"CHECK (state IN ({_STATES_OLD}))"
    )
    op.drop_column("tenant_settings", "proctoring_fullscreen_grace_seconds")
    op.drop_column("tenant_settings", "proctoring_soft_violation_limit")
    op.drop_column("tenant_settings", "proctoring_enabled")
    op.drop_column("sessions", "proctoring_violation_count")
    op.drop_column("sessions", "proctoring_outcome")
    op.drop_column("sessions", "proctoring_violations")
```

- [ ] **Step 2: Run the migration up and back down**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus alembic upgrade head
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: all three succeed; final `alembic current` shows `0043`.

- [ ] **Step 3: Verify the CHECK accepts `terminated`**

Run:
```bash
docker compose exec nexus python -c "
import asyncio
from sqlalchemy import text
from app.database import engine
async def main():
    async with engine.begin() as c:
        r = await c.execute(text(\"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='sessions_state_check'\"))
        print(r.scalar_one())
asyncio.run(main())
"
```
Expected: the printed CHECK definition contains `'terminated'`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/migrations/versions/0043_session_proctoring.py
git commit -m "feat(session): migration 0043 — proctoring columns + terminated state + tenant config"
```

---

## Task 2: Backend schemas, state machine, ORM columns

**Files:**
- Modify: `backend/nexus/app/modules/session/schemas.py`
- Modify: `backend/nexus/app/modules/session/state_machine.py`
- Modify: `backend/nexus/app/modules/session/models.py:43-77`
- Modify: `backend/nexus/app/modules/tenant_settings/schemas.py`
- Modify: `backend/nexus/app/modules/tenant_settings/models.py`
- Modify: `backend/nexus/app/modules/tenant_settings/service.py:55-59`
- Test: `backend/nexus/tests/test_session_state_machine.py`

- [ ] **Step 1: Write the failing state-machine test**

Create `backend/nexus/tests/test_session_state_machine.py`:

```python
import pytest

from app.modules.session.schemas import SessionState
from app.modules.session.errors import InvalidSessionStateError
from app.modules.session.state_machine import transition


def test_active_to_terminated_is_legal():
    assert transition(SessionState.ACTIVE, SessionState.TERMINATED) == SessionState.TERMINATED


def test_active_to_completed_still_legal():
    assert transition(SessionState.ACTIVE, SessionState.COMPLETED) == SessionState.COMPLETED


def test_terminated_is_terminal():
    with pytest.raises(InvalidSessionStateError):
        transition(SessionState.TERMINATED, SessionState.COMPLETED)


def test_consented_cannot_jump_to_terminated():
    with pytest.raises(InvalidSessionStateError):
        transition(SessionState.CONSENTED, SessionState.TERMINATED)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_session_state_machine.py -v`
Expected: FAIL — `AttributeError: TERMINATED` (enum value missing).

- [ ] **Step 3: Add `TERMINATED` to `SessionState` and the transition graph**

In `backend/nexus/app/modules/session/schemas.py`, add the enum member after `ERROR`:

```python
class SessionState(StrEnum):
    CREATED = "created"
    PRE_CHECK = "pre_check"
    CONSENTED = "consented"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"
    TERMINATED = "terminated"
```

In `backend/nexus/app/modules/session/state_machine.py`, update the graph + docstring:

```python
_LEGAL_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.CREATED: {SessionState.PRE_CHECK, SessionState.CANCELLED},
    SessionState.PRE_CHECK: {SessionState.CONSENTED, SessionState.CANCELLED},
    SessionState.CONSENTED: {SessionState.ACTIVE, SessionState.CANCELLED},
    SessionState.ACTIVE: {
        SessionState.COMPLETED,
        SessionState.ERROR,
        SessionState.TERMINATED,
    },
    SessionState.COMPLETED: set(),
    SessionState.CANCELLED: set(),
    SessionState.ERROR: set(),
    SessionState.TERMINATED: set(),
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_session_state_machine.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Add the proctoring schemas to `session/schemas.py`**

Append to `backend/nexus/app/modules/session/schemas.py` (after `StartSessionResponse`), and add the `proctoring` field to `StartSessionResponse` + `proctoring_enabled` to `PreCheckResponse`:

```python
ProctoringKind = Literal[
    "tab_switch",
    "focus_loss",
    "fullscreen_abandoned",
    "devtools",
    "fullscreen_exit",
    "keyboard",
]


class ProctoringConfig(BaseModel):
    """Per-tenant proctoring policy delivered to the candidate frontend
    on /start and /rejoin. enabled=False means the frontend mounts no
    proctoring listeners at all."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    soft_violation_limit: int
    fullscreen_grace_seconds: int


class ProctoringEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ProctoringKind
    occurred_at: datetime


class ProctoringEventResult(BaseModel):
    terminated: bool
    violation_count: int
    soft_violation_count: int
    already_terminal: bool = False
```

In `PreCheckResponse`, add the disclosure flag (after `otp_issued_at`):

```python
    otp_issued_at: datetime | None
    proctoring_enabled: bool
```

In `StartSessionResponse`, add the config block (after `audio_processing_hints`):

```python
    audio_processing_hints: AudioProcessingHints
    proctoring: ProctoringConfig
```

- [ ] **Step 6: Add ORM columns to the `Session` model**

In `backend/nexus/app/modules/session/models.py`, after `error_code` (line 77), add:

```python
    error_code: Mapped[str | None] = mapped_column(Text)
    proctoring_violations: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    proctoring_outcome: Mapped[str | None] = mapped_column(Text)
    proctoring_violation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
```

(`JSONB`, `Integer`, `Text`, and `sql_text` are already imported in this file — confirm at the top; the existing `knockout_failures` column uses the same `JSONB` + `sql_text("'[]'::jsonb")` pattern.)

- [ ] **Step 7: Extend `tenant_settings` schema, model, and read path**

In `backend/nexus/app/modules/tenant_settings/schemas.py`, add fields + validators to `TenantSettings`:

```python
from pydantic import BaseModel, field_validator, Field
# ...
class TenantSettings(BaseModel):
    tenant_id: UUID
    engine_knockout_policy: KnockoutPolicy = "close_polite"
    engine_agent_name: str | None = None
    proctoring_enabled: bool = True
    proctoring_soft_violation_limit: int = Field(default=3, ge=1, le=20)
    proctoring_fullscreen_grace_seconds: int = Field(default=10, ge=3, le=60)
    # ... existing _reject_empty_override validator unchanged ...
```

In `backend/nexus/app/modules/tenant_settings/models.py`, add the three columns to `TenantSettingsModel` (after `engine_agent_name`):

```python
    from sqlalchemy import Boolean, Integer  # add to existing imports
    # ...
    engine_agent_name: Mapped[str | None] = mapped_column(nullable=True)
    proctoring_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    proctoring_soft_violation_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3")
    )
    proctoring_fullscreen_grace_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10")
    )
```

In `backend/nexus/app/modules/tenant_settings/service.py`, populate the new fields when a row exists (the `return TenantSettings(...)` block at lines 55-59):

```python
    return TenantSettings(
        tenant_id=row.tenant_id,
        engine_knockout_policy=row.engine_knockout_policy,
        engine_agent_name=row.engine_agent_name,
        proctoring_enabled=row.proctoring_enabled,
        proctoring_soft_violation_limit=row.proctoring_soft_violation_limit,
        proctoring_fullscreen_grace_seconds=row.proctoring_fullscreen_grace_seconds,
    )
```

- [ ] **Step 8: Write + run the tenant-settings default test**

Append to `backend/nexus/tests/test_session_state_machine.py` (or a new `tests/test_tenant_settings_proctoring_defaults.py`):

```python
from uuid import uuid4
from app.modules.tenant_settings import DEFAULT_TENANT_SETTINGS


def test_proctoring_defaults_on_lazy_default():
    s = DEFAULT_TENANT_SETTINGS(uuid4())
    assert s.proctoring_enabled is True
    assert s.proctoring_soft_violation_limit == 3
    assert s.proctoring_fullscreen_grace_seconds == 10
```

Run: `docker compose run --rm nexus pytest tests/test_session_state_machine.py tests/test_tenant_settings_proctoring_defaults.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/nexus/app/modules/session/schemas.py backend/nexus/app/modules/session/state_machine.py backend/nexus/app/modules/session/models.py backend/nexus/app/modules/tenant_settings/ backend/nexus/tests/test_session_state_machine.py backend/nexus/tests/test_tenant_settings_proctoring_defaults.py
git commit -m "feat(session): terminated state + proctoring schemas/columns + tenant proctoring config"
```

---

## Task 3: Proctoring decision logic (pure, DB-free)

**Files:**
- Create: `backend/nexus/app/modules/session/proctoring.py`
- Test: `backend/nexus/tests/test_session_proctoring.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_session_proctoring.py`:

```python
import pytest

from app.modules.session.proctoring import (
    VIOLATION_SEVERITY,
    classify_severity,
    decide_termination,
)


def test_severity_map_is_complete():
    assert VIOLATION_SEVERITY["tab_switch"] == "hard"
    assert VIOLATION_SEVERITY["focus_loss"] == "hard"
    assert VIOLATION_SEVERITY["fullscreen_abandoned"] == "hard"
    assert VIOLATION_SEVERITY["devtools"] == "hard"
    assert VIOLATION_SEVERITY["fullscreen_exit"] == "soft"
    assert VIOLATION_SEVERITY["keyboard"] == "soft"


def test_hard_violation_terminates_with_kind_as_outcome():
    terminal, outcome = decide_termination(kind="devtools", soft_count_including_new=0, soft_limit=3)
    assert terminal is True
    assert outcome == "devtools"


def test_soft_below_limit_does_not_terminate():
    terminal, outcome = decide_termination(kind="keyboard", soft_count_including_new=3, soft_limit=3)
    assert terminal is False
    assert outcome is None


def test_soft_over_limit_terminates_with_threshold_outcome():
    terminal, outcome = decide_termination(kind="keyboard", soft_count_including_new=4, soft_limit=3)
    assert terminal is True
    assert outcome == "soft_threshold_exceeded"


def test_classify_severity_rejects_unknown_kind():
    with pytest.raises(KeyError):
        classify_severity("nope")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_session_proctoring.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.session.proctoring`.

- [ ] **Step 3: Implement the pure helper**

Create `backend/nexus/app/modules/session/proctoring.py`:

```python
"""Pure proctoring policy helpers — no DB, no I/O, fully unit-testable.

The HTTP endpoint + service (session/router.py, session/service.py) call
these to classify a violation and decide whether it terminates the session.
Severity is server-authoritative — the client-reported kind is the only
input we trust, and only after Pydantic validates it against ProctoringKind.
"""
from __future__ import annotations

from typing import Literal

Severity = Literal["hard", "soft"]

VIOLATION_SEVERITY: dict[str, Severity] = {
    "tab_switch": "hard",
    "focus_loss": "hard",
    "fullscreen_abandoned": "hard",
    "devtools": "hard",
    "fullscreen_exit": "soft",
    "keyboard": "soft",
}


def classify_severity(kind: str) -> Severity:
    """Return 'hard'|'soft' for a violation kind. Raises KeyError on unknown."""
    return VIOLATION_SEVERITY[kind]


def decide_termination(
    *, kind: str, soft_count_including_new: int, soft_limit: int
) -> tuple[bool, str | None]:
    """Decide whether this violation ends the session.

    Returns (terminal, proctoring_outcome). For a hard kind the outcome is
    the kind itself; for a soft escalation it is 'soft_threshold_exceeded';
    otherwise (False, None).
    """
    severity = classify_severity(kind)
    if severity == "hard":
        return True, kind
    if soft_count_including_new > soft_limit:
        return True, "soft_threshold_exceeded"
    return False, None
```

- [ ] **Step 4: Run it to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_session_proctoring.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/session/proctoring.py backend/nexus/tests/test_session_proctoring.py
git commit -m "feat(session): pure proctoring severity + termination decision helpers"
```

---

## Task 4: `record_proctoring_event` service + `/start`/`/rejoin`/`/pre-check` config embed

**Files:**
- Modify: `backend/nexus/app/modules/session/service.py`
- Test: `backend/nexus/tests/test_session_proctoring_service.py`

- [ ] **Step 1: Wire the proctoring config into `start_session`, `rejoin_session`, `get_pre_check_context`**

In `backend/nexus/app/modules/session/service.py`, add imports near the top:

```python
from app.modules.tenant_settings import get_tenant_settings
from app.modules.session.proctoring import classify_severity, decide_termination
from app.modules.session.schemas import (
    AudioProcessingHints,
    PreCheckResponse,
    ProctoringConfig,
    ProctoringEventResult,
    SessionDetailResponse,
    SessionListPage,
    SessionState,
    StartSessionResponse,
)
```

Add a private builder after `_compute_audio_processing_hints` (around line 85):

```python
async def _build_proctoring_config(
    db: AsyncSession, tenant_id: UUID
) -> ProctoringConfig:
    settings_ = await get_tenant_settings(db, tenant_id)
    return ProctoringConfig(
        enabled=settings_.proctoring_enabled,
        soft_violation_limit=settings_.proctoring_soft_violation_limit,
        fullscreen_grace_seconds=settings_.proctoring_fullscreen_grace_seconds,
    )
```

In `get_pre_check_context`, set the disclosure flag in the returned `PreCheckResponse` (the `return PreCheckResponse(...)` block ~line 215):

```python
    proctoring = await _build_proctoring_config(db, sess.tenant_id)
    return PreCheckResponse(
        session_id=sess.id,
        company_name=company_name,
        job_title=job.title,
        stage_name=stage.name,
        duration_minutes=stage.duration_minutes,
        consent_text=_CONSENT_TEXT,
        state=SessionState(sess.state),
        otp_required=sess.otp_required,
        otp_verified_at=sess.otp_verified_at,
        otp_issued_at=sess.otp_issued_at,
        proctoring_enabled=proctoring.enabled,
    )
```

In `start_session`, the final `return StartSessionResponse(...)` (~line 529):

```python
    proctoring = await _build_proctoring_config(db, sess.tenant_id)
    return StartSessionResponse(
        livekit_url=settings.livekit_public_url or settings.livekit_url,
        livekit_token=candidate_lk_token,
        room_name=room_name,
        session_id=sess.id,
        audio_processing_hints=_compute_audio_processing_hints(),
        proctoring=proctoring,
    )
```

In `rejoin_session`, the final `return StartSessionResponse(...)` (~line 598):

```python
    proctoring = await _build_proctoring_config(db, session.tenant_id)
    return StartSessionResponse(
        livekit_url=settings.livekit_public_url or settings.livekit_url,
        livekit_token=new_lk_token,
        room_name=session.livekit_room_name,
        session_id=session.id,
        audio_processing_hints=_compute_audio_processing_hints(),
        proctoring=proctoring,
    )
```

- [ ] **Step 2: Add `record_proctoring_event` to `service.py`**

Append to `backend/nexus/app/modules/session/service.py`:

```python
async def record_proctoring_event(
    db: AsyncSession,
    *,
    session_id: UUID,
    tenant_id: UUID,
    kind: str,
    occurred_at: datetime,
    correlation_id: str,
) -> ProctoringEventResult:
    """Record one proctoring violation and decide termination (authoritative).

    * Loads the session for id + tenant_id (cross-tenant → 404, same opacity
      as /state).
    * If the session is not 'active', returns an idempotent terminal success
      (a violation arriving after the session already ended is a no-op).
    * Appends {kind, severity, occurred_at} to sessions.proctoring_violations.
    * Terminal on a hard kind OR when cumulative soft count > the tenant's
      proctoring_soft_violation_limit. On termination: stamp proctoring_outcome,
      transition active → terminated, best-effort cancel_room, audit.
    """
    sess = (
        await db.execute(
            select(Session).where(Session.id == session_id, Session.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise SessionNotFoundError()

    existing = list(sess.proctoring_violations or [])

    if sess.state != SessionState.ACTIVE.value:
        soft = sum(1 for v in existing if v.get("severity") == "soft")
        return ProctoringEventResult(
            terminated=True,
            violation_count=len(existing),
            soft_violation_count=soft,
            already_terminal=True,
        )

    severity = classify_severity(kind)
    violations = existing + [
        {"kind": kind, "severity": severity, "occurred_at": occurred_at.isoformat()}
    ]
    soft_count = sum(1 for v in violations if v.get("severity") == "soft")

    tenant_settings = await get_tenant_settings(db, tenant_id)
    terminal, outcome = decide_termination(
        kind=kind,
        soft_count_including_new=soft_count,
        soft_limit=tenant_settings.proctoring_soft_violation_limit,
    )

    # Reassigning a new list marks the JSONB attribute dirty for the flush.
    sess.proctoring_violations = violations
    sess.proctoring_violation_count = len(violations)

    if terminal:
        sess.proctoring_outcome = outcome
        sess.state = transition(SessionState.ACTIVE, SessionState.TERMINATED).value
        sess.state_changed_at = datetime.now(UTC)
        await db.flush()
        if sess.livekit_room_name:
            with contextlib.suppress(Exception):
                await cancel_room(sess.livekit_room_name)
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            actor_email=None,
            action="session.proctoring_terminated",
            resource="session",
            resource_id=sess.id,
            payload={
                "proctoring_outcome": outcome,
                "kind": kind,
                "violation_count": len(violations),
                "correlation_id": correlation_id,
            },
        )
    else:
        await db.flush()
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            actor_email=None,
            action="session.proctoring_violation",
            resource="session",
            resource_id=sess.id,
            payload={
                "kind": kind,
                "severity": severity,
                "violation_count": len(violations),
                "correlation_id": correlation_id,
            },
        )

    return ProctoringEventResult(
        terminated=terminal,
        violation_count=len(violations),
        soft_violation_count=soft_count,
    )
```

- [ ] **Step 3: Write the failing service test**

Create `backend/nexus/tests/test_session_proctoring_service.py`. This mirrors the session-row setup pattern in `tests/test_middleware_candidate_single_use.py` (use that file's fixtures/helpers for creating a tenant + assignment + `active` session under a bypass-RLS session). The test asserts the service behavior:

```python
import uuid
from datetime import datetime, UTC

import pytest

from app.modules.session import service as session_service
from app.modules.session.schemas import SessionState
from app.modules.session.errors import SessionNotFoundError

# NOTE: `make_active_session(db, tenant_id)` is a helper to add — it inserts a
# minimal sessions row at state='active' with a livekit_room_name and returns it.
# Model it on the row-construction already used in
# tests/test_middleware_candidate_single_use.py. Patch session_service.cancel_room
# with an AsyncMock so no real LiveKit call is made.


@pytest.mark.asyncio
async def test_hard_violation_terminates(bypass_db, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())
    tenant_id = uuid.uuid4()
    sess = await make_active_session(bypass_db, tenant_id)

    result = await session_service.record_proctoring_event(
        bypass_db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="tab_switch",
        occurred_at=datetime.now(UTC),
        correlation_id="cid",
    )
    assert result.terminated is True
    await bypass_db.refresh(sess)
    assert sess.state == SessionState.TERMINATED.value
    assert sess.proctoring_outcome == "tab_switch"
    assert sess.proctoring_violation_count == 1
    session_service.cancel_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_below_limit_does_not_terminate(bypass_db):
    tenant_id = uuid.uuid4()
    sess = await make_active_session(bypass_db, tenant_id)
    for _ in range(3):  # limit default 3 → 3 warnings allowed
        result = await session_service.record_proctoring_event(
            bypass_db, session_id=sess.id, tenant_id=tenant_id,
            kind="keyboard", occurred_at=datetime.now(UTC), correlation_id="c",
        )
    assert result.terminated is False
    await bypass_db.refresh(sess)
    assert sess.state == SessionState.ACTIVE.value
    assert sess.proctoring_violation_count == 3


@pytest.mark.asyncio
async def test_soft_over_limit_terminates(bypass_db, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())
    tenant_id = uuid.uuid4()
    sess = await make_active_session(bypass_db, tenant_id)
    last = None
    for _ in range(4):  # 4th soft exceeds limit 3
        last = await session_service.record_proctoring_event(
            bypass_db, session_id=sess.id, tenant_id=tenant_id,
            kind="keyboard", occurred_at=datetime.now(UTC), correlation_id="c",
        )
    assert last.terminated is True
    await bypass_db.refresh(sess)
    assert sess.proctoring_outcome == "soft_threshold_exceeded"


@pytest.mark.asyncio
async def test_post_termination_is_idempotent(bypass_db, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())
    tenant_id = uuid.uuid4()
    sess = await make_active_session(bypass_db, tenant_id)
    await session_service.record_proctoring_event(
        bypass_db, session_id=sess.id, tenant_id=tenant_id,
        kind="devtools", occurred_at=datetime.now(UTC), correlation_id="c",
    )
    again = await session_service.record_proctoring_event(
        bypass_db, session_id=sess.id, tenant_id=tenant_id,
        kind="devtools", occurred_at=datetime.now(UTC), correlation_id="c",
    )
    assert again.terminated is True
    assert again.already_terminal is True


@pytest.mark.asyncio
async def test_cross_tenant_session_404(bypass_db):
    tenant_id = uuid.uuid4()
    sess = await make_active_session(bypass_db, tenant_id)
    with pytest.raises(SessionNotFoundError):
        await session_service.record_proctoring_event(
            bypass_db, session_id=sess.id, tenant_id=uuid.uuid4(),  # wrong tenant
            kind="keyboard", occurred_at=datetime.now(UTC), correlation_id="c",
        )
```

- [ ] **Step 4: Run the service test (add the `make_active_session` helper + `bypass_db` fixture if missing)**

Run: `docker compose run --rm nexus pytest tests/test_session_proctoring_service.py -v`
Expected: PASS (5 passed). If `make_active_session`/`bypass_db` don't exist, add them mirroring `tests/test_middleware_candidate_single_use.py` (a `get_bypass_db()` session + a `Session(...)` insert at `state='active'`, `livekit_room_name='session-x'`).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/session/service.py backend/nexus/tests/test_session_proctoring_service.py
git commit -m "feat(session): record_proctoring_event service + proctoring config on start/rejoin/pre-check"
```

---

## Task 5: Proctoring endpoint (router)

**Files:**
- Modify: `backend/nexus/app/modules/session/router.py`
- Test: `backend/nexus/tests/test_session_proctoring_endpoint.py`

- [ ] **Step 1: Add the endpoint**

In `backend/nexus/app/modules/session/router.py`, add `ProctoringEventRequest` + `ProctoringEventResult` to the schema import block (lines 31-39), `import uuid` if not present, then add the endpoint after `get_candidate_session_state_endpoint` (after line 244):

```python
@candidate_session_router.post(
    "/proctoring/event",
    response_model=ProctoringEventResult,
)
async def post_proctoring_event_endpoint(
    request: Request,
    token: str,  # consumed by middleware — declared so FastAPI routes correctly
    body: ProctoringEventRequest,
    db: AsyncSession = Depends(get_tenant_db),
) -> ProctoringEventResult:
    """Record one proctoring violation; backend decides termination.

    Auth: candidate JWT in path (already verified by AuthMiddleware; an
    already-`used_at` token still authenticates — only unknown/superseded
    JTIs are rejected, same as /rejoin and /state). Tenant-scoped via the
    verified token's claims. No PII recorded — only kind/severity/timestamps.

    Rate limit (declared; not yet enforced — see /rejoin note): 60/min per
    token, 120/min per IP.
    """
    payload = request.state.candidate_token_payload
    return await session_service.record_proctoring_event(
        db,
        session_id=payload.session_id,
        tenant_id=payload.tenant_id,
        kind=body.kind,
        occurred_at=body.occurred_at,
        correlation_id=str(uuid.uuid4()),
    )
```

Add `import uuid` at the top of `router.py` (it currently imports only `from uuid import UUID`).

- [ ] **Step 2: Write the failing endpoint test**

Create `backend/nexus/tests/test_session_proctoring_endpoint.py`, mirroring the candidate-JWT request pattern in `tests/test_middleware_candidate_single_use.py` (mint a candidate token for an `active` session, call the endpoint via the `AsyncClient` fixture from `conftest.py`). Example happy-path + validation tests:

```python
import uuid
from datetime import datetime, UTC

import pytest

# Helpers `mint_candidate_token_for(session)` and `make_active_session` are the
# same ones used by tests/test_middleware_candidate_single_use.py — reuse them.


@pytest.mark.asyncio
async def test_hard_event_terminates_via_http(client, bypass_db, monkeypatch):
    from unittest.mock import AsyncMock
    from app.modules.session import service as session_service
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())

    sess = await make_active_session(bypass_db, uuid.uuid4())
    token = mint_candidate_token_for(sess)

    r = await client.post(
        f"/api/candidate-session/{token}/proctoring/event",
        json={"kind": "devtools", "occurred_at": datetime.now(UTC).isoformat()},
    )
    assert r.status_code == 200
    assert r.json()["terminated"] is True


@pytest.mark.asyncio
async def test_unknown_kind_is_422(client, bypass_db):
    sess = await make_active_session(bypass_db, uuid.uuid4())
    token = mint_candidate_token_for(sess)
    r = await client.post(
        f"/api/candidate-session/{token}/proctoring/event",
        json={"kind": "screenshot", "occurred_at": datetime.now(UTC).isoformat()},
    )
    assert r.status_code == 422
```

- [ ] **Step 3: Run the endpoint test**

Run: `docker compose run --rm nexus pytest tests/test_session_proctoring_endpoint.py -v`
Expected: PASS (2 passed).

- [ ] **Step 4: Run the full backend session test suite + lint**

Run:
```bash
docker compose run --rm nexus pytest tests/ -k "session or proctoring or tenant_settings" -q
docker compose run --rm nexus ruff check app/modules/session app/modules/tenant_settings
```
Expected: all pass, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/session/router.py backend/nexus/tests/test_session_proctoring_endpoint.py
git commit -m "feat(session): POST /proctoring/event endpoint"
```

---

## Task 6: Frontend API client

**Files:**
- Modify: `frontend/session/lib/api/candidate-session.ts`
- Modify: `frontend/session/tests/lib/api/candidate-session.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `frontend/session/tests/lib/api/candidate-session.test.ts`:

```typescript
describe('candidateSessionApi.proctoringEvent', () => {
  it('POSTs the violation and returns the parsed result', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ terminated: false, violation_count: 1, soft_violation_count: 1 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const res = await candidateSessionApi.proctoringEvent('tok', {
      kind: 'keyboard',
      occurred_at: '2026-05-21T00:00:00.000Z',
    })

    expect(res.terminated).toBe(false)
    expect(res.violation_count).toBe(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/api/candidate-session/tok/proctoring/event')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({
      kind: 'keyboard',
      occurred_at: '2026-05-21T00:00:00.000Z',
    })
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend/session && npm run test -- candidate-session`
Expected: FAIL — `proctoringEvent is not a function`.

- [ ] **Step 3: Add the types + method**

In `frontend/session/lib/api/candidate-session.ts`:

Add after the `AudioProcessingHints` interface:

```typescript
export type ProctoringKind =
  | 'tab_switch'
  | 'focus_loss'
  | 'fullscreen_abandoned'
  | 'devtools'
  | 'fullscreen_exit'
  | 'keyboard'

export interface ProctoringConfig {
  enabled: boolean
  soft_violation_limit: number
  fullscreen_grace_seconds: number
}

export interface ProctoringEventBody {
  kind: ProctoringKind
  occurred_at: string // ISO-8601
}

export interface ProctoringEventResult {
  terminated: boolean
  violation_count: number
  soft_violation_count: number
  already_terminal?: boolean
}
```

Add `proctoring` to `StartSessionResponse`:

```typescript
export interface StartSessionResponse {
  livekit_url: string
  livekit_token: string
  room_name: string
  session_id: string
  audio_processing_hints: AudioProcessingHints
  proctoring: ProctoringConfig
}
```

Add `proctoring_enabled` to `PreCheckResponse`:

```typescript
export interface PreCheckResponse {
  session_id: string
  company_name: string
  job_title: string
  stage_name: string
  duration_minutes: number
  consent_text: string
  state: SessionState
  otp_required: boolean
  otp_verified_at: string | null
  otp_issued_at: string | null
  proctoring_enabled: boolean
}
```

Add the method to the `candidateSessionApi` object (after `getState`):

```typescript
  /**
   * Report a single proctoring violation. Backend is authoritative on the
   * escalation threshold and termination; the response says whether the
   * session was ended. Carries no PII — only the violation kind + timestamp.
   */
  proctoringEvent: (token: string, body: ProctoringEventBody) =>
    _call<ProctoringEventResult>(
      'POST',
      `/api/candidate-session/${token}/proctoring/event`,
      body,
    ),
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend/session && npm run test -- candidate-session`
Expected: PASS. Then `npm run test:coverage -- candidate-session` and confirm the 100%-branch gate on this file still holds.

- [ ] **Step 5: Commit**

```bash
git add frontend/session/lib/api/candidate-session.ts frontend/session/tests/lib/api/candidate-session.test.ts
git commit -m "feat(session-fe): proctoringEvent API client + proctoring config/disclosure types"
```

---

## Task 7: Shared kinds + proctoring controller

**Files:**
- Create: `frontend/session/components/interview/proctoring/violation-kinds.ts`
- Create: `frontend/session/components/interview/proctoring/use-proctoring-controller.ts`
- Test: `frontend/session/tests/components/interview/proctoring/use-proctoring-controller.test.tsx`

- [ ] **Step 1: Create the shared kinds module**

Create `frontend/session/components/interview/proctoring/violation-kinds.ts`:

```typescript
import type { ProctoringKind } from '@/lib/api/candidate-session'

export const HARD_KINDS: ReadonlySet<ProctoringKind> = new Set([
  'tab_switch',
  'focus_loss',
  'fullscreen_abandoned',
  'devtools',
])

export function isHard(kind: ProctoringKind): boolean {
  return HARD_KINDS.has(kind)
}

/** Human-readable phrase for warnings/toasts (no PII). */
export const VIOLATION_LABEL: Record<ProctoringKind, string> = {
  tab_switch: 'switching tabs',
  focus_loss: 'leaving the interview window',
  fullscreen_abandoned: 'exiting fullscreen',
  devtools: 'opening developer tools',
  fullscreen_exit: 'exiting fullscreen',
  keyboard: 'keyboard activity',
}

export type ProctoringTermination = ProctoringKind | 'soft_threshold_exceeded'

/** End-screen sentence fragment for each terminating reason. */
export const PROCTORING_END_LABEL: Record<ProctoringTermination, string> = {
  ...VIOLATION_LABEL,
  soft_threshold_exceeded: 'repeated interview-rule violations',
}
```

- [ ] **Step 2: Write the failing controller test**

Create `frontend/session/tests/components/interview/proctoring/use-proctoring-controller.test.tsx`:

```tsx
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import { useProctoringController } from '@/components/interview/proctoring/use-proctoring-controller'

vi.mock('@livekit/components-react', () => ({
  useSessionContext: () => ({ end: vi.fn() }),
}))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))

afterEach(() => vi.restoreAllMocks())

const cfg = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('useProctoringController', () => {
  it('hard violation ends locally even if the POST rejects (fail-safe)', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockRejectedValue(new Error('offline'))
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated }),
    )
    await act(async () => {
      await result.current.report('devtools')
    })
    expect(onTerminated).toHaveBeenCalledWith('devtools')
  })

  it('soft violation terminates only when backend says terminated', async () => {
    const spy = vi
      .spyOn(candidateSessionApi, 'proctoringEvent')
      .mockResolvedValue({ terminated: true, violation_count: 4, soft_violation_count: 4 })
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated }),
    )
    await act(async () => {
      await result.current.report('keyboard')
    })
    expect(spy).toHaveBeenCalled()
    await waitFor(() => expect(onTerminated).toHaveBeenCalledWith('soft_threshold_exceeded'))
  })

  it('terminates only once', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: true, violation_count: 1, soft_violation_count: 0,
    })
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated }),
    )
    await act(async () => {
      await result.current.report('tab_switch')
      await result.current.report('focus_loss')
    })
    expect(onTerminated).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd frontend/session && npm run test -- use-proctoring-controller`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the controller**

Create `frontend/session/components/interview/proctoring/use-proctoring-controller.ts`:

```typescript
'use client'

import { useCallback, useRef, useState } from 'react'
import { useSessionContext } from '@livekit/components-react'
import { toast } from 'sonner'

import {
  candidateSessionApi,
  type ProctoringConfig,
  type ProctoringKind,
} from '@/lib/api/candidate-session'
import { isHard, VIOLATION_LABEL, type ProctoringTermination } from './violation-kinds'

export interface BorderFlash {
  tone: 'hard' | 'soft'
  key: number
}

export interface UseProctoringControllerArgs {
  token: string
  config: ProctoringConfig
  onTerminated: (reason: ProctoringTermination) => void
}

export interface ProctoringController {
  report: (kind: ProctoringKind) => Promise<void>
  flash: BorderFlash | null
}

export function useProctoringController({
  token,
  config,
  onTerminated,
}: UseProctoringControllerArgs): ProctoringController {
  const ctx = useSessionContext() as unknown as { end?: () => void }
  const [flash, setFlash] = useState<BorderFlash | null>(null)
  const flashKey = useRef(0)
  const softCount = useRef(0)
  const terminatedRef = useRef(false)

  const terminate = useCallback(
    (reason: ProctoringTermination) => {
      if (terminatedRef.current) return
      terminatedRef.current = true
      onTerminated(reason) // sets the app-level terminal ref synchronously
      ctx.end?.() // disconnect; OutcomeWatcher is guarded against this
    },
    [ctx, onTerminated],
  )

  const report = useCallback(
    async (kind: ProctoringKind) => {
      if (terminatedRef.current) return

      const hard = isHard(kind)
      flashKey.current += 1
      setFlash({ tone: hard ? 'hard' : 'soft', key: flashKey.current })

      if (hard) {
        // Fail-safe: record best-effort, end locally regardless of the POST.
        void candidateSessionApi.proctoringEvent(token, { kind, occurred_at: new Date().toISOString() }).catch(() => {})
        toast.error(`Interview ending — ${VIOLATION_LABEL[kind]} is not permitted.`)
        terminate(kind)
        return
      }

      // Soft: warn, then let the backend decide the threshold.
      softCount.current += 1
      toast.warning(
        `Warning ${softCount.current} of ${config.soft_violation_limit}: please avoid ${VIOLATION_LABEL[kind]}.`,
      )
      try {
        const res = await candidateSessionApi.proctoringEvent(token, {
          kind,
          occurred_at: new Date().toISOString(),
        })
        if (res.terminated) terminate('soft_threshold_exceeded')
      } catch {
        // Network failure on a soft violation: keep the interview running.
      }
    },
    [token, config.soft_violation_limit, terminate],
  )

  return { report, flash }
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `cd frontend/session && npm run test -- use-proctoring-controller`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add frontend/session/components/interview/proctoring/violation-kinds.ts frontend/session/components/interview/proctoring/use-proctoring-controller.ts frontend/session/tests/components/interview/proctoring/use-proctoring-controller.test.tsx
git commit -m "feat(session-fe): proctoring controller + shared violation kinds"
```

---

## Task 8: Detector hooks

**Files:**
- Create: `frontend/session/components/interview/proctoring/use-visibility-guard.ts`
- Create: `frontend/session/components/interview/proctoring/use-focus-guard.ts`
- Create: `frontend/session/components/interview/proctoring/use-keyboard-guard.ts`
- Create: `frontend/session/components/interview/proctoring/use-devtools-guard.ts`
- Create: `frontend/session/components/interview/proctoring/use-fullscreen-guard.ts`
- Test: `frontend/session/tests/components/interview/proctoring/detector-hooks.test.tsx`

- [ ] **Step 1: Write the failing detector tests**

Create `frontend/session/tests/components/interview/proctoring/detector-hooks.test.tsx`:

```tsx
import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useVisibilityGuard } from '@/components/interview/proctoring/use-visibility-guard'
import { useFocusGuard } from '@/components/interview/proctoring/use-focus-guard'
import { useKeyboardGuard } from '@/components/interview/proctoring/use-keyboard-guard'

afterEach(() => vi.restoreAllMocks())

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  Object.defineProperty(document, 'hidden', { value: state === 'hidden', configurable: true })
}

describe('useVisibilityGuard', () => {
  it('fires tab_switch when the tab is hidden while armed', () => {
    const onViolation = vi.fn()
    renderHook(() => useVisibilityGuard({ armed: true, onViolation }))
    act(() => {
      setVisibility('hidden')
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onViolation).toHaveBeenCalledWith('tab_switch')
  })

  it('does nothing when not armed', () => {
    const onViolation = vi.fn()
    renderHook(() => useVisibilityGuard({ armed: false, onViolation }))
    act(() => {
      setVisibility('hidden')
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onViolation).not.toHaveBeenCalled()
  })
})

describe('useFocusGuard', () => {
  it('fires focus_loss on blur when the tab is still visible', () => {
    setVisibility('visible')
    const onViolation = vi.fn()
    renderHook(() => useFocusGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new Event('blur')))
    expect(onViolation).toHaveBeenCalledWith('focus_loss')
  })

  it('defers to the visibility guard when the blur is a tab switch (hidden)', () => {
    setVisibility('hidden')
    const onViolation = vi.fn()
    renderHook(() => useFocusGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new Event('blur')))
    expect(onViolation).not.toHaveBeenCalled()
  })
})

describe('useKeyboardGuard', () => {
  it('reports a debounced keyboard violation on a typing key', () => {
    const onViolation = vi.fn()
    renderHook(() => useKeyboardGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' })))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b' })))
    expect(onViolation).toHaveBeenCalledTimes(1) // debounced within the burst window
    expect(onViolation).toHaveBeenCalledWith('keyboard')
  })

  it('ignores navigation keys so the End button stays operable', () => {
    const onViolation = vi.fn()
    renderHook(() => useKeyboardGuard({ armed: true, onViolation }))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' })))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })))
    expect(onViolation).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend/session && npm run test -- detector-hooks`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `use-visibility-guard.ts`**

```typescript
'use client'

import { useEffect } from 'react'
import type { ProctoringKind } from '@/lib/api/candidate-session'

export interface GuardArgs {
  armed: boolean
  onViolation: (kind: ProctoringKind) => void
}

export function useVisibilityGuard({ armed, onViolation }: GuardArgs): void {
  useEffect(() => {
    if (!armed) return
    const onVis = () => {
      if (document.visibilityState === 'hidden') onViolation('tab_switch')
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [armed, onViolation])
}
```

- [ ] **Step 4: Implement `use-focus-guard.ts`**

```typescript
'use client'

import { useEffect } from 'react'
import type { GuardArgs } from './use-visibility-guard'

export function useFocusGuard({ armed, onViolation }: GuardArgs): void {
  useEffect(() => {
    if (!armed) return
    const onBlur = () => {
      // A tab switch fires both blur and visibilitychange→hidden; let the
      // visibility guard own that case so we record one violation, not two.
      if (document.visibilityState === 'hidden') return
      onViolation('focus_loss')
    }
    window.addEventListener('blur', onBlur)
    return () => window.removeEventListener('blur', onBlur)
  }, [armed, onViolation])
}
```

- [ ] **Step 5: Implement `use-keyboard-guard.ts`**

```typescript
'use client'

import { useEffect, useRef } from 'react'
import type { GuardArgs } from './use-visibility-guard'

const NAV_KEYS = new Set(['Tab', 'Enter', ' ', 'Escape', 'Shift', 'Control', 'Alt', 'Meta'])
const KEYBOARD_DEBOUNCE_MS = 1500

function isDevtoolsCombo(e: KeyboardEvent): boolean {
  if (e.key === 'F12') return true
  return (e.ctrlKey || e.metaKey) && e.shiftKey && ['I', 'J', 'C'].includes(e.key.toUpperCase())
}

function isBlockedCombo(e: KeyboardEvent): boolean {
  return (e.ctrlKey || e.metaKey) && ['s', 'p', 'f'].includes(e.key.toLowerCase())
}

export function useKeyboardGuard({ armed, onViolation }: GuardArgs): void {
  const lastFired = useRef(0)
  useEffect(() => {
    if (!armed) return
    const onKey = (e: KeyboardEvent) => {
      // Block save/print/find + the devtools-open shortcuts (the open is also
      // caught hard by useDevtoolsGuard; the keypress is recorded as soft).
      if (isDevtoolsCombo(e) || isBlockedCombo(e)) e.preventDefault()
      if (NAV_KEYS.has(e.key)) return // keep the End button keyboard-operable
      const now = Date.now()
      if (now - lastFired.current < KEYBOARD_DEBOUNCE_MS) return
      lastFired.current = now
      onViolation('keyboard')
    }
    const onCtx = (e: MouseEvent) => e.preventDefault()
    window.addEventListener('keydown', onKey)
    window.addEventListener('contextmenu', onCtx)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('contextmenu', onCtx)
    }
  }, [armed, onViolation])
}
```

- [ ] **Step 6: Implement `use-devtools-guard.ts`**

```typescript
'use client'

import { useEffect, useRef } from 'react'
import type { GuardArgs } from './use-visibility-guard'

const SIZE_DELTA_THRESHOLD = 160
const POLL_MS = 1000
const DEBUGGER_PAUSE_MS = 100

export function useDevtoolsGuard({ armed, onViolation }: GuardArgs): void {
  const baseW = useRef(0)
  const baseH = useRef(0)
  const fired = useRef(false)

  useEffect(() => {
    if (!armed) return
    fired.current = false
    // Baseline captured at arm time excludes the browser's own chrome
    // (toolbars), so we detect devtools docking *after* the session starts
    // as a delta increase rather than a fixed (always-positive) gap.
    baseW.current = window.outerWidth - window.innerWidth
    baseH.current = window.outerHeight - window.innerHeight

    const sizeOpened = () =>
      window.outerWidth - window.innerWidth - baseW.current > SIZE_DELTA_THRESHOLD ||
      window.outerHeight - window.innerHeight - baseH.current > SIZE_DELTA_THRESHOLD

    const fire = () => {
      if (fired.current) return
      fired.current = true
      onViolation('devtools')
    }

    const onResize = () => {
      if (sizeOpened()) fire()
    }
    window.addEventListener('resize', onResize)

    const interval = window.setInterval(() => {
      if (fired.current) return
      const t0 = performance.now()
      // Catches an already-open / undocked console. Only pauses the main
      // thread when devtools is actually open — i.e. the instant we terminate.
      // eslint-disable-next-line no-debugger
      debugger
      if (performance.now() - t0 > DEBUGGER_PAUSE_MS) {
        fire()
        return
      }
      if (sizeOpened()) fire()
    }, POLL_MS)

    return () => {
      window.removeEventListener('resize', onResize)
      window.clearInterval(interval)
    }
  }, [armed, onViolation])
}
```

- [ ] **Step 7: Implement `use-fullscreen-guard.ts`**

```typescript
'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ProctoringKind } from '@/lib/api/candidate-session'

export interface FullscreenGuardArgs {
  armed: boolean
  graceSeconds: number
  onViolation: (kind: ProctoringKind) => void
}

export interface FullscreenGuardState {
  showOverlay: boolean
  secondsLeft: number
  returnToFullscreen: () => void
}

export function useFullscreenGuard({
  armed,
  graceSeconds,
  onViolation,
}: FullscreenGuardArgs): FullscreenGuardState {
  const [showOverlay, setShowOverlay] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState(graceSeconds)
  const hasEntered = useRef(false)
  const graceActive = useRef(false)
  const timer = useRef<number | null>(null)

  const clearCountdown = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current)
      timer.current = null
    }
    graceActive.current = false
    setShowOverlay(false)
  }, [])

  const startGrace = useCallback(() => {
    if (graceActive.current) return
    graceActive.current = true
    setShowOverlay(true)
    setSecondsLeft(graceSeconds)
    let left = graceSeconds
    timer.current = window.setInterval(() => {
      left -= 1
      setSecondsLeft(left)
      if (left <= 0) {
        clearCountdown()
        onViolation('fullscreen_abandoned') // hard — controller terminates
      }
    }, 1000)
  }, [graceSeconds, clearCountdown, onViolation])

  const returnToFullscreen = useCallback(() => {
    // Must run inside a user-gesture handler (the overlay button click).
    void document.documentElement.requestFullscreen?.().catch(() => {})
  }, [])

  useEffect(() => {
    if (!armed) return
    const onFsChange = () => {
      if (document.fullscreenElement) {
        const wasGrace = graceActive.current
        clearCountdown()
        if (!hasEntered.current) {
          hasEntered.current = true // initial entry — never a violation
          return
        }
        if (wasGrace) onViolation('fullscreen_exit') // soft — returned in time
      } else {
        startGrace()
      }
    }
    document.addEventListener('fullscreenchange', onFsChange)
    // If we armed and aren't in fullscreen (the start-gesture request was
    // denied), prompt the candidate to click in — without penalty.
    if (!document.fullscreenElement) startGrace()
    else hasEntered.current = true
    return () => {
      document.removeEventListener('fullscreenchange', onFsChange)
      clearCountdown()
    }
  }, [armed, startGrace, clearCountdown, onViolation])

  return { showOverlay, secondsLeft, returnToFullscreen }
}
```

- [ ] **Step 8: Run the detector tests**

Run: `cd frontend/session && npm run test -- detector-hooks`
Expected: PASS (6 passed). (The devtools `debugger` trap and the fullscreen API are not exercised in jsdom — documented gap; the size-delta + visibility/focus/keyboard paths are covered.)

- [ ] **Step 9: Commit**

```bash
git add frontend/session/components/interview/proctoring/use-*.ts frontend/session/tests/components/interview/proctoring/detector-hooks.test.tsx
git commit -m "feat(session-fe): proctoring detector hooks (visibility/focus/keyboard/devtools/fullscreen)"
```

---

## Task 9: Presentational components + ProctoringGuard

**Files:**
- Create: `frontend/session/components/interview/proctoring/ViolationBorder.tsx`
- Create: `frontend/session/components/interview/proctoring/FullscreenGraceOverlay.tsx`
- Create: `frontend/session/components/interview/proctoring/ProctoringGuard.tsx`
- Create: `frontend/session/components/interview/app/ProctoringEndedScreen.tsx`

- [ ] **Step 1: Implement `ViolationBorder.tsx`**

```tsx
'use client'

import { motion, useReducedMotion } from 'motion/react'
import type { BorderFlash } from './use-proctoring-controller'

export function ViolationBorder({ flash }: { flash: BorderFlash | null }) {
  const reduce = useReducedMotion()
  if (!flash) return null
  const color = flash.tone === 'hard' ? 'var(--px-danger)' : 'var(--px-caution)'
  return (
    <>
      <motion.div
        key={flash.key}
        aria-hidden
        className="pointer-events-none fixed inset-0 z-[60]"
        initial={{ opacity: reduce ? 0.7 : 0 }}
        animate={reduce ? { opacity: 0.7 } : { opacity: [0, 1, 0.6, 1, 0] }}
        transition={reduce ? undefined : { duration: 2.4, times: [0, 0.15, 0.5, 0.7, 1] }}
        style={{ boxShadow: `inset 0 0 0 4px ${color}, inset 0 0 48px ${color}` }}
      />
      <span role="alert" className="sr-only">
        {flash.tone === 'hard'
          ? 'Interview ending due to a monitoring violation.'
          : 'Warning: monitoring detected an interview-rule violation.'}
      </span>
    </>
  )
}
```

- [ ] **Step 2: Implement `FullscreenGraceOverlay.tsx`**

```tsx
'use client'

import { Button } from '@/components/ui/button'

export function FullscreenGraceOverlay({
  secondsLeft,
  onReturn,
}: {
  secondsLeft: number
  onReturn: () => void
}) {
  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-black/60 backdrop-blur-xl">
      <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10 text-center">
        <h2 className="font-serif text-2xl text-px-fg">Return to fullscreen to continue</h2>
        <p className="mt-3 text-sm text-px-fg-3">
          This interview must stay in fullscreen. It will end in{' '}
          <span className="font-mono font-bold text-px-danger">{Math.max(secondsLeft, 0)}s</span>{' '}
          if you don&apos;t return.
        </p>
        <Button
          size="lg"
          onClick={onReturn}
          className="mt-8 w-64 rounded-full font-mono text-xs font-bold uppercase tracking-wider"
        >
          Return to fullscreen
        </Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Implement `ProctoringGuard.tsx`**

```tsx
'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { useVoiceAssistant } from '@livekit/components-react'

import type { ProctoringConfig } from '@/lib/api/candidate-session'
import { useProctoringController } from './use-proctoring-controller'
import { useVisibilityGuard } from './use-visibility-guard'
import { useFocusGuard } from './use-focus-guard'
import { useKeyboardGuard } from './use-keyboard-guard'
import { useDevtoolsGuard } from './use-devtools-guard'
import { useFullscreenGuard } from './use-fullscreen-guard'
import { ViolationBorder } from './ViolationBorder'
import { FullscreenGraceOverlay } from './FullscreenGraceOverlay'
import type { ProctoringTermination } from './violation-kinds'

const ARM_SETTLE_MS = 800

const DISABLED: ProctoringConfig = {
  enabled: false,
  soft_violation_limit: 3,
  fullscreen_grace_seconds: 10,
}

export function ProctoringGuard({
  token,
  config,
  onTerminated,
  children,
}: {
  token: string
  config: ProctoringConfig | null
  onTerminated: (reason: ProctoringTermination) => void
  children: ReactNode
}) {
  const cfg = config ?? DISABLED
  const { state } = useVoiceAssistant()
  const [armed, setArmed] = useState(false)

  // Arm only once the agent is live + a short settle window, so the LiveKit
  // connect, media publish, and the start-gesture fullscreen entry all settle
  // before enforcement begins (prevents self-inflicted terminations).
  useEffect(() => {
    if (armed || !cfg.enabled) return
    if (state === 'listening' || state === 'thinking' || state === 'speaking') {
      const t = setTimeout(() => setArmed(true), ARM_SETTLE_MS)
      return () => clearTimeout(t)
    }
  }, [state, armed, cfg.enabled])

  const controller = useProctoringController({ token, config: cfg, onTerminated })
  const enforce = armed && cfg.enabled

  useVisibilityGuard({ armed: enforce, onViolation: controller.report })
  useFocusGuard({ armed: enforce, onViolation: controller.report })
  useKeyboardGuard({ armed: enforce, onViolation: controller.report })
  useDevtoolsGuard({ armed: enforce, onViolation: controller.report })
  const fs = useFullscreenGuard({
    armed: enforce,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })

  return (
    <>
      {children}
      {cfg.enabled && <ViolationBorder flash={controller.flash} />}
      {cfg.enabled && fs.showOverlay && (
        <FullscreenGraceOverlay secondsLeft={fs.secondsLeft} onReturn={fs.returnToFullscreen} />
      )}
    </>
  )
}
```

- [ ] **Step 4: Implement `ProctoringEndedScreen.tsx`**

```tsx
'use client'

import { PROCTORING_END_LABEL, type ProctoringTermination } from '../proctoring/violation-kinds'

export function ProctoringEndedScreen({ reason }: { reason: string | null }) {
  const label =
    reason && reason in PROCTORING_END_LABEL
      ? PROCTORING_END_LABEL[reason as ProctoringTermination]
      : 'a monitoring violation'
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-2xl text-px-fg">Your interview was ended.</h1>
        <p className="mt-3 text-sm text-px-fg-3">
          Our monitoring detected {label}. This session has ended and cannot be resumed. If you
          believe this was a mistake, contact the hiring team.
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Type-check + lint**

Run: `cd frontend/session && npm run type-check && npm run lint`
Expected: no errors. (No test in this step — components are exercised by the composition test in Task 10.)

- [ ] **Step 6: Commit**

```bash
git add frontend/session/components/interview/proctoring/ViolationBorder.tsx frontend/session/components/interview/proctoring/FullscreenGraceOverlay.tsx frontend/session/components/interview/proctoring/ProctoringGuard.tsx frontend/session/components/interview/app/ProctoringEndedScreen.tsx
git commit -m "feat(session-fe): ProctoringGuard + violation border + grace overlay + ended screen"
```

---

## Task 10: Wire into app + view-controller + welcome disclosure + composition test

**Files:**
- Modify: `frontend/session/components/interview/app/view-controller.tsx`
- Modify: `frontend/session/components/interview/app/app.tsx`
- Modify: `frontend/session/components/interview/app/welcome-view.tsx`
- Test: `frontend/session/tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`

- [ ] **Step 1: Add the disclosure notice to `welcome-view.tsx`**

Add `proctored?: boolean` to the `Props` interface and render a notice. Replace the `Props` interface and the `<p>` body region:

```tsx
interface Props {
  companyName: string
  jobTitle: string
  durationMinutes: number
  startButtonText: string
  mode: 'start' | 'rejoin'
  onStartCall: () => void
  isPending?: boolean
  proctored?: boolean
}
```

Add this block inside the centered `<div>`, after the `<p className="mt-3 ...">{body}</p>` line and before the `<Button>`:

```tsx
        {proctored && (
          <div className="px-glass mt-6 rounded-xl px-5 py-4 text-left">
            <p className="text-xs font-semibold uppercase tracking-wider text-px-fg-2">
              This interview is monitored
            </p>
            <ul className="mt-2 space-y-1 text-xs text-px-fg-3">
              <li>• Stay in fullscreen for the whole interview.</li>
              <li>• Don&apos;t switch tabs or leave this window.</li>
              <li>• Keyboard use and developer tools are detected.</li>
              <li>• Repeated or serious violations will end your interview.</li>
            </ul>
          </div>
        )}
```

- [ ] **Step 2: Extend `view-controller.tsx`**

Update `Outcome`, the `Props`, the imports, the early returns, and the live branch:

```tsx
import { ProctoringGuard } from '../proctoring/ProctoringGuard'
import { ProctoringEndedScreen } from './ProctoringEndedScreen'
import type { ProctoringConfig } from '@/lib/api/candidate-session'
import type { ProctoringTermination } from '../proctoring/violation-kinds'
// ...
export type Outcome = 'live' | 'completed' | 'error' | 'proctoring_terminated'

interface Props {
  appConfig: AppConfig
  preCheck: PreCheckResponse
  mode: 'start' | 'rejoin'
  outcome: Outcome
  errorCode: string | null
  isStartPending: boolean
  onStart: () => void
  onError: (code: string) => void
  token: string
  proctoring: ProctoringConfig | null
  proctoringReason: string | null
  onProctoringTerminated: (reason: ProctoringTermination) => void
}
```

In the function signature destructure the new props, then add the early return (before `if (outcome === 'completed')`):

```tsx
  if (outcome === 'proctoring_terminated') {
    return <ProctoringEndedScreen reason={proctoringReason} />
  }
  if (outcome === 'completed') return <CompletionScreen />
```

Pass `proctored` to `WelcomeView`:

```tsx
      <WelcomeView
        companyName={appConfig.companyName}
        jobTitle={preCheck.job_title}
        durationMinutes={preCheck.duration_minutes}
        startButtonText={appConfig.startButtonText}
        mode={mode}
        onStartCall={onStart}
        isPending={isStartPending}
        proctored={preCheck.proctoring_enabled}
      />
```

Wrap `<LiveInterview>` in the live branch:

```tsx
  return (
    <AgentUIWithLoader>
      <ProctoringGuard token={token} config={proctoring} onTerminated={onProctoringTerminated}>
        <LiveInterview
          companyName={appConfig.companyName}
          jobTitle={preCheck.job_title}
          logo={appConfig.logo}
          accent={appConfig.accent}
          onEnd={() => ctx.end?.()}
        />
      </ProctoringGuard>
      <ReconnectingOverlay onTimeout={() => onError('RECONNECT_FAILED')} />
    </AgentUIWithLoader>
  )
```

- [ ] **Step 3: Extend `app.tsx`**

Add imports + state + the terminal-guard, capture the proctoring config from the start/rejoin response, and pass everything to `ViewController`.

Add to imports:

```tsx
import { type ProctoringConfig } from '@/lib/api/candidate-session'
import type { ProctoringTermination } from '../proctoring/violation-kinds'
```

Inside `App`, after the existing `useState` hooks:

```tsx
  const [proctoring, setProctoring] = useState<ProctoringConfig | null>(null)
  const [proctoringReason, setProctoringReason] = useState<string | null>(null)
  const proctoringTerminatedRef = useRef(false)
```

Guard the two outcome setters so the disconnect that `ctx.end()` causes can't override the proctoring screen. Replace `setError` and `onCompleted`:

```tsx
  const setError = useCallback((code: string) => {
    if (proctoringTerminatedRef.current) return
    setErrorCode(code)
    setOutcome('error')
  }, [])
  // ...
  const onCompleted = useCallback(() => {
    if (proctoringTerminatedRef.current) return
    setOutcome('completed')
  }, [])

  const onProctoringTerminated = useCallback((reason: ProctoringTermination) => {
    proctoringTerminatedRef.current = true
    setProctoringReason(reason)
    setOutcome('proctoring_terminated')
  }, [])
```

In the `TokenSource.custom` callback, capture the config right after `credsRef.current = {...}` is set:

```tsx
          credsRef.current = {
            serverUrl: creds.livekit_url,
            participantToken: creds.livekit_token,
          }
          setProctoring(creds.proctoring ?? null)
          return credsRef.current
```

Update `onStart` to enter fullscreen on the user gesture when proctoring is disclosed:

```tsx
  const onStart = useCallback(() => {
    if (preCheck.proctoring_enabled && document.fullscreenElement == null) {
      void document.documentElement.requestFullscreen?.().catch(() => {})
    }
    void session.start().catch(() => {})
  }, [session, preCheck.proctoring_enabled])
```

Pass the new props to `<ViewController>`:

```tsx
        <ViewController
          appConfig={appConfig}
          preCheck={preCheck}
          mode={mode}
          outcome={outcome}
          errorCode={errorCode}
          isStartPending={isStartPending}
          onStart={onStart}
          onError={setError}
          token={token}
          proctoring={proctoring}
          proctoringReason={proctoringReason}
          onProctoringTerminated={onProctoringTerminated}
        />
```

- [ ] **Step 4: Write the composition test**

Create `frontend/session/tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`:

```tsx
import { render, screen, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import { ProctoringGuard } from '@/components/interview/proctoring/ProctoringGuard'

let voiceState = 'listening'
const endMock = vi.fn()
vi.mock('@livekit/components-react', () => ({
  useVoiceAssistant: () => ({ state: voiceState }),
  useSessionContext: () => ({ end: endMock }),
}))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  endMock.mockClear()
})

const cfg = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('ProctoringGuard composition', () => {
  it('a hard violation (tab switch) terminates the session', async () => {
    vi.useFakeTimers()
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: true, violation_count: 1, soft_violation_count: 0,
    })
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    const onTerminated = vi.fn()

    render(
      <ProctoringGuard token="t" config={cfg} onTerminated={onTerminated}>
        <div>live interview</div>
      </ProctoringGuard>,
    )
    // Arm window (800ms settle).
    act(() => { vi.advanceTimersByTime(900) })
    // Simulate a tab switch.
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).toHaveBeenCalledWith('tab_switch')
    expect(endMock).toHaveBeenCalled()
  })

  it('negative control: with proctoring disabled, no listeners terminate the session', () => {
    vi.useFakeTimers()
    const onTerminated = vi.fn()
    render(
      <ProctoringGuard token="t" config={{ ...cfg, enabled: false }} onTerminated={onTerminated}>
        <div>live interview</div>
      </ProctoringGuard>,
    )
    act(() => { vi.advanceTimersByTime(2000) })
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).not.toHaveBeenCalled()
    expect(screen.getByText('live interview')).toBeInTheDocument()
  })
})
```

- [ ] **Step 5: Run the composition test + full frontend suite**

Run:
```bash
cd frontend/session
npm run test -- proctoring-guard.composition
npm run test
npm run type-check
npm run lint
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/session/components/interview/app/app.tsx frontend/session/components/interview/app/view-controller.tsx frontend/session/components/interview/app/welcome-view.tsx frontend/session/tests/components/interview/proctoring/proctoring-guard.composition.test.tsx
git commit -m "feat(session-fe): wire ProctoringGuard into live session + disclosure + terminated routing"
```

---

## Task 11: Threat-model documentation

**Files:**
- Modify (or create): `docs/security/threat-model.md`

- [ ] **Step 1: Add the proctoring section**

Append a "Candidate proctoring (2026-05-21)" section to `docs/security/threat-model.md` (create the file with a short header if it doesn't exist). Content must state:
- The new endpoint `POST /api/candidate-session/{token}/proctoring/event` (candidate-JWT auth, tenant-scoped, no PII, declared rate limit 60/min-per-token & 120/min-per-IP).
- What proctoring stops (casual tab/window switching, reflexive F12/right-click, docked or already-open devtools in the common case).
- What it cannot stop (tampered client blocking/patching the POST; devtools opened pre-navigation, breakpoints disabled to defeat the `debugger` trap, remote debugger; second device; size-delta false positives from zoom/docked panels — mitigated by baseline-delta + the `debugger` trap and by recording every termination *with its reason* for human review).
- The conclusion: proctoring is a deterrent + evidence-recorder, not a guarantee.

- [ ] **Step 2: Commit**

```bash
git add docs/security/threat-model.md
git commit -m "docs(security): threat model — candidate proctoring surface + honest limitations"
```

---

## Self-review notes (addressed)

- **Spec coverage:** migration (T1) ✓; terminated state + schemas + tenant config (T2) ✓; severity/threshold logic (T3) ✓; service + start/rejoin/pre-check embed (T4) ✓; endpoint + rate-limit declaration (T5) ✓; API client (T6) ✓; controller (T7) ✓; five detector hooks incl. armed-gate/dedupe/debounce (T8) ✓; border/overlay/guard/ended-screen + disclosure (T9, T10) ✓; correctness guards: armed-gate (ProctoringGuard arm effect), self-induced suppression (fullscreen `hasEntered` ref + arm-settle window), dedupe (focus guard defers to visibility; controller `terminatedRef` makes the first hard violation win) ✓; fail-safe (controller hard path ends regardless of POST) ✓; mobile degradation (fullscreen guard `requestFullscreen?.()` optional-chaining; if absent it never abandons) ✓; threat model (T11) ✓.
- **Type consistency:** `report(kind: ProctoringKind)` is the single detector→controller signature (`GuardArgs.onViolation`); `onTerminated(reason: ProctoringTermination)` flows controller→ProctoringGuard→app→view-controller; `ProctoringConfig` shape identical across backend schema, API client, and frontend props.
- **Known test gaps (documented, acceptable):** the `debugger;` trap and real Fullscreen API can't be exercised in jsdom — covered logic is the size-delta heuristic, visibility/focus/keyboard, controller policy, and the composition flow.
```
