# Delete Engine v1 — Promote v2 to Sole `interview_engine` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy interview engine (`app/modules/interview_engine/`), rename `interview_engine_v2/` → `interview_engine/`, and rip out the `interview_engine_version` selection flag end-to-end so there is exactly one engine.

**Architecture:** v2 absorbs the LiveKit worker-bootstrap (`server`/`prewarm`/`entrypoint`) that currently lives in v1's `agent.py`, dropping the v1/v2 branch so dispatch is unconditional. Then v1 is deleted, v2 is renamed onto the freed path, the version flag/column/config is removed (incl. a forward migration `0047`), and dead prompts/tests/docs are swept.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async (asyncpg), Alembic, LiveKit Agents, Dramatiq, Docker Compose, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-05-27-delete-engine-v1-promote-v2-design.md`

**Branch:** `cleanup/delete-engine-v1` (already created; spec already committed there).

---

## Conventions for every task

- Run commands from `backend/nexus/`.
- Lint: `docker compose run --rm nexus ruff check .`
- Tests: `docker compose run --rm nexus pytest <paths> -m "not prompt_quality" -q` (plain pytest; do NOT use `--cov` — it segfaults on the livekit import tree, see backend CLAUDE.md).
- Engine import smoke (livekit lives only in the running container): `docker compose exec nexus python -c "<import>"` (needs `docker compose up -d nexus` first).
- Commit messages end with the `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## File-structure map (what changes)

**Created:**
- `app/modules/interview_engine_v2/__main__.py` (Task 1) → renamed to `app/modules/interview_engine/__main__.py` (Task 2)
- `migrations/versions/0047_drop_interview_engine_version.py` (Task 4)

**Renamed (Task 2):**
- `app/modules/interview_engine_v2/` → `app/modules/interview_engine/`
- `tests/interview_engine_v2/` → `tests/interview_engine/`

**Deleted:**
- `app/modules/interview_engine/` (v1, Task 2) and `tests/interview_engine/` (v1, Task 2)
- `app/modules/interview_engine_v2/selection.py` (Task 2)
- `tests/interview_engine_v2/test_entrypoint_branch.py` (Task 2)
- `tests/interview_runtime/test_build_session_config_engine_version.py`, `tests/interview_runtime/test_engine_version_column.py` (Task 3)
- `prompts/v2/engine/` (Task 6)

**Modified:** `app/modules/interview_engine_v2/{agent.py,__init__.py}` (Task 1); `app/modules/jd/models.py`, `app/modules/interview_runtime/{schemas.py,service.py}`, `app/config.py`, `app/ai/config.py`, `app/modules/reporting/router.py`, `tests/interview_runtime/test_schemas.py` (Task 3); `app/config.py`, `app/ai/config.py`, `app/ai/realtime.py`, `.env.example` (Task 5); `backend/nexus/CLAUDE.md`, root `CLAUDE.md`, `docker-compose.yml`, renamed-module docstrings, `interview_runtime` stale comments (Task 7).

---

## Task 1: Make v2 self-sufficient (port the worker bootstrap)

Add the LiveKit harness to v2 so it can run as a standalone worker, with dispatch **unconditional** (no `should_run_v2`). v1 is still the live entrypoint after this task — no breakage.

**Files:**
- Modify: `app/modules/interview_engine_v2/agent.py`
- Modify: `app/modules/interview_engine_v2/__init__.py`
- Create: `app/modules/interview_engine_v2/__main__.py`

- [ ] **Step 1: Confirm the `run` contract.** Open `app/modules/interview_engine_v2/agent.py` and confirm the `run` signature is `async def run(ctx, session_config, *, tenant_id, correlation_id)` and that `run` fetches its own `tenant_settings`/persona internally (v1 did NOT pass `tenant_settings` to it). If `run` instead requires `tenant_settings`, note it — Step 3's `_run_entrypoint` must then also fetch + pass it. Also confirm whether a module-level `log = structlog.get_logger(...)` already exists (reuse it; otherwise add `log = structlog.get_logger("interview-engine")`).

- [ ] **Step 2: Add the bootstrap imports.** Extend the existing imports at the top of `agent.py`:

```python
import json  # add near the other stdlib imports

