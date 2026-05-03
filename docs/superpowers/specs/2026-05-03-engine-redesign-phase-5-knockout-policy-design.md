# Engine Redesign — Phase 5: Knockout policy + tenant settings

**Status:** Draft for user review · **Date:** 2026-05-03 · **Phase:** 5 of 6 in the engine-redesign arc

## Summary

Phase 5 ships the **persisted knockout-failure artifact** and the **per-tenant
configuration surface** that controls how the engine reacts to a knockout. After
Phase 5:

- A new `tenant_settings` table holds two columns: `engine_knockout_policy`
  (`record_only` | `close_polite`, default `record_only`) and
  `engine_agent_name` (TEXT, nullable — env fallback when null).
- The engine reads `tenant_settings` at session start and threads the values
  through the controller. The `close_polite` stub already at
  `controller.py:438` (left there by Phase 2) is wired to actually fire
  `_terminate(outcome="knockout_closed")`.
- A new `KnockoutFailure` Pydantic model lands in `interview_runtime.schemas`
  with a defense-in-depth source-side PII scrub, replacing the in-memory
  `KnockoutFailureRecord` dataclass at `controller.py:62`.
- `SessionResult.knockout_failures` (list) is persisted to a dedicated
  `sessions.knockout_failures` JSONB column — queryable for Phase 3D
  analytics + EEOC fairness reviews.
- The 6-state `session_outcome` enum (already shipped backend-side in Phase 2
  at `outcome_close.py:19-26`) is fully wired through the frontend:
  `useSessionOutcome` returns a typed Literal union, `OutcomeWatcher` switches
  exhaustively over all 6 values, and a new `CANDIDATE_UNRESPONSIVE` code is
  added to `DisconnectError`.

Behavior change in production interviews:

- With `engine_knockout_policy='record_only'` (the default for every existing
  tenant), behavior is **identical** to Phase 4: knockouts accumulate in the
  in-memory list and now also land in `sessions.knockout_failures`, but the
  interview continues to its natural end.
- With `engine_knockout_policy='close_polite'`, the interview gracefully
  closes after any knockout fires (no failure reason mentioned to the
  candidate per `outcome_close.py::knockout_closed` instructions).
- The candidate's spoken-name override (`engine_agent_name`) only takes
  effect for tenants that have an explicit row with a non-null value.

This phase consumes 4 of the 21 decisions from the
[overview spec](2026-05-02-interview-engine-redesign-overview-design.md):

- **Decision #4** — knockout policy default is `record_only`, tenant-overridable.
- **Decision #17** — the 6-value `session_outcome` enum is the lever for frontend UI.
- **Decision #18** — fairness sign-off for any candidate-facing prompt change
  (Phase 5 doesn't change any prompt body, so no new sign-off is required —
  see §6.3).
- **Decision #19** — `tenant_settings` is data-model-ready from day one;
  recruiter UI to edit settings is post-arc.

## 1 — Decisions locked in this phase's brainstorm

| # | Open question (overview §12.5) | Decision |
|---|---|---|
| P5-Q1 | `tenant_settings.engine_agent_name` ↔ `@server.rtc_session` decorator constraint | **Accept the constraint.** The `agent_name` arg in the decorator at `agent.py:130` and the `dispatch_agent(agent_name=…)` call at `livekit.py:102` STAY on the env value (`settings.engine_agent_name`) — that pair is a fleet-wide LiveKit routing label, not a candidate-facing identifier. The per-tenant override applies only to (a) the prompt substitution at `controller.py:93` (what the candidate hears) and (b) a NEW `controller.started` structured-log line that records the displayed name + override status. The existing `agent.py:154` `engine.dispatch.received` log STAYS on env (it fires before tenant_settings are read; semantically it logs the routing identity). |
| P5-Q2 | `SessionResult.knockout_failures` storage shape | **Dedicated `sessions.knockout_failures` JSONB column** (default `'[]'`). Queryable for Phase 3D analytics (`WHERE knockout_failures != '[]'`); EEOC fairness review can pull aggregates without JSONB-path queries; mirrors the convention already in place where `transcript`, `questions_asked`, `questions_skipped` are promoted from `raw_result_json` to dedicated columns. Aligns with overview spec §10.3. |
| P5-Q3 | `KnockoutFailure.reason` redaction policy | **Decoupled from the event-log knob.** The DB column ALWAYS persists the full reason text — Phase 3D analytics and EEOC review need it. Defense-in-depth happens at construction time via a Pydantic field validator (`_scrub_pii` runs unconditionally on every construction path including `model_validate` from DB). The existing `ENGINE_EVENT_LOG_REDACTION` env knob keeps governing only the event-log envelope (it strips `disqualify.knockout.reason` in `metadata` mode via `_CONTENT_FIELDS_BY_KIND`). Two artifacts, two consumers, two policies — no shared knob. |
| P5-Q4 | `tenant_settings` row creation strategy | **Lazy / "no row = defaults."** `get_tenant_settings(db, tenant_id)` returns a `TenantSettings(...)` populated from the row if present, or from the schema's defaults if not. No backfill for existing tenants; no plumbing into `admin/service.py::provision_client`. When the recruiter-side editing UI eventually ships (post-arc), the first edit creates the row via UPSERT. |
| P5-Q5 | `candidate_unresponsive` frontend routing | **Error path with new `CANDIDATE_UNRESPONSIVE` code** on the existing `DisconnectError` screen — not the generic `CompletionScreen`. The candidate may have had a connectivity blip / tab crash; they need the "contact your recruiter" affordance. The four graceful outcomes (`completed`, `knockout_closed`, `time_expired`, `candidate_ended`) all route to `CompletionScreen` because the agent's voice already conveyed the specific context per `outcome_close.py` instructions. |
| P5-Q6 | `useSessionOutcome` return type narrowing | **Narrow now.** Return `SessionOutcome \| null` where `SessionOutcome` is a 6-value Literal union exported from a new shared module `frontend/session/components/interview/lib/session-outcome.ts`. A runtime `isSessionOutcome` guard drops unrecognized values to null (defensive against backend/frontend version skew). `OutcomeWatcher` uses an exhaustive `switch` with `_exhaustive: never` so future outcomes can't be silently missed. |

