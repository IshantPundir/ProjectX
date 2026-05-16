# Engine failure handling — durable error path for interview sessions

**Date:** 2026-05-16
**Surface:** `backend/nexus` (FastAPI + interview engine), `frontend/session`, `frontend/app`
**Status:** spec

---

## Problem

The interview engine's `entrypoint()` (`app/modules/interview_engine/agent.py:237`) has
no failure handler for crashes that occur before `session.start()` completes.

When `build_session_config(...)` raised `pydantic.ValidationError` on a long
`company_profile.about` field (2026-05-16 incident, session
`c795c0b4-08eb-4939-ae6c-393ae19f651c`), the following silent-failure chain played out:

1. The engine process died at line 254.
2. `_handle_close` was never wired (wiring happens at line 438, after the crash site),
   so the LiveKit `CloseEvent` never fired the persistence path.
3. No `session_outcome` attribute was published — the candidate's frontend kept
   spinning indefinitely on "starting interview…".
4. The `sessions` row stayed in `state='active'` forever. No audit row, no `error_code`,
   no observable signal anywhere downstream.
5. LiveKit Cloud respawned a fresh worker process (visible in logs), but it was
   stateless and did not reconnect to the dead room.

The bug class is broader than the specific `ValidationError`: **any uncaught
exception in the pre-`session.start()` window** has the same shape — config-fetch
failures, room-connect failures, plugin construction failures, anything raised
before line 458.

The wire contract for the failure case is already laid:

- `sessions.state` accepts `'error'` per the documented state machine
  (`created → pre_check → consented → active → completed | cancelled | error`).
- `sessions.error_code TEXT NULL` exists (migration 0024).
- The backend `SessionOutcome` literal (`agent.py:113`) includes `'error'`.
- The frontend `SESSION_OUTCOMES` const (`frontend/session/components/interview/lib/session-outcome.ts:12`) includes `'error'`.
- The frontend `useSessionOutcome` hook already subscribes to the agent
  participant's `session_outcome` attribute.

**Nothing in the codebase writes to any of these.** `grep` for `state.*=.*"error"`
returns zero matches in `app/modules/session/`. The column is reserved, the literal
is declared, the frontend hook exists, but no code path connects them.

## Goal

Connect the unused wire so that every engine failure leaves a durable, observable
trace and every consumer (candidate, recruiter, ops) can see what happened.

Specifically:

1. Every uncaught exception in `entrypoint()` results in a session row transitioned
   to `state='error'` with a coded `error_code`, an audit row, and (best-effort) a
   `session_outcome='error'` attribute published to the LK room.
2. A background reaper detects sessions stuck in `state='active'` past a threshold
   and force-transitions them to `state='error'` with `error_code='engine_unresponsive'`
   — catches process-kill / OOM / dispatch-never-arrived cases the in-process handler
   can't.
3. The candidate sees a friendly error screen with retry guidance keyed off either
   the LK room attribute or an HTTP state poll (whichever surfaces the failure first).
4. The recruiter tracker shows an error badge on the candidate's card with a
   human-readable reason translated from `error_code`.

## Non-goals

- **Auto-retry on transient errors.** Recruiter clicks "re-send invite" using the
  existing scheduler flow. Matches the "Borderline = human review" principle elsewhere
  in the system.
- **Cross-tenant scheduling primitive.** The reaper is a single in-process AsyncIO
  scheduler with PG advisory lock for multi-replica safety. Not Celery Beat, not
  EventBridge, not Kubernetes CronJob — those become options later if traffic demands.
- **Session resumption from checkpoint.** The `engine_checkpoint` column exists
  (migration 0029) but mid-session crash recovery is a separate problem; this spec
  only addresses *never-started* and *abandoned-while-active* sessions.
- **Sentry wiring.** The candidate-session app does not log to a third party yet
  (per root CLAUDE.md). The failure handler writes structured logs + an audit row;
  Sentry integration is a separate PR with its own threat-model update.

## Decisions made during brainstorming