# extend the existing `from livekit.agents import (...)` block with:
AgentServer,
JobProcess,

# process-startup model registration so `download-files` prewarms the EOU model
# (mirrors the v1 carve-out documented in backend/nexus/CLAUDE.md):
from livekit.plugins.turn_detector import multilingual as _turn_detector_multilingual  # noqa: F401
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

from app.ai.otel import bootstrap_tracer_provider
from app.modules.session import classify_engine_exception, transition_to_error

# extend the existing `from app.modules.interview_runtime import (...)` block with:
build_session_config,
```

- [ ] **Step 3: Add the bootstrap below the imports** (after the logger, before/around the existing `run`). This is v1's harness with the branch removed:

```python
server = AgentServer(host="0.0.0.0", port=8081)


def prewarm(proc: JobProcess) -> None:
    """Process-startup hook: bootstrap an OTel TracerProvider so livekit-agents'
    built-in spans ship to whatever OTLP endpoint the operator configures.
    Production-safe default: no env vars set -> spans go nowhere."""
    provider = bootstrap_tracer_provider()
    _otel_set_global_provider(provider)
    proc.userdata["otel_provider"] = provider
    log.info("engine.otel.bootstrapped", service_name=settings.otel_service_name)


server.setup_fnc = prewarm


@server.rtc_session(agent_name=settings.engine_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint. Parses dispatch metadata, binds the log context,
    then runs the engine; any pre-run crash is funneled to the failure handler."""
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
        raise  # preserves LiveKit's "job crashed" log


async def _run_entrypoint(
    ctx: JobContext,
    session_uuid: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """Fetch the SessionConfig and run the engine (unconditional — single engine)."""
    async with get_bypass_session() as db:
        session_config = await build_session_config(
            db, session_id=session_uuid, tenant_id=tenant_uuid,
        )
    log.info(
        "engine.config.fetched",
        question_count=len(session_config.stage.questions),
        stage_type=session_config.stage.stage_type,
        candidate_name=session_config.candidate.name,
        job_title=session_config.job_title,
    )
    await run(
        ctx,
        session_config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
    )


async def _handle_entrypoint_failure(
    *,
    exc: Exception,
    ctx: JobContext,
    session_id: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """Single failure handler for every pre-`run` crash path.

    Order: classify -> transition session row to state='error' (durable truth) ->
    best-effort publish session_outcome='error' to the room. DB transition is first
    so the candidate's HTTP fallback poll wins even if the room publish fails. Each
    step is independently guarded and neither re-raises (the caller re-raises the
    original exception)."""
    error_code = classify_engine_exception(exc)
    log.error(
        "engine.entrypoint.failed",
        error_code=error_code,
        error_type=type(exc).__name__,
        error=str(exc),
    )
    try:
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
    except Exception as inner:  # noqa: BLE001
        log.error(
            "engine.entrypoint.db_transition_failed",
            error=str(inner),
            error_type=type(inner).__name__,
        )
    await _best_effort_publish_outcome_attribute(ctx)


async def _best_effort_publish_outcome_attribute(ctx: JobContext) -> None:
    """Publish session_outcome='error' to the room if possible (connecting first,
    since the failure may predate ctx.connect()). Swallows every exception."""
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

If Step 1 found `run` needs `tenant_settings`, add `from app.modules.tenant_settings import get_tenant_settings`, fetch it inside the `async with get_bypass_session()` block, and pass it to `run`.

- [ ] **Step 4: Create `app/modules/interview_engine_v2/__main__.py`:**

```python
"""LiveKit Agents CLI entrypoint. Run with: ``python -m app.modules.interview_engine_v2``
(the nexus-engine compose service spawns the package this way)."""

from livekit.agents import cli

from app.modules.interview_engine_v2.agent import server

if __name__ == "__main__":
    cli.run_app(server)
```

- [ ] **Step 5: Export `server` lazily from `__init__.py`.** In `app/modules/interview_engine_v2/__init__.py`, add `server` to the `TYPE_CHECKING` block, to `__all__`, and to the `__getattr__` lazy-loader (alongside the existing `run`), so livekit stays out of the FastAPI process:

```python
if TYPE_CHECKING:  # static-only
    from app.modules.interview_engine_v2.agent import run as run  # noqa: F401
    from app.modules.interview_engine_v2.agent import server as server  # noqa: F401

# ... in __all__: add "server"

def __getattr__(name: str) -> Any:  # PEP 562 — lazy livekit-bearing exports
    if name == "run":
        from app.modules.interview_engine_v2.agent import run
        return run
    if name == "server":
        from app.modules.interview_engine_v2.agent import server
        return server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

- [ ] **Step 6: Lint + import smoke.**

```bash
docker compose run --rm nexus ruff check app/modules/interview_engine_v2/
docker compose up -d nexus
docker compose exec nexus python -c "from app.modules.interview_engine_v2.agent import server, entrypoint, run; print('ok')"
```
Expected: `ok` (no ImportError, no livekit complaints).

- [ ] **Step 7: Commit.**

```bash
git add app/modules/interview_engine_v2/
git commit -m "feat(engine): give v2 a standalone LiveKit worker bootstrap (unconditional dispatch)"
```

---

## Task 2: Delete v1, rename v2 → `interview_engine`, drop selection

One coherent commit: after it, `python -m app.modules.interview_engine` runs the (renamed) v2 worker. The compose command is unchanged because the package name is reused.

**Files:** deletes v1 module + v1 tests + `selection.py` + `test_entrypoint_branch.py`; `git mv` v2 → `interview_engine` (app + tests); global import rewrite; `__init__.py` edit.

- [ ] **Step 1: Delete v1 and selection.**

```bash
git rm -r app/modules/interview_engine
git rm -r tests/interview_engine
git rm app/modules/interview_engine_v2/selection.py
git rm tests/interview_engine_v2/test_entrypoint_branch.py
```

- [ ] **Step 2: Rename v2 onto the freed path.**

```bash
git mv app/modules/interview_engine_v2 app/modules/interview_engine
git mv tests/interview_engine_v2 tests/interview_engine
```

- [ ] **Step 3: Rewrite every import/path reference.**

```bash
grep -rl "interview_engine_v2" app tests | xargs sed -i 's/interview_engine_v2/interview_engine/g'
```
Then confirm zero remain: `grep -rn "interview_engine_v2" app tests` → no output.

- [ ] **Step 4: Remove `should_run_v2` from the package API.** In `app/modules/interview_engine/__init__.py`, delete the `from app.modules.interview_engine.selection import should_run_v2` import line and the `"should_run_v2"` entry in `__all__`. (The `__main__.py` docstring now reads `python -m app.modules.interview_engine` after Step 3's sed — verify.)

- [ ] **Step 5: Verify nothing else references the deleted symbols.**

```bash
grep -rn "should_run_v2\|from app.modules.interview_engine_v2\|interview_engine_v2" app tests
```
Expected: no output.

- [ ] **Step 6: Lint + import smoke.**

```bash
docker compose run --rm nexus ruff check app/ tests/
docker compose up -d nexus
docker compose exec nexus python -c "from app.modules.interview_engine.agent import server, entrypoint, run; print('ok')"
docker compose exec nexus python -c "import app.main; print('app imports ok')"
```
Expected: both print ok (the second confirms the FastAPI process still imports cleanly without livekit — the lazy `__getattr__` is doing its job).

- [ ] **Step 7: Commit.**

```bash
git add -A
git commit -m "refactor(engine): delete legacy v1, rename v2 -> interview_engine, drop selection"
```

---

## Task 3: Remove the `interview_engine_version` flag from code

Removes the field from the model, wire-contract, resolver, config, and the reporting filter, plus the flag-only tests. (DB column dropped in Task 4.)

**Files:** `app/modules/jd/models.py`, `app/modules/interview_runtime/schemas.py`, `app/modules/interview_runtime/service.py`, `app/config.py`, `app/ai/config.py`, `app/modules/reporting/router.py`, `tests/interview_runtime/test_schemas.py`; deletes two test files.

- [ ] **Step 1: Drop the ORM column.** In `app/modules/jd/models.py`, remove the `interview_engine_version: Mapped[str | None] = mapped_column(...)` declaration (~line 53) and any now-unused import it required.

- [ ] **Step 2: Drop the wire field.** In `app/modules/interview_runtime/schemas.py`, remove the `interview_engine_version: Literal["v1", "v2"] = Field(...)` field on `SessionConfig` (~line 268) and its description block.

- [ ] **Step 3: Drop the resolver.** In `app/modules/interview_runtime/service.py`, remove the `resolved_engine_version = (job.interview_engine_version or AIConfig().interview_engine_default_version)` block (~line 196-202) and both `interview_engine_version=resolved_engine_version` lines (the `SessionConfig(...)` kwarg ~line 263 and the `logger.info(...)` kwarg ~line 293). If `AIConfig` is now unused in this file, remove its import.

- [ ] **Step 4: Drop the global default config.** Remove `interview_engine_default_version` from `app/config.py` (~line 494-496, incl. the comment block) and the `interview_engine_default_version` property from `app/ai/config.py` (~line 171-174).

- [ ] **Step 5: Fix the reporting hub filter.** In `app/modules/reporting/router.py` (~line 181-191), change the `WHERE` so every completed session is reportable — delete the line `AND (sr.id IS NOT NULL OR j.interview_engine_version = 'v2')`. **Keep** the `LEFT JOIN session_reports sr` (the SELECT still uses `sr.status`/`sr.verdict`/`sr.overall_score`). Update the docstring (~line 171-177) to: "A session appears when it is `completed`. Tenant-scoped by an explicit `s.tenant_id` filter plus RLS."

- [ ] **Step 6: Remove flag-only tests.**

```bash
git rm tests/interview_runtime/test_build_session_config_engine_version.py
git rm tests/interview_runtime/test_engine_version_column.py
```
Then in `tests/interview_runtime/test_schemas.py`, delete the two assertions referencing `interview_engine_version` (~line 186, 192-193) and remove the `"interview_engine_version": "v2"` kwarg from the `SessionConfig(**{...})` construction.

- [ ] **Step 7: Verify zero residue (excluding the historical 0044 migration).**

```bash
grep -rn "interview_engine_version\|interview_engine_default_version" app tests
```
Expected: no output. (Migration `0044` still mentions it — that's intended history; it's under `migrations/`, not matched here.)

- [ ] **Step 8: Lint + targeted tests.**

```bash
docker compose run --rm nexus ruff check app/ tests/
docker compose run --rm nexus pytest tests/interview_runtime tests/reporting -m "not prompt_quality" -q
```
Expected: PASS.

- [ ] **Step 9: Commit.**

```bash
git add -A
git commit -m "refactor(engine): remove interview_engine_version flag from model/config/reporting"
```

---

## Task 4: Migration `0047` — drop the column

**Files:** Create `migrations/versions/0047_drop_interview_engine_version.py`

- [ ] **Step 1: Write the migration** (mirror `0044`'s names; `_CK = "ck_job_postings_interview_engine_version"`):

```python
"""drop job_postings.interview_engine_version

Revision ID: 0047_drop_interview_engine_version
Revises: 0046_reset_emptied_banks
Create Date: 2026-05-27

The v1/v2 engine-selection flag is retired: v2 is the sole interview engine, so
the per-job override column and its CHECK are removed. Downgrade re-adds them
(nullable, NULL = inherit the former global default) as the rollback path.
"""

from alembic import op
import sqlalchemy as sa

revision = "0047_drop_interview_engine_version"
down_revision = "0046_reset_emptied_banks"
branch_labels = None
depends_on = None

_CK = "ck_job_postings_interview_engine_version"


def upgrade() -> None:
    op.drop_constraint(_CK, "job_postings", type_="check")
    op.drop_column("job_postings", "interview_engine_version")


def downgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("interview_engine_version", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        _CK,
        "job_postings",
        "interview_engine_version IS NULL OR interview_engine_version IN ('v1', 'v2')",
    )
```

Before writing, open `migrations/versions/0044_interview_engine_version.py` and confirm the exact revision-id string of `0046` (`down_revision` value) and the constraint name/creation form, so `0047`'s `down_revision` and the re-created CHECK match byte-for-byte.

- [ ] **Step 2: Round-trip the migration in the container.**

```bash
docker compose up -d nexus
docker compose exec nexus alembic upgrade head
docker compose exec nexus alembic downgrade -1
docker compose exec nexus alembic upgrade head
```
Expected: each step exits 0; final `head` is `0047_drop_interview_engine_version`. Sanity-check the column is gone:
```bash
docker compose exec nexus python -c "import asyncio; from app.database import get_bypass_session; from sqlalchemy import text;
async def m():
    async with get_bypass_session() as db:
        r=await db.execute(text(\"select column_name from information_schema.columns where table_name='job_postings' and column_name='interview_engine_version'\"));
        print('rows:', r.fetchall())
asyncio.run(m())"
```
Expected: `rows: []`.

- [ ] **Step 3: Commit.**

```bash
git add migrations/versions/0047_drop_interview_engine_version.py
git commit -m "feat(db): migration 0047 drops job_postings.interview_engine_version"
```

---

## Task 5: Remove v1-only engine config + realtime factory

Grep-driven: remove a field only after confirming v1 (now deleted) was its sole consumer. Handle the `build_turn_detector` default-rebase trap.

**Files:** `app/config.py`, `app/ai/config.py`, `app/ai/realtime.py`, `.env.example`

- [ ] **Step 1: Confirm each candidate field is dead.** For the list below, run a grep and confirm the only matches are the definition site(s) in `config.py`/`ai/config.py`/`realtime.py` (no consumer under `app/modules/`):

```bash
for f in engine_judge_model engine_judge_total_budget_ms engine_judge_retry_wait_ms \
         engine_judge_prompt_version engine_speaker_model engine_speaker_max_output_tokens \
         engine_speaker_prompt_version engine_checkpoint_turns engine_checkpoint_seconds \
         engine_claims_pool_max engine_continuation_enabled engine_continuation_min_word_count \
         engine_continuation_consecutive_abort_cap engine_idle_first_nudge_seconds \
         engine_idle_second_nudge_seconds engine_idle_give_up_seconds engine_endpointing_mode \
         engine_endpointing_min_delay engine_endpointing_max_delay interview_llm_model \
         interview_reasoning_effort interview_turn_detector_unlikely_threshold; do
  echo "== $f =="; grep -rn "$f" app/ | grep -v "app/config.py\|app/ai/config.py\|app/ai/realtime.py";
done
```
Expected: each `==` header followed by no lines (all consumers were in deleted v1 files). Any surviving consumer → keep that field and note why.

Also grep these "verify individually" fields the same way and decide keep/remove from the output:
```bash
for f in engine_task_budget_overhead_seconds engine_closing_drain_timeout_seconds \
         engine_session_ended_message engine_log_audio_events engine_log_user_transcripts \
         engine_event_log_sink engine_event_log_dir engine_event_log_redaction; do
  echo "== $f =="; grep -rn "$f" app/ | grep -v "app/config.py\|app/ai/config.py";
done
```

- [ ] **Step 2: Rebase the turn-detector default, then remove `build_llm_plugin`.** In `app/ai/realtime.py`:
  - In `build_turn_detector(...)`, change the default source from `ai_config.interview_turn_detector_unlikely_threshold` to `ai_config.engine_v2_turn_detector_unlikely_threshold` (confirm v2's `agent.py` passes its threshold explicitly; if it does, you may instead make the param required — pick the smaller diff). Update the docstring lines that say "the v1 path, byte-for-byte".
  - Delete `build_llm_plugin()` entirely (v2 uses `build_mouth_llm_plugin()`). Confirm no remaining import of `build_llm_plugin` anywhere: `grep -rn "build_llm_plugin" app/` → no output.

- [ ] **Step 3: Remove the confirmed-dead fields** from `app/config.py` (the `Settings` field declarations + their comment blocks) and the matching `@property` accessors from `app/ai/config.py`. Remove the same vars from `.env.example` (search it for each removed name; delete the line + its comment).

- [ ] **Step 4: Lint + import + tests.**

```bash
docker compose run --rm nexus ruff check app/
docker compose up -d nexus
docker compose exec nexus python -c "import app.main; from app.config import settings; from app.ai.config import ai_config; print('config ok')"
docker compose run --rm nexus pytest tests/ -k "config or realtime or ai_config" -m "not prompt_quality" -q
```
Expected: `config ok` and tests PASS. (If no such tests exist, the import check is the gate.)

- [ ] **Step 5: Commit.**

```bash
git add -A
git commit -m "chore(engine): remove v1-only engine config + build_llm_plugin factory"
```

---

## Task 6: Delete the v1 engine prompts

**Files:** delete `prompts/v2/engine/`

- [ ] **Step 1: Confirm nothing loads them.**

```bash
grep -rn "v2/engine\|engine_judge_prompt_version\|engine_speaker_prompt_version\|judge.system\|speaker/_preamble" app/
```
Expected: no output (the prompt-version settings were removed in Task 5; the v1 loaders were deleted in Task 2).

- [ ] **Step 2: Delete and commit.**

```bash
git rm -r prompts/v2/engine
git commit -m "chore(prompts): delete legacy v1 engine prompts (judge + speaker)"
```
(Leave `prompts/v3/engine/`, `prompts/v3/report_scorer/`, and all `prompts/v*/question_bank_*` / JD prompts intact.)

---

## Task 7: Docs + residual prose

**Files:** `backend/nexus/CLAUDE.md`, root `CLAUDE.md`, `docker-compose.yml`, renamed-module docstrings, `app/modules/interview_runtime/*` stale comments, `app/ai/prompts.py` docstring.

- [ ] **Step 1: backend/nexus/CLAUDE.md.** Collapse the "Phase 3D.engine (v1, legacy)" + "Phase 3D.engine-v2" bullets into one "interview engine" description (single engine, no selection/cutover/default-off language). In the Module Structure tree, delete the v1 `interview_engine/` line and relabel `interview_engine_v2/` → `interview_engine/`. Add migration `0047` to the migrations list and bump the head pointer (`0046` → `0047`) everywhere it appears. Update the engine compose-command note and the coverage-workaround `--source=app/modules/interview_engine/...` example path. Remove the `interview_engine_version` selection paragraph.

- [ ] **Step 2: Root CLAUDE.md.** In the phase status table, rewrite the `3D.engine-v2` row (and the `3D` engine references) to describe a single engine — drop "parallel module", "per-job opt-in", "default-OFF", "v1 fallback" wording.

- [ ] **Step 3: docker-compose.yml.** Update the `nexus-engine` comment that says the entrypoint "dispatches to v1 or v2" to reflect the single engine. The `command: python -m app.modules.interview_engine start` is unchanged.

- [ ] **Step 4: Module + runtime prose.** Rewrite docstrings/comments in the renamed module and `interview_runtime` that still describe the old world:
  - `app/modules/interview_engine/__init__.py` and `agent.py` header — drop "v2", "legacy `interview_engine`", "parallel", "reference-only", "default-off"; describe one engine.
  - `app/modules/interview_runtime/{__init__.py,models.py,results.py,schemas.py}` — fix comments referencing "the merged interview_engine worker", "Relocated here from app/modules/interview_engine/models/...", "interview_engine.models.speaker" (that path no longer exists). Keep them as plain provenance notes without implying v1/v2 coexistence.
  - `app/ai/prompts.py` (~line 14, 32) and `app/modules/question_bank/actors.py` (~line 75) — the `interview_engine` path references are still valid post-rename; only adjust wording if it implies a generic/v1 harness.

- [ ] **Step 5: Verify no stale engine vocabulary remains in living docs/code.**

```bash
grep -rn "interview_engine_v2\|should_run_v2\|engine v1\|default-OFF\|default-off\|reference-only" \
  backend/nexus/CLAUDE.md CLAUDE.md docker-compose.yml app/modules/interview_engine app/modules/interview_runtime
```
Expected: no output (or only intentional, reconciled mentions).

- [ ] **Step 6: Commit.**

```bash
git add -A
git commit -m "docs(engine): describe a single interview engine; drop v1/v2 selection language"
```

---

## Task 8: Final verification (gate completion — no new code)

- [ ] **Step 1: Lint clean.** `docker compose run --rm nexus ruff check .` → no errors.

- [ ] **Step 2: Zero-residue greps (all must return nothing):**

```bash
grep -rn "interview_engine_v2\|should_run_v2" app/ tests/
grep -rn "interview_engine_version\|interview_engine_default_version" app/ tests/ .env.example docker-compose.yml
grep -rn "engine_judge_\|engine_speaker_\|build_llm_plugin\|interview_llm_model" app/
grep -rn "prompts/v2/engine\|v2/engine" app/ docker-compose.yml
grep -rn "from app.modules.interview_engine" app/modules/interview_runtime
```

- [ ] **Step 3: Migration round-trips** (re-run Task 4 Step 2 sequence) — clean, head `0047`.

- [ ] **Step 4: Test suite.**

```bash
docker compose run --rm nexus pytest tests/interview_engine tests/interview_runtime tests/reporting tests/test_module_boundaries.py -m "not prompt_quality" -q
```
Expected: PASS.

- [ ] **Step 5: Live engine smoke test.** Rebuild/restart the engine and run one real interview:

```bash
docker compose up -d --force-recreate nexus-engine
docker compose logs -f nexus-engine    # watch for engine.dispatch.received + engine.config.fetched
```
Start an interview from the candidate session UI. Confirm: the candidate gets the engine's opener; the triage/brain/mouth loop responds; on close the session row reaches `state='completed'` with a populated `coverage_summary` (NOT `error`/`engine_unresponsive`). This live test is the real verification of the Task 1 bootstrap port (the entrypoint can't be unit-tested due to the livekit/coverage segfault).

- [ ] **Step 6: Hand back to the user.** Summarize what changed; the branch is `cleanup/delete-engine-v1`, NOT pushed (push → Railway deploy is the user's call).

---

## Self-review notes

- **Spec coverage:** Area 1→Task 1; Area 2→Task 2; Area 3→Tasks 3+4; Area 4→Task 5; Area 5→Task 6; Area 6→Tasks 2+3 (test deletes/rename); Area 7→Task 7; verification→Task 8. All covered.
- **Deliberate omission:** the spec floated "add a replacement dispatch test" for the deleted `test_entrypoint_branch.py`. Dropped: with `should_run_v2` gone, dispatch is unconditional — there is no branch to assert, so such a test would be vacuous. The live smoke test (Task 8 Step 5) is the real coverage.
- **Type/name consistency:** `_run_entrypoint`, `_handle_entrypoint_failure`, `_best_effort_publish_outcome_attribute`, `entrypoint`, `prewarm`, `server`, `run` used consistently across Tasks 1–2. Migration id `0047_drop_interview_engine_version` and constraint `ck_job_postings_interview_engine_version` consistent across Tasks 3–4 and 8.