## 2 — Scope

### 2.1 In scope

| Surface | Change |
|---|---|
| `migrations/versions/0027_tenant_settings.py` (NEW) | Creates `tenant_settings` (PK = tenant_id, FK→clients ON DELETE CASCADE, two policy/agent_name columns, canonical RLS pair with NULLIF). Adds `sessions.knockout_failures JSONB NOT NULL DEFAULT '[]'`. Both ops are PG11+ metadata-only. Down-migration drops both. |
| `app/modules/tenant_settings/__init__.py` (NEW) | Public API: `TenantSettings`, `KnockoutPolicy`, `get_tenant_settings`, `DEFAULT_TENANT_SETTINGS`. Re-exports via `__all__`. |
| `app/modules/tenant_settings/models.py` (NEW) | ORM `TenantSettingsModel`. PK = `tenant_id` (UUID, FK clients.id ON DELETE CASCADE). |
| `app/modules/tenant_settings/schemas.py` (NEW) | Pydantic `TenantSettings`; `KnockoutPolicy = Literal["record_only", "close_polite"]`. |
| `app/modules/tenant_settings/service.py` (NEW) | `get_tenant_settings(db, tenant_id) -> TenantSettings` — single SELECT; lazy-default if no row. Bypass-RLS-aware (engine call site uses bypass session; nexus-side callers can use tenant or bypass). |
| `app/modules/interview_runtime/schemas.py` | NEW `KnockoutFailure` Pydantic model with `_scrub_pii` field validator on `reason`. NEW field on `SessionResult`: `knockout_failures: list[KnockoutFailure] = Field(default_factory=list)`. |
| `app/modules/interview_runtime/__init__.py` | Re-export `KnockoutFailure` via `__all__`. |
| `app/modules/interview_runtime/service.py` (`record_session_result`) | Add `knockout_failures=[k.model_dump(mode="json") for k in result.knockout_failures]` to the `update(SessionRow).values(...)` block. |
| `app/modules/session/models.py` (`Session` ORM, line 20) | Add `knockout_failures: Mapped[list[dict]] = mapped_column(JSONB, server_default="'[]'::jsonb", nullable=False)` mapped column. |
| `app/modules/interview_engine/agent.py` | Inside the existing `async with get_bypass_session() as db:` block, add `tenant_settings = await get_tenant_settings(db, tenant_id=tenant_uuid)` after `build_session_config(...)`. Replace `tenant_policy="record_only"` (line :233) with `tenant_settings=tenant_settings`. Constructor parameter rename: `tenant_policy` → `tenant_settings`. |
| `app/modules/interview_engine/controller.py` | Replace `tenant_policy: KnockoutPolicy` constructor parameter with `tenant_settings: TenantSettings`. Derive `self._tenant_policy = tenant_settings.engine_knockout_policy` and `self._agent_name = tenant_settings.engine_agent_name or settings.engine_agent_name` in `__init__`. Pass `agent_name=self._agent_name` into `build_controller_prompt`. Wire `close_polite` at line :438 (replaces the stub comment). DELETE the `KnockoutFailureRecord` dataclass at line :62; type `_knockout_failures` as `list[KnockoutFailure]`; rebuild append site at lines :420-427 to use `KnockoutFailure(...)`. NEW structured log `controller.started` in `on_enter` recording `agent_name_displayed` + `agent_name_override_active`. |
| `app/modules/interview_engine/controller.py::build_controller_prompt` | Add `agent_name: str` parameter; substitute `agent_name=agent_name` (line :93) instead of `settings.engine_agent_name`. |
| `app/main.py::_TENANT_SCOPED_TABLES` | Append `"tenant_settings"`. |
| `tests/test_module_boundaries.py::KNOWN_DOMAIN_MODULES` | Append `"tenant_settings"`. |
| `frontend/session/components/interview/lib/session-outcome.ts` (NEW) | `SESSION_OUTCOMES` const; `SessionOutcome` Literal union; `isSessionOutcome` runtime guard. |
| `frontend/session/components/interview/app/hooks/use-session-outcome.ts` | Narrow return to `SessionOutcome \| null`; gate writes through `isSessionOutcome`. |
| `frontend/session/components/interview/app/app.tsx::OutcomeWatcher` | Replace if-chain at lines 141-150 with exhaustive `switch` + `_exhaustive: never` compile-time guard. |
| `frontend/session/components/interview/app/DisconnectError.tsx` | Add `CANDIDATE_UNRESPONSIVE` entry to `COPY` map. |
| `tests/test_tenant_settings_*.py` (NEW) | Schemas + service tests (see §5.1). |
| `tests/test_interview_runtime_knockout_failure.py` (NEW) | Model tests including PII-scrub regex coverage. |
| `tests/test_session_result_knockout_failures.py` (NEW) | Round-trip test. |
| `tests/test_migration_0027_tenant_settings.py` (NEW) | Migration apply / RLS policy presence / column add / downgrade. |
| `tests/interview_engine/integration/test_close_polite_policy.py` (NEW) | `record_only` vs `close_polite` behavior under controller. |
| `tests/interview_engine/integration/test_agent_name_override.py` (NEW) | Tenant override → prompt substitution + log. |
| `tests/interview_runtime/integration/test_record_session_result_knockout_failures.py` (NEW or extend) | Column write through `record_session_result`. |
| `frontend/session/tests/components/interview/{session-outcome,use-session-outcome,outcome-watcher,disconnect-error}.test.{ts,tsx}` (NEW or extend) | Frontend Vitest coverage (see §5.2). |
| `backend/nexus/CLAUDE.md` | Migration entry for `0027_*`; revision count line; new `tenant_settings` module entry; Phase 3D.engine-redesign-5 status block. |
| `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` | Phase 5 row in status index: `⚪ → 🟠 → 🔵 → ✅` with spec + plan paths filled in as artifacts land. |