| Decision | Choice | Reason |
|---|---|---|
| Engine handler structure | All-in-one try/except wrapping `_run_entrypoint(...)` (Approach A) | Smallest delta over current code; mirrors existing `_handle_close` lifecycle pattern; easy to test the handler in isolation |
| Retry path | Recruiter-driven re-invite | Simplest; matches the "human-in-the-loop on failure" principle; no transient/hard taxonomy needed |
| Reaper infra | `apscheduler.AsyncIOScheduler` inside FastAPI lifespan + `pg_try_advisory_lock` | Zero new infra; one well-known dep; naturally handles multi-replica leadership |
| Reaper threshold | 15-minute static (env-tunable) | Conservative — typical AI screen is 30 min; a 15-min idle on `active` is dead. Static is easier to reason about than per-stage dynamic; revisit when we have real session data |
| Error code taxonomy | 6 fixed codes + CHECK constraint | Closes the "free-text column drifts to arbitrary strings" risk; finite enumeration makes the FE label map straightforward |
| Candidate FE detection | Both LK attribute AND HTTP state poll fallback | Pre-connect failures never publish the attribute; HTTP poll covers that gap. LK attribute is preferred when present (real-time) |
| Length validation on `about` / `hiring_bar` | Free-text everywhere (separate small fix already landed on `feature/tracker-page`) | Original bug-of-the-week; addressed in the prior turn. Spec mentioned for context only |

---

## Architecture

```
                                ┌──────────────────────────────────────────┐
                                │  Candidate                               │
                                │  frontend/session                        │
                                │                                          │
                                │  useSessionOutcome() reads from agent    │
                                │  participant attributes [existing]       │
                                │                                          │
                                │  useSessionStateFallback() polls         │
                                │  GET /api/candidate-session/{token}/     │
                                │  state every 5s [NEW]                    │
                                │                                          │
                                │  OutcomeWatcher 'error' branch renders   │
                                │  <SessionErrorScreen/> [NEW]             │
                                └────────────▲─────────────────────────────┘
                                             │
                                             │ LiveKit room attrs + HTTP poll
                                             │
       ┌─────────────────────────────────────┴────────────────────────────┐
       │                                                                  │
       │  Backend (Nexus + Engine)                                        │
       │                                                                  │
       │  ┌────────────────────────────┐  ┌──────────────────────────┐    │
       │  │ Engine entrypoint()        │  │ Reaper (apscheduler)     │    │
       │  │ agent.py [REFACTOR]        │  │ session/reaper.py [NEW]  │    │
       │  │                            │  │                          │    │
       │  │ try:                       │  │ Every 5min:              │    │
       │  │   await _run_entrypoint()  │  │   pg_try_advisory_lock   │    │
       │  │ except Exception as exc:   │  │   SELECT stuck sessions  │    │
       │  │   _handle_entrypoint_      │  │   for each: transition   │    │
       │  │     failure(exc, ...)      │  │     to 'error'           │    │
       │  │   raise                    │  │     code='engine_un-     │    │
       │  │                            │  │           responsive'    │    │
       │  └──────────┬─────────────────┘  └──────────┬───────────────┘    │
       │             │                                │                   │
       │             └────────────┬───────────────────┘                   │
       │                          │                                       │
       │                          ▼                                       │
       │      ┌──────────────────────────────────────────────────┐        │
       │      │ session.service.transition_to_error()  [NEW]     │        │
       │      │                                                  │        │
       │      │ Atomic UPDATE gated on state IN (active, consent)│        │
       │      │ Sets: state='error', error_code, state_changed   │        │
       │      │ Writes audit_log row 'session.errored'           │        │
       │      └──────────────────────────────────────────────────┘        │
       │                                                                  │
       │      ┌──────────────────────────────────────────────────┐        │
       │      │ session.error_codes.classify_engine_exception()  │        │
       │      │ [NEW]                                            │        │
       │      │ Exception type -> ErrorCode literal              │        │
       │      └──────────────────────────────────────────────────┘        │
       │                                                                  │
       └──────────────────────────────────────────────────────────────────┘
                                             │
                                             │ existing tracker API + new badge
                                             ▼
                                ┌──────────────────────────────────────────┐
                                │  Recruiter                               │
                                │  frontend/app/.../tracker/[jobId]/page   │
                                │                                          │
                                │  Candidate card shows error badge with   │
                                │  human-readable reason from error_code   │
                                │  [SessionStatusBadge extension]          │
                                └──────────────────────────────────────────┘
```