### 2.2 Explicitly out of scope

- **Recruiter API surface for `tenant_settings`** — no router, no
  request/response schemas, no `frontend/app` UI. Decision #19 defers
  this to post-arc. The data-model-ready columns are enough for now.
- **`admin/service.py::provision_client` integration** — no row inserted
  on tenant provisioning (lazy-default pattern, P5-Q4).
- **Backfill for existing tenants** — none needed; lazy defaults cover
  the no-row case.
- **New event-log kinds** — `controller.intent.knockout_closed` is
  added to the canonical kinds list referenced in `event_log/redaction.py`'s
  module docstring, but its payload (`{"question_id": ...}`) carries no
  content fields, so no entry in `_CONTENT_FIELDS_BY_KIND` is required.
- **Prompt body changes** — `outcome_close.py::knockout_closed`
  instructions were signed off in Phase 2 and remain unchanged. No
  edits to `prompts/v1/interview/*.txt`. No new fairness sign-off
  required (§6.3).
- **Phase 6 work** — server-authoritative audio, `getUserMedia`
  constraints, e2e checklist.
- **Phase 3D analytics** — Phase 5 makes the data persistable and
  queryable; the analysis/reporting modules that consume it are
  separate work.

## 3 — Architectural shape

### 3.1 Data flow at session start (engine entrypoint)

```
agent.py::entrypoint
  ├─ parse dispatch metadata (session_id, tenant_id, correlation_id)
  ├─ structlog.contextvars.bind_contextvars(...)
  ├─ log.info("engine.dispatch.received", agent_name=settings.engine_agent_name)  ← STAYS env
  │
  ├─ async with get_bypass_session() as db:
  │     ├─ config = await build_session_config(db, session_id, tenant_id)
  │     └─ tenant_settings = await get_tenant_settings(db, tenant_id)             ← NEW
  │
  ├─ event_collector = EventCollector(...)
  ├─ agent = InterviewController(
  │     session_config=config,
  │     tenant_id=tenant_uuid,
  │     correlation_id=correlation_id,
  │     collector=event_collector,
  │     idle_nudge_config=...,
  │     budget=...,
  │     tenant_settings=tenant_settings,                                          ← was tenant_policy="record_only"
  │   )
  └─ session.start(agent, room)
```

### 3.2 Controller construction

```python
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
        tenant_settings: TenantSettings,           # was tenant_policy: KnockoutPolicy
    ) -> None:
        # ... existing fields ...
        self._tenant_policy: KnockoutPolicy = tenant_settings.engine_knockout_policy
        self._agent_name: str = tenant_settings.engine_agent_name or settings.engine_agent_name
        self._knockout_failures: list[KnockoutFailure] = []   # was list[KnockoutFailureRecord]
        # ...
        super().__init__(instructions=build_controller_prompt(session_config, agent_name=self._agent_name))

    async def on_enter(self) -> None:
        # ... existing setup ...
        log.info(
            "controller.started",
            agent_name_displayed=self._agent_name,
            agent_name_override_active=bool(tenant_settings.engine_agent_name),  # captured into self in __init__
            tenant_policy=self._tenant_policy,
        )
        # ... existing greeting + question loop ...
```

The `tenant_settings.engine_agent_name` boolean for the "override active"
log is captured into a `self._agent_name_override_active` attribute in
`__init__` so the log line stays self-contained.

### 3.3 `build_controller_prompt` signature change

```python
def build_controller_prompt(session_config: SessionConfig, *, agent_name: str) -> str:
    # ... unchanged Template loading ...
    return template.substitute(
        agent_name=agent_name,                                   # was settings.engine_agent_name
        # ... other substitutions unchanged ...
    )
```

### 3.4 Knockout-failure handling at `controller.py:417-438`

```python
def _handle_task_result(self, q: QuestionConfig, result: TaskResult) -> None:
    for signal in result.signals_lacked:
        self._disqualified_signals.add(signal)
    if result.knockout:
        reason_text = (result.knockout_reason or "").strip()
        if not reason_text:
            # KnockoutFailure.reason has min_length=1; an empty reason is an
            # upstream bug (the disqualify_knockout tool requires non-empty
            # reason). Log + skip the append rather than crash the controller.
            log.warning(
                "controller.knockout.empty_reason",
                question_id=q.id,
                signal_values=list(q.signal_values),
            )
            return
        self._knockout_failures.append(
            KnockoutFailure(                                     # was KnockoutFailureRecord
                question_id=q.id,
                reason=reason_text,                             # _scrub_pii fires on construction
                signal_values=list(q.signal_values),
                occurred_at_ms=now_ms() - self._session_start_ms,
            )
        )
        self._collector.append(
            kind="disqualify.knockout",
            payload={
                "question_id": q.id,
                "reason_chars": len(reason_text),
                "reason": reason_text,                          # stripped in metadata mode by redaction.py
            },
            wall_ms=now_ms(),
        )
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

The `KnockoutFailure(...)` constructor runs `_scrub_pii` on `reason`
unconditionally (Pydantic `mode="before"` validator). The pre-existing
`disqualify.knockout` event-log payload is unchanged; the redaction
module already strips `reason` in `metadata` mode via
`_CONTENT_FIELDS_BY_KIND`.

`asyncio.create_task` is required because `_handle_task_result` is sync
(no `async def`). `_terminate` is async and idempotent (it short-circuits
on `self._terminated=True`), so a second termination request from any
other path (idle-nudge, end-early intent, time-expired) is harmless.

The `record_only` branch is implicit — no behavior change from Phase 4.

### 3.5 Result persistence

```python
# app/modules/interview_runtime/service.py::record_session_result
res = await db.execute(
    update(SessionRow)
    .where(SessionRow.id == session_id, SessionRow.tenant_id == tenant_id, SessionRow.state == "active")
    .values(
        raw_result_json=result.model_dump(mode="json"),
        transcript=[t.model_dump(mode="json") for t in result.full_transcript],
        questions_asked=result.questions_asked,
        probes_fired=result.total_probes_fired,
        knockout_failures=[k.model_dump(mode="json") for k in result.knockout_failures],   # NEW
        agent_completed_at=now,
        result_status=derived_status,
        state="completed",
        state_changed_at=now,
    )
)
```

Idempotency unchanged — the existing `state='active'` gate keeps retries
safe. The column default `'[]'::jsonb` covers any pre-Phase-5 row that
might exist (e.g., dev session created before the migration ran).

### 3.6 Frontend session-outcome plumbing

```
agent process
  └─ _publish_session_outcome(outcome)  ← shipped Phase 2; writes 1 of 6 values to participant attrs

frontend/session
  └─ useSessionOutcome()  ← reads agent participant attribute
        └─ isSessionOutcome guard drops unknown values to null (version-skew defense)
              └─ OutcomeWatcher exhaustive switch
                    ├─ completed | knockout_closed | time_expired | candidate_ended → onCompleted (CompletionScreen)
                    ├─ candidate_unresponsive → onError('CANDIDATE_UNRESPONSIVE')
                    ├─ error → onError('ENGINE_ERROR')
                    ├─ null → fall through to DisconnectReason mapping (existing)
                    └─ default → never (compile-time guard)
```

## 4 — Data shapes

### 4.1 `tenant_settings` table (migration `0027_tenant_settings`)

```sql
CREATE TABLE tenant_settings (
    tenant_id              UUID         PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    engine_knockout_policy TEXT         NOT NULL DEFAULT 'record_only'
        CHECK (engine_knockout_policy IN ('record_only', 'close_polite')),
    engine_agent_name      TEXT         NULL,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON tenant_settings
  USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);