### File layout

```
backend/nexus/
├── app/
│   ├── main.py                                       MODIFY — lifespan adds reaper scheduler
│   ├── config.py                                     MODIFY — reaper_* settings
│   ├── modules/
│   │   ├── interview_engine/
│   │   │   └── agent.py                              REFACTOR — extract _run_entrypoint, add try/except
│   │   └── session/
│   │       ├── error_codes.py                        NEW — ErrorCode literal + classify_engine_exception
│   │       ├── service.py                            MODIFY — add transition_to_error()
│   │       ├── reaper.py                             NEW — run_stuck_session_reaper()
│   │       ├── router.py                             MODIFY — add GET /api/candidate-session/{token}/state
│   │       └── schemas.py                            MODIFY — CandidateSessionStateResponse
│   └── migrations/versions/
│       └── 0039_session_error_code_check.py          NEW — CHECK constraint on sessions.error_code
└── tests/
    ├── session/
    │   ├── test_transition_to_error.py               NEW
    │   ├── test_error_codes.py                       NEW
    │   ├── test_reaper.py                            NEW
    │   └── test_candidate_state_endpoint.py          NEW
    └── interview_engine/
        └── test_entrypoint_failure.py                NEW

frontend/session/
├── components/interview/
│   ├── screens/
│   │   └── session-error-screen.tsx                  NEW
│   ├── app/hooks/
│   │   └── use-session-state-fallback.ts             NEW
│   └── lib/
│       └── session-error-messages.ts                 NEW
├── lib/api/
│   └── candidate-session.ts                          MODIFY — add getState()
└── tests/
    ├── components/interview/
    │   ├── session-error-screen.test.tsx             NEW
    │   └── outcome-precedence.test.tsx               NEW
    └── hooks/
        └── use-session-state-fallback.test.ts        NEW

frontend/app/
├── components/dashboard/tracker/
│   ├── SessionStatusBadge.tsx                        MODIFY — add 'error' branch + error_code label
│   └── session-error-labels.ts                       NEW
├── lib/api/
│   └── tracker.ts (or candidates.ts)                 MODIFY — kanban response includes latest session state + error_code
└── tests/components/tracker/
    └── session-status-badge.test.tsx                 NEW
```

---

## Error code taxonomy

`sessions.error_code` is currently free `TEXT`. Migration `0039_session_error_code_check.py`
adds a CHECK constraint pinning it to the enumerated set below. The column comment
documents the contract.

| `error_code` | When it's set | Set by | Candidate UX category |
|---|---|---|---|
| `engine_session_config_invalid` | `build_session_config` raised — schema validation, missing FK, etc. | entrypoint handler | "Configuration issue" |
| `engine_company_profile_missing` | `CompanyProfileMissingError` from runtime | entrypoint handler | "Not fully set up" |
| `engine_question_bank_not_ready` | `QuestionBankNotReadyError` (status≠confirmed or is_stale) | entrypoint handler | "Not fully set up" |
| `engine_room_join_failed` | `ctx.connect()` / `wait_for_participant()` raised | entrypoint handler | "Internal error" |
| `engine_internal_error` | Unhandled exception in entrypoint (catch-all) | entrypoint handler | "Internal error" |
| `engine_unresponsive` | Session stuck in `active` past threshold | reaper | "Interview never started" |

### Classifier

`app/modules/session/error_codes.py`:

```python
"""Error code taxonomy for engine-driven session failures.

The literal values here are pinned by a CHECK constraint on
sessions.error_code (migration 0039). Adding a value requires:
  1. Update the Literal.
  2. Update the CHECK constraint via a new migration.
  3. Update the two frontend label maps.
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
    """
    if isinstance(exc, CompanyProfileMissingError):
        return "engine_company_profile_missing"
    if isinstance(exc, QuestionBankNotReadyError):
        return "engine_question_bank_not_ready"
    # ValidationError from build_session_config — bucket as config_invalid.
    # Import locally to avoid hard-coupling this module to pydantic_core's
    # internal class name; we identify it by qualified name.
    if type(exc).__name__ == "ValidationError" and type(exc).__module__.startswith(
        "pydantic"
    ):
        return "engine_session_config_invalid"
    # Add livekit ConnectError / TimeoutError handling once we identify the
    # actual types raised by ctx.connect()/wait_for_participant — verify
    # during implementation. Until then, those land in engine_internal_error.
    return "engine_internal_error"
```

### Migration

```python
# migrations/versions/0039_session_error_code_check.py
"""sessions.error_code CHECK constraint."""

from alembic import op

revision = "0039"
down_revision = "0038"


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

Dev database has zero `error_code` values today — no backfill needed. The
CHECK is safe to add directly.

---

## Engine entrypoint failure handler

### Refactor

`agent.py:237 entrypoint()` is refactored to lift the metadata parse above a
single try/except. Everything from `async with get_bypass_session() as db: …`
down through `await session.start(...)` moves into a new private
`_run_entrypoint(...)` — pure code motion, no behavior change inside.

```python
@server.rtc_session(agent_name=settings.engine_agent_name)
async def entrypoint(ctx: JobContext) -> None:
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

### The handler

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

    DB transition is first so the candidate's HTTP fallback poll wins even
    if the room/attribute publish fails. Re-raise (in caller) keeps the
    LiveKit framework's existing crash signal intact.
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

### The shared transition function

`app/modules/session/service.py` gains:

```python
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
    completed / cancelled / already-error row. The boolean return lets the
    reaper distinguish 'I just claimed this stuck row' from 'someone else
    transitioned it first.'

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

### Concurrency: handler vs `_handle_close`

The handler may race with `_handle_close` if `session.start()` partially succeeded
before the exception. Resolution:

- `_handle_close`'s persistence path transitions `state → 'completed'` (via
  `record_session_result`).
- `transition_to_error` is gated on `state IN ('consented', 'active')`.
- Whichever lands the UPDATE first wins; the loser sees `rowcount=0` and no-ops.
- The audit log gets exactly one of `session.errored` / `engine.session.completed`,
  not both.

---

## The reaper

### Wiring

`app/main.py` lifespan adds the scheduler:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.modules.session.reaper import run_stuck_session_reaper


@asynccontextmanager
async def lifespan(app: FastAPI):
    # …existing startup: Base.registry.configure(), _assert_rls_completeness …

    scheduler = AsyncIOScheduler(timezone="UTC")
    if settings.reaper_enabled:
        scheduler.add_job(
            run_stuck_session_reaper,
            trigger="interval",
            seconds=settings.reaper_interval_seconds,
            id="stuck_session_reaper",
            max_instances=1,   # in-process concurrency guard
            coalesce=True,     # missed ticks collapse to one run
        )
        scheduler.start()
        log.info("reaper.scheduler.started", interval=settings.reaper_interval_seconds)
    app.state.reaper_scheduler = scheduler

    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
```

### The sweeper

`app/modules/session/reaper.py`:

```python
"""Stuck-session reaper.

Single-flight via pg_try_advisory_lock — concurrent ticks across replicas
return immediately on lock contention. The reaper targets state='active'
rows that have not changed state for `reaper_stuck_threshold_seconds`
and have no agent_completed_at — the empirical signature of an engine
that died without ever transitioning the session.
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

### Settings

`app/config.py` adds:

```python
reaper_enabled: bool = True   # tests set False to disable wallclock-driven ticks
reaper_interval_seconds: int = 300  # 5 min
reaper_stuck_threshold_seconds: int = 900  # 15 min
```

`.env.example` is updated with the three knobs and their rationale.

### Why advisory lock, not row-level

`SELECT … FOR UPDATE SKIP LOCKED` would serialize per-row but not per-sweep —
two replicas ticking simultaneously would each find the same stuck rows, contend
on `transition_to_error` (which gates atomically and is safe), but would spam
the audit log with N concurrent attempts. The advisory lock is the cheaper coordination.

### Why query `state_changed_at < cutoff`, not `started_at < cutoff`

`started_at` only gets set when the engine successfully transitions to `active`.
For sessions that crashed pre-`active` (which the entrypoint handler covers), the
row would still be in `state='consented'`. The reaper today targets only
`state='active'` so it doesn't double-write rows the handler already moved.
Extending coverage to orphaned-`consented` rows is a future change with its
own threshold; out of scope.

---

## Candidate frontend error UX

### New endpoint: candidate state poll

```python
# app/modules/session/schemas.py
class CandidateSessionStateResponse(BaseModel):
    state: SessionState              # 'created' | 'pre_check' | … | 'error'
    error_code: ErrorCode | None
    state_changed_at: datetime