CREATE POLICY "service_bypass" ON tenant_settings
  USING (current_setting('app.bypass_rls', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_settings TO nexus_app;
```

Notes:

- `tenant_id` is the PK — one row per tenant. `ON DELETE CASCADE` follows
  the migration `0023_tenant_hard_delete_cascade` discipline.
- The CHECK constraint enforces the 2-value policy enum at the DB layer.
- `engine_agent_name` is nullable; null means "use env fallback."
- Apply canonical RLS pair with `NULLIF(..., '')::uuid` — required to avoid
  the empty-string GUC restoration crash documented in
  `backend/nexus/CLAUDE.md` ("RLS Pattern" trap #2).
- Grant the `nexus_app` runtime role explicit DML privileges (matches the
  pattern from migration `0010_create_nexus_app_role`).

### 4.2 `sessions.knockout_failures` column (same migration)

```sql
ALTER TABLE sessions ADD COLUMN knockout_failures JSONB NOT NULL DEFAULT '[]'::jsonb;
```

PG11+ metadata-only. Existing sessions get `'[]'`. No backfill needed.

### 4.3 `KnockoutFailure` Pydantic model

```python
# app/modules/interview_runtime/schemas.py

import re

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b")

def _scrub_pii(text: str) -> str:
    """Defense-in-depth scrub. Prompt instructs the LLM never to include PII;
    this runs unconditionally as a backstop. Replaces matches with [redacted]."""
    text = _EMAIL_RE.sub("[redacted]", text)
    text = _PHONE_RE.sub("[redacted]", text)
    return text


class KnockoutFailure(BaseModel):
    question_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    signal_values: list[str] = Field(min_length=1)
    occurred_at_ms: int = Field(ge=0)

    @field_validator("reason", mode="before")
    @classmethod
    def _scrub_reason(cls, v: str) -> str:
        if not isinstance(v, str):
            return v   # let the Field(str) constraint produce the right ValidationError
        return _scrub_pii(v)
```

Notes:

- `mode="before"` runs the scrub on every construction path — including
  `model_validate` from a DB read (defense-in-depth for any pre-scrub
  rows that may exist from in-flight dev sessions).
- The `min_length=1` on `reason` rejects accidental empty knockouts. The
  controller's append site at `controller.py:421` currently passes
  `result.knockout_reason or ""`; Phase 5's plan changes this to refuse
  to construct a `KnockoutFailure` when `knockout_reason` is empty (logs
  a warning, skips the append — a knockout without a reason is a bug
  upstream).
- `max_length=500` is generous (overview spec calls for 1-2 sentences;
  500 chars ≈ 2-3 generous sentences) and guards against runaway LLM
  output.
- The `signal_values` `min_length=1` matches the controller's call site
  which always passes `list(q.signal_values)` and `q.signal_values` itself
  carries `min_length=1` per `QuestionConfig`.

### 4.4 `SessionResult` extension

```python
class SessionResult(BaseModel):
    # ... existing fields unchanged ...
    knockout_failures: list[KnockoutFailure] = Field(default_factory=list)
```

`Field(default_factory=list)` (not `= []`) — Pydantic best practice to
avoid the shared-mutable-default trap.

### 4.5 `TenantSettings` Pydantic model

```python
# app/modules/tenant_settings/schemas.py

from typing import Literal
from uuid import UUID
from pydantic import BaseModel

KnockoutPolicy = Literal["record_only", "close_polite"]


class TenantSettings(BaseModel):
    tenant_id: UUID
    engine_knockout_policy: KnockoutPolicy = "record_only"
    engine_agent_name: str | None = None  # None => fall back to settings.engine_agent_name
```

The DEFAULT_TENANT_SETTINGS factory in service.py:

```python
def DEFAULT_TENANT_SETTINGS(tenant_id: UUID) -> TenantSettings:
    return TenantSettings(tenant_id=tenant_id)
```

### 4.6 Frontend `SessionOutcome` shared type

```typescript
// frontend/session/components/interview/lib/session-outcome.ts

export const SESSION_OUTCOMES = [
  'completed',
  'knockout_closed',
  'time_expired',
  'candidate_ended',
  'candidate_unresponsive',
  'error',
] as const

export type SessionOutcome = (typeof SESSION_OUTCOMES)[number]

export function isSessionOutcome(v: string | null | undefined): v is SessionOutcome {
  return typeof v === 'string' && (SESSION_OUTCOMES as readonly string[]).includes(v)
}
```

This shared module is the single source of truth for the 6 values on the
frontend. The list MUST stay in sync with `outcome_close.py::SessionOutcome`
on the backend; a comment in each file points at the other.

## 5 — Tests

### 5.1 Backend tests (Python)

#### Pure-unit tier (`tests/test_*.py`, top-level)

| Test file | Coverage |
|---|---|
| `tests/test_tenant_settings_schemas.py` (NEW) | `TenantSettings` defaults; `engine_knockout_policy` rejects unknown values via Literal; `engine_agent_name` accepts None + string; round-trip `model_dump` / `model_validate` |
| `tests/test_interview_runtime_knockout_failure.py` (NEW) | Required fields enforced; `pydantic.ValidationError` on missing fields (not bare `Exception`); min_length / max_length on `reason`; **PII scrub regex tests**: emails redacted (`john@acme.com`, `j.smith+work@ex.io`); phones redacted (`+1 555-123-4567`, `(555) 123-4567`, `555.123.4567`); plain text passes through; idempotent (running twice = running once); validator runs on `model_validate` (DB-read path) |
| `tests/test_session_result_knockout_failures.py` (NEW) | Round-trip: construct `SessionResult` with non-empty `knockout_failures` list → `model_dump(mode="json")` → `model_validate` → equal; default is empty list |

#### Integration tier (engine subset)

| Test file | Coverage |
|---|---|
| `tests/interview_engine/integration/test_close_polite_policy.py` (NEW) | Two scenarios using `EventCollector` + patched persistence: (1) `record_only` — knockout fires, controller continues to next question, no `_terminate` call. (2) `close_polite` — knockout fires, `_terminate(outcome="knockout_closed")` runs, `outcome_close.py::knockout_closed` instructions go to `session.generate_reply`, persistence happens once, shutdown retry loop runs. Use `EventCollector` to assert `controller.intent.knockout_closed` event lands. |
| `tests/interview_engine/integration/test_agent_name_override.py` (NEW) | Construct `InterviewController` with `tenant_settings.engine_agent_name="Acme-Bot"` → `build_controller_prompt` substitutes `Acme-Bot` (assert via instructions string match); with `engine_agent_name=None` → falls back to env value; `controller.started` log fires with correct `agent_name_displayed` + `agent_name_override_active` (use `structlog`'s `capture_logs`). |
| `tests/interview_runtime/integration/test_record_session_result_knockout_failures.py` (NEW or extend existing) | `record_session_result` writes the new column alongside `raw_result_json`; column populated from `result.knockout_failures`; idempotent retry preserves prior write; cross-tenant call (wrong tenant_id) returns `ValueError('session not found')`. |

#### Service tier

| Test file | Coverage |
|---|---|
| `tests/test_tenant_settings_service.py` (NEW) | `get_tenant_settings(db, tenant_id)` — returns `DEFAULT_TENANT_SETTINGS(tenant_id)` when no row; returns row values when row exists; tenant-isolation gate (cross-tenant read under tenant-scoped session returns the calling tenant's defaults, not the other tenant's row); both bypass-RLS and tenant-scoped session paths exercised. |

#### Migration tier

| Test file | Coverage |
|---|---|
| `tests/test_migration_0027_tenant_settings.py` (NEW) | `alembic upgrade head` creates `tenant_settings` table; both RLS policies present (queries `pg_policies` for `tenant_isolation` + `service_bypass`); `tenant_isolation` has non-NULL `WITH CHECK`; CHECK constraint on `engine_knockout_policy` rejects unknown value (raise `IntegrityError`); `sessions.knockout_failures` column exists with default `'[]'::jsonb`; downgrade drops both cleanly. |

#### Coverage gate

100% branch coverage on:

- `app/modules/tenant_settings/service.py` (new tenant-scoped service per CLAUDE.md "Test Coverage Gates")
- `_scrub_pii` + `KnockoutFailure._scrub_reason` validator (guards what reaches the DB column the analytics layer reads — fits the CLAUDE.md "candidate scoring and classification thresholds" gate)

Use the `pytest-cov` Docker workaround documented in `backend/nexus/CLAUDE.md`
("Coverage in Docker — pytest-cov + Python 3.13 segfault workaround"):

```bash
docker compose exec nexus python -m coverage run --branch \
    --source=app/modules/tenant_settings,app/modules/interview_runtime/schemas \
    -m pytest tests/test_tenant_settings_service.py tests/test_interview_runtime_knockout_failure.py
docker compose exec nexus python -m coverage report --show-missing
```

### 5.2 Frontend tests (Vitest)

| Test file | Coverage |
|---|---|
| `tests/components/interview/session-outcome.test.ts` (NEW) | `isSessionOutcome` — each of the 6 values returns true; unknown string returns false; null/undefined returns false; non-string types return false |
| `tests/components/interview/use-session-outcome.test.ts` (NEW) | Hook returns null when no agent participant; returns each of the 6 outcomes when attribute set; **drops unknown values to null** (defensive against backend/frontend version skew); ref-stickiness — once seen, value persists when agent disappears from `useRemoteParticipants` |
| `tests/components/interview/outcome-watcher.test.tsx` (NEW) | Each of the 6 outcomes routes to the right handler; engine-no-publish + `CLIENT_INITIATED` reason → `onCompleted`; engine-no-publish + `DUPLICATE_IDENTITY` → `onError('DUPLICATE_SESSION')`; engine-no-publish + unknown reason → `onError('UNEXPECTED_DISCONNECT')`. Use existing `useRemoteParticipants` mock pattern. |
| `tests/components/interview/disconnect-error.test.tsx` (NEW or extend) | Snapshot for the new `CANDIDATE_UNRESPONSIVE` code so the copy is visible in PR review |

## 6 — Compliance & sign-offs

### 6.1 Tenant isolation

- `tenant_settings` is registered in `_TENANT_SCOPED_TABLES` in `app/main.py` →
  the startup `_assert_rls_completeness` check fires on it.
- The migration test asserts both RLS policies exist with the canonical shape.
- Cross-tenant test in `test_tenant_settings_service.py` proves the service
  doesn't leak rows under a wrong tenant context.

### 6.2 PII / fairness boundary

- `_scrub_pii` runs on every `KnockoutFailure` construction (LLM output → DB read → in-memory pass).
- LLM prompt instruction (in `outcome_close.py::knockout_closed` and the
  Phase 3 task prompts) already constrains the LLM to factual self-disclosure
  with no PII — this is the primary defense.
- The DB column is RLS-protected via the `sessions` table's existing canonical
  policy pair.
- Three layers of defense; PII has to fail through all three to leak.

### 6.3 Senior-reviewer fairness sign-off

**No new sign-off entry required for Phase 5.** Verification:

- `outcome_close.py::knockout_closed` instructions ("Do NOT reference any
  specific failure or knockout reason. Two short sentences.") were signed off
  in Phase 2 and Phase 5 doesn't change the string.
- No new prompt files. No edits to `prompts/v1/interview/*.txt`.
- The new structured-log line `controller.started` is operator-facing, not
  candidate-facing.

If during plan execution a prompt body change becomes necessary (e.g., a
new variant closing line surfaces from the integration tests), append a
new entry to `docs/security/prompt-fairness-signoffs.md` per Decision #18 +
the Phases 2/3/4 precedent. The plan should mark this as a contingency.

### 6.4 Audit log

- The audit row written by `record_session_result` (`engine.session.completed`)
  already captures `correlation_id`, `questions_asked`, `result_status`. No
  new audit-log fields needed for Phase 5 — `knockout_failures` is queryable
  directly from the `sessions` row.
- A future "tenant settings edited via UI" path (post-arc) will write its
  own `tenant.settings.updated` audit row; out of scope here.

### 6.5 Threat model

No threat-model update required. `tenant_settings` is a read-mostly, server-side
configuration table on the existing nexus authority; no new external service,
no new auth surface, no change to candidate-facing trust boundary.

## 7 — Documentation updates (must land in the same commits as the code)

### 7.1 `backend/nexus/CLAUDE.md`

- Migrations bullet list: insert entry for `0027_tenant_settings` below the
  `0026_question_kind_column` line:
  > `0027_tenant_settings` — **Phase 5**: new `tenant_settings` table (PK = tenant_id, FK→clients ON DELETE CASCADE, two columns: `engine_knockout_policy` enum + `engine_agent_name` nullable text). Adds `sessions.knockout_failures JSONB NOT NULL DEFAULT '[]'`. Both ops are PG11+ metadata-only.
- Revision count line under Schema management: change "currently has 26
  revisions; head is `0026_question_kind_column`" → "currently has 27
  revisions; head is `0027_tenant_settings`".
- `migrations/` line under Module Structure: same edit.
- "Current State" list: add a new bullet
  `Phase 3D.engine-redesign-5 — done: tenant_settings table + KnockoutFailure persistence + close_polite policy wiring + 6-state session_outcome frontend wiring.` (full paragraph with file references, modeled on engine-redesign-4 entry).
- Module Structure tree: add `├── tenant_settings/` line under
  `app/modules/`.
- Phase 3C-3D modules table: add a `tenant_settings` row.

### 7.2 Overview spec

`docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md`:

- Phase status index: change Phase 5 row from
  `| 5 — Knockout policy | _pending_ | _pending_ | ⚪ not started |`
  to
  `| 5 — Knockout policy | [...phase-5-knockout-policy-design.md] | [...phase-5-knockout-policy.md] | ✅ shipped |`
  with the actual file paths filled in as each artifact lands.

### 7.3 No `docs/onboarding/` change

No new operator/SRE-facing change. The Phase 6 e2e checklist is where the
manual end-to-end test for the entire arc lives.

## 8 — Plan template guard rails (lessons from Phase 3/4)

The Phase 5 plan must pre-empt the template issues that surfaced during
Phase 3 / Phase 4 implementation:

- **Pydantic validators**: use `mode="before"` for the PII scrub so it runs
  on every construction path including `model_validate`.
- **Validation errors**: tests that expect rejection on bad input use
  `pytest.raises(pydantic.ValidationError)`, not `pytest.raises(Exception)`.
- **`pytest` import**: at module top, never inside a function body.
- **All cross-module imports through public API**:
  - `from app.modules.tenant_settings import TenantSettings, get_tenant_settings, KnockoutPolicy`
  - `from app.modules.interview_runtime import KnockoutFailure, SessionResult, SessionConfig`
  - The new `tenant_settings/__init__.py` exports via explicit `__all__`.
  - The `interview_runtime/__init__.py` re-export is added in the same
    task that adds the model.
- **Register the new table** in `_TENANT_SCOPED_TABLES` in `app/main.py`.
  Plan task explicit. Without this the startup `_assert_rls_completeness`
  check fires CRITICAL and the deploy aborts — but only after the table
  exists, so this MUST land in the same commit as the migration.
- **Register the new module** in `KNOWN_DOMAIN_MODULES` in
  `tests/test_module_boundaries.py`. Plan task explicit. Otherwise any
  cross-module deep import to it would silently pass the lint test (the
  test only flags imports of *known* modules).
- **Migration revision string** must match filename: `revision = "0027_tenant_settings"`.
- **`down_revision = "0026_question_kind_column"`** in the new migration.
- **Two ops in one migration** (`tenant_settings` table + `sessions.knockout_failures`
  column) is acceptable because both are additive and metadata-only; the
  migration's docstring documents both. If either turns out non-trivial
  during implementation, the plan can split into `0027` + `0028`.
- **Constructor parameter rename** (`tenant_policy` → `tenant_settings`)
  changes the public shape of `InterviewController.__init__`. Update
  every test fixture and integration test that constructs the controller.
  Find via `grep -rn "InterviewController(" tests/`.

## 9 — Acceptance gates for Phase 5

Per overview spec §11, Phase 5 contributes to:

- **§11.8** — `SessionResult.knockout_failures` is non-empty when a hard
  requirement is failed; empty when not. Verified by integration test, not
  manual e2e.
- The 6-state `session_outcome` flow renders the right frontend screen for
  each outcome (Vitest snapshot for `CANDIDATE_UNRESPONSIVE` + the
  exhaustive switch in `OutcomeWatcher`).
- `tenant_settings.engine_knockout_policy='close_polite'` actually closes
  the interview after a knockout — verified by `test_close_polite_policy.py`.
- `tenant_settings.engine_agent_name='Acme-Bot'` overrides the prompt
  substitution; null falls back to env value — verified by
  `test_agent_name_override.py`.
- The PII scrub strips emails/phones from any `KnockoutFailure.reason`
  regardless of LLM output — verified by `test_interview_runtime_knockout_failure.py`.

End-to-end manual test of all phases is deferred to Phase 6 per the working
agreement (overview spec §"Resuming this arc").

## 10 — Open questions

**None reserved for plan-execution time.** The brainstorm closed all six
P5-Q items in §1. The plan should be a mechanical translation of this
spec into per-task commits; if the executing subagent encounters a
genuinely-new design question, it should pause and re-open the spec
rather than guess.

## 11 — Summary of file impact

**New files (~18, two of which may be "extend existing" depending on what already exists at plan-time):**
- `migrations/versions/0027_tenant_settings.py`
- `app/modules/tenant_settings/__init__.py`
- `app/modules/tenant_settings/models.py`
- `app/modules/tenant_settings/schemas.py`
- `app/modules/tenant_settings/service.py`
- `frontend/session/components/interview/lib/session-outcome.ts`
- `tests/test_tenant_settings_schemas.py`
- `tests/test_tenant_settings_service.py`
- `tests/test_interview_runtime_knockout_failure.py`
- `tests/test_session_result_knockout_failures.py`
- `tests/test_migration_0027_tenant_settings.py`
- `tests/interview_engine/integration/test_close_polite_policy.py`
- `tests/interview_engine/integration/test_agent_name_override.py`
- `frontend/session/tests/components/interview/session-outcome.test.ts`
- `frontend/session/tests/components/interview/use-session-outcome.test.ts`
- `frontend/session/tests/components/interview/outcome-watcher.test.tsx`
- `frontend/session/tests/components/interview/disconnect-error.test.tsx` (or extend existing)
- `tests/interview_runtime/integration/test_record_session_result_knockout_failures.py` (or extend existing)

**Modified files (~13):**
- `app/modules/interview_runtime/schemas.py` (add `KnockoutFailure`, extend `SessionResult`)
- `app/modules/interview_runtime/__init__.py` (re-export)
- `app/modules/interview_runtime/service.py` (write new column)
- `app/modules/session/models.py` (`Session` class, add `knockout_failures` column)
- `app/modules/interview_engine/agent.py` (fetch tenant_settings; pass to controller)
- `app/modules/interview_engine/controller.py` (constructor rename; agent_name plumb; close_polite wire; KnockoutFailureRecord delete)
- `app/main.py` (register table)
- `tests/test_module_boundaries.py` (register module)
- `frontend/session/components/interview/app/hooks/use-session-outcome.ts` (narrow type)
- `frontend/session/components/interview/app/app.tsx` (`OutcomeWatcher` exhaustive switch)
- `frontend/session/components/interview/app/DisconnectError.tsx` (new code)
- `backend/nexus/CLAUDE.md` (migration list, revision count, status block, modules tree)
- `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` (Phase 5 status)

**Deleted (0 files / 1 in-place class):**
- `KnockoutFailureRecord` dataclass at `controller.py:62-68` (replaced by `KnockoutFailure` import)

## 12 — Glossary additions (none)

All terms referenced (knockout failure, session_outcome, close_polite, RLS,
canonical RLS pair, NULLIF discipline) are defined in the overview spec
§13 or `backend/nexus/CLAUDE.md`. No new product concepts in this phase.