# app/modules/session/router.py — added to candidate_session_router
@candidate_session_router.get("/state", response_model=CandidateSessionStateResponse)
async def get_candidate_session_state(
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> CandidateSessionStateResponse:
    """Minimal state read for the candidate's fallback poll.

    Auth: candidate JWT in path (verified by middleware). Tenant-scoped via
    the verified token's claims. Returns state + error_code only — no
    transcript, no questions, no PII. Rate-limited at 12/min/token.
    """
    payload = request.state.candidate_token_payload
    session_id = uuid.UUID(payload["session_id"])
    tenant_id = uuid.UUID(payload["tenant_id"])
    row = (
        await db.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return CandidateSessionStateResponse(
        state=row.state,
        error_code=row.error_code,
        state_changed_at=row.state_changed_at,
    )
```

Rate limit (per root CLAUDE.md → Rate Limiting & Abuse Posture): 12/min per IP, 12/min per token.

### New hook: `useSessionStateFallback`

`frontend/session/components/interview/app/hooks/use-session-state-fallback.ts`:

```typescript
'use client'

import { useEffect, useState } from 'react'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import type { CandidateSessionState } from '@/lib/api/candidate-session'

/**
 * Polls /state every 5s once the candidate clicks Start.
 *
 * Stops polling on terminal state (error / completed / cancelled).
 * Mirrors the engine's session_outcome attribute path for cases where
 * the engine crashed before publishing the attribute (pre-room-connect
 * failures). When the LK attribute also arrives, OutcomeWatcher's
 * precedence rule wins on whichever surfaces first.
 */
export function useSessionStateFallback(
  token: string,
  enabled: boolean,
): CandidateSessionState | null {
  const [state, setState] = useState<CandidateSessionState | null>(null)

  useEffect(() => {
    if (!enabled) return
    let stopped = false

    const tick = async () => {
      if (stopped) return
      try {
        const next = await candidateSessionApi.getState(token)
        if (stopped) return
        setState(next)
        if (next.state === 'error' || next.state === 'completed' || next.state === 'cancelled') {
          return  // terminal — stop the loop
        }
      } catch (err) {
        // Network blip — keep polling. 4xx (e.g. token superseded) lets
        // the existing error landing handle it on next user navigation.
      }
      if (!stopped) setTimeout(tick, 5000)
    }
    tick()

    return () => { stopped = true }
  }, [token, enabled])

  return state
}
```

### New screen: `<SessionErrorScreen/>`

`frontend/session/components/interview/screens/session-error-screen.tsx` reads
the `error_code` (from the HTTP poll response — the LK attribute carries
`session_outcome='error'` only, no code) and renders:

- Friendly headline keyed off the code's UX category (4 categories: "Configuration
  issue" / "Not fully set up" / "Internal error" / "Interview never started").
- Body text explaining what happens next (recruiter will resend invite).
- No retry button.
- Footer with the session ID and a `support@projectx.com` placeholder mailto (will be
  templated by a real address later).

`frontend/session/components/interview/lib/session-error-messages.ts`:

```typescript
import type { ErrorCode } from './error-codes'

interface ErrorCopy {
  headline: string
  body: string
}

export const SESSION_ERROR_COPY: Record<ErrorCode, ErrorCopy> = {
  engine_session_config_invalid: {
    headline: 'We hit a configuration issue',
    body: 'Your interview couldn\'t be set up correctly. Your recruiter has been notified and will send a new invite.',
  },
  engine_company_profile_missing: {
    headline: 'Your interview isn\'t fully set up',
    body: 'Some company information is missing. Your recruiter will reach out shortly.',
  },
  engine_question_bank_not_ready: {
    headline: 'Your interview isn\'t fully set up',
    body: 'The questions for this interview aren\'t ready yet. Your recruiter will reach out shortly.',
  },
  engine_room_join_failed: {
    headline: 'Something went wrong on our side',
    body: 'We couldn\'t connect to your interview room. Your recruiter will resend the invite.',
  },
  engine_internal_error: {
    headline: 'Something went wrong on our side',
    body: 'Your recruiter has been notified and will resend the invite.',
  },
  engine_unresponsive: {
    headline: 'Your interview didn\'t start',
    body: 'The interview was abandoned without progress. Your recruiter will reach out to reschedule.',
  },
}

const FALLBACK: ErrorCopy = {
  headline: 'Something went wrong',
  body: 'Your recruiter will be in touch with next steps.',
}

export function copyForErrorCode(code: string | null | undefined): ErrorCopy {
  if (!code) return FALLBACK
  return SESSION_ERROR_COPY[code as ErrorCode] ?? FALLBACK
}
```

### Outcome precedence

The existing `OutcomeWatcher` reads from `useSessionOutcome()`. The new fallback
hook returns a state snapshot. Precedence rule:

1. If `useSessionOutcome()` returns `'error'`, render `<SessionErrorScreen/>` with
   `error_code=null` (the LK attribute doesn't carry the code; the screen renders
   the fallback copy).
2. Otherwise if `useSessionStateFallback()` returns `state='error'`, render
   `<SessionErrorScreen/>` with the response's `error_code`.
3. The screen never renders both — the first match wins and is sticky (matches the
   existing `useSessionOutcome` "last seen value sticks" pattern).

Trade-off: the LK-attribute path is faster (sub-second) but doesn't carry the code.
The HTTP-poll path is slower (up to 5s) but carries the full code. In practice, the
HTTP path almost always wins for the entrypoint-failure case (no attribute is ever
published), and the LK path wins for mid-session errors (where the close handler
publishes the attribute). Both produce a clean error screen.

---

## Recruiter frontend tracker

### Data shape

Today, `useTrackerJob(jobId)` (or the equivalent kanban-loader hook in
`frontend/app/lib/hooks/`) returns the kanban response — candidates grouped by
stage. Each candidate card needs to surface:

- The latest session's `state` (`active`, `completed`, `error`, …)
- The session's `error_code` if `state='error'`

Backend change: `/api/candidates/kanban` (or whichever endpoint feeds the tracker)
extends each candidate row with `latest_session: { state, error_code, stage_id } | null`.
"Latest" is defined as: filter sessions to the candidate's *current* stage (the one
the assignment is currently in per `candidate_job_assignments.current_stage_id`),
then pick the most-recent by `state_changed_at`. Selecting on `current_stage_id`
keeps the badge contextual to the column the candidate appears in on the board —
an old `state='error'` row on a stage the candidate already moved past should not
plaster a red badge on the card. If no session row exists for the current stage,
`latest_session=null` and the badge is omitted entirely.

### `<SessionStatusBadge/>` extension

`frontend/app/components/dashboard/candidates/SessionStatusBadge.tsx` (existing
file per root CLAUDE.md) gains an `error` branch:

```typescript
import { labelForErrorCode } from '@/components/dashboard/tracker/session-error-labels'

if (state === 'error') {
  return (
    <Badge variant="destructive" tooltip={`error_code: ${error_code ?? 'unknown'}`}>
      Failed: {labelForErrorCode(error_code)}
    </Badge>
  )
}
```

`session-error-labels.ts`:

```typescript
export const SESSION_ERROR_LABELS: Record<string, string> = {
  engine_session_config_invalid: 'Configuration error',
  engine_company_profile_missing: 'Company profile incomplete',
  engine_question_bank_not_ready: 'Question bank not ready',
  engine_room_join_failed: 'Couldn\'t reach interview room',
  engine_internal_error: 'Internal error',
  engine_unresponsive: 'Interview never started',
}

export function labelForErrorCode(code: string | null | undefined): string {
  if (!code) return 'Failed'
  return SESSION_ERROR_LABELS[code] ?? 'Failed'
}
```

The card's existing `Re-send invite` action remains the recruiter's retry handle
— no new button needed.

---

## Testing strategy

Tests are layered — the highest-leverage ones are at the boundary where the bug
manifested. Coverage targets meet the root CLAUDE.md gates (100% branch for
`app/modules/session/service.py`, candidate-session path, and proxy/middleware).

### Backend unit tests

1. **`tests/session/test_transition_to_error.py`** (5 tests)
   - rowcount=1 on `active → error`; audit row written.
   - rowcount=1 on `consented → error`; audit row written.
   - rowcount=0 on `completed → error` (no clobber); no audit row.
   - rowcount=0 on `error → error` (idempotent); no duplicate audit.
   - Concurrent: two `transition_to_error` calls on the same row from
     parallel sessions; exactly one wins, exactly one audit row.

2. **`tests/session/test_error_codes.py`** (1 parametrized test)
   - Each typed exception (`CompanyProfileMissingError`,
     `QuestionBankNotReadyError`, pydantic `ValidationError`,
     `RuntimeError`) maps to the expected code.

3. **`tests/interview_engine/test_entrypoint_failure.py`** (3 tests)
   - **Regression test for the original bug**: synthesize a JobContext with a
     metadata blob; call `_run_entrypoint` with mocked `build_session_config` that
     raises `ValidationError`; assert the handler runs, the DB row transitions
     to `state='error'` with `error_code='engine_session_config_invalid'`, the
     audit row is written, the outcome attribute is set on a fake room.
   - Pre-connect failure: `ctx.connect()` raises; assert the handler's
     `_best_effort_publish_outcome_attribute` swallows the error and the DB
     transition still happens.
   - Success path is untouched: a successful `_run_entrypoint` doesn't trigger
     the handler.

4. **`tests/session/test_reaper.py`** (4 tests)
   - Seed three sessions: one `active` past threshold, one `active` within threshold,
     one `completed`. Run one tick. Only the first transitions.
   - Advisory lock contention: call the reaper twice in parallel; only one
     acquires the lock, the other returns immediately with `reaper.lock.contended`.
   - Audit log: a successful tick writes exactly one `session.errored` row per
     transitioned session.
   - Idempotency: running the reaper twice back-to-back transitions on the first
     run and is a clean no-op on the second.

5. **`tests/session/test_candidate_state_endpoint.py`** (3 tests)
   - Happy path: valid token → 200, response matches DB row.
   - Cross-tenant token (token with `tenant_id` mismatching the session's
     `tenant_id`) → 404 (not 401 — leak prevention; matches existing pattern in
     `build_session_config`).
   - Rate limit: 13th call within 60s → 429.

### Frontend tests

6. **`frontend/session/tests/components/interview/session-error-screen.test.tsx`** (1 parametrized)
   - Every `error_code` renders the right headline/body.
   - Unknown code renders fallback copy.

7. **`frontend/session/tests/hooks/use-session-state-fallback.test.ts`** (4 tests)
   - Polls every 5s while `enabled=true`.
   - Stops polling on terminal state.
   - Surfaces `error_code` from the response.
   - Network errors don't stop the loop (keeps polling).

8. **`frontend/session/tests/components/interview/outcome-precedence.test.tsx`** (2 tests)
   - LK attribute wins when it surfaces before the HTTP poll.
   - HTTP fallback wins when the LK attribute never arrives.

9. **`frontend/app/tests/components/tracker/session-status-badge.test.tsx`** (1 parametrized)
   - Each `(state, error_code)` combo renders the right badge.
   - Unknown `error_code` falls back to `"Failed"`.

### Manual end-to-end (CI not yet wired)

10. **Reproduce the original bug shape:**
    - Branch off `feature/tracker-page`. In `app/modules/interview_runtime/schemas.py`,
      temporarily revert `CompanyContext.about` to `max_length=500`.
    - With Workato's long-text profile in place, run a candidate session.
    - Confirm:
      - Engine logs `engine.entrypoint.failed error_code=engine_session_config_invalid`.
      - DB row transitions to `state='error', error_code='engine_session_config_invalid'`.
      - Audit log row `session.errored` lands.
      - Candidate frontend renders `<SessionErrorScreen/>` with "Configuration issue" copy.
      - Recruiter tracker shows `Failed: Configuration error` badge on the card.
    - Revert the temporary `max_length=500`. Confirm a fresh session works end-to-end.
    - Document the manual repro in the implementation PR description.

### Pre-existing items addressed in implementation

- **Circular import in `app/modules/interview_runtime/schemas.py`** (the
  "leaf-direct import" claim that doesn't actually work — pytest collection fails
  for any bare `from app.modules.interview_runtime.schemas import …`). Fix is to
  move `TranscriptEntry` out of `interview_runtime.schemas` into a leaf module
  (`app/modules/interview_runtime/transcript_entry.py`) that engine.models.speaker
  can import without re-entering the runtime package's `__init__.py`. Existing
  imports in `interview_runtime.schemas` re-export it for back-compat. Verifies
  via removing the workaround imports in `tests/interview_runtime/test_schemas.py`.
- **One stuck dev row** (`c795c0b4-08eb-4939-ae6c-393ae19f651c`, `state='active'`).
  One-off SQL in the implementation PR description; not encoded as a migration
  (no production fallout, dev-only).

---

## Rollout

1. Migration `0039` adds the CHECK constraint. Zero rows violate it (dev DB has
   no `error_code` values). Safe to deploy directly.
2. Backend changes (engine handler + service + reaper + endpoint) ship as one PR.
   The reaper is gated by `reaper_enabled` (default `True`); a future ops-driven
   disable is one env var away.
3. Frontend changes (session error screen + state poll + tracker badge) ship as
   a second PR, after the backend is on `main`. The two surfaces consume the new
   backend independently.
4. Manual E2E walkthrough (see Testing #10) verifies the chain end-to-end.

## Future work (out of scope)

- **Sentry wiring on the candidate surface.** Today the failure logs land in
  structlog only. When Sentry comes online (separate PR), `engine.entrypoint.failed`
  events should fire a Sentry event with the error_code as a tag.
- **Reaper extension to `state='consented'` orphans.** A candidate who completes
  OTP and then closes the browser leaves a row in `state='consented'` forever.
  Same reaper shape, different threshold, different `error_code`. Defer until
  observed in dev.
- **Per-stage dynamic reaper threshold.** Static 15min is fine until we see
  long-duration stages getting falsely reaped. Trivial to extend the query with
  a join on `job_pipeline_stages.duration_minutes` when we have data.
- **Self-healing dispatch retry.** If `engine_unresponsive` is observed N times
  for the same assignment, surface it differently in the tracker (e.g. "Persistent
  failure — escalate"). Not before we have real failure-rate data.
