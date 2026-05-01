# Phase 3 — Engine Merge into Nexus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `backend/interview_engine/` into `backend/nexus/app/modules/interview_engine/`, drop the engine-dispatch JWT + `/api/internal/*` HTTP boundary, and switch the engine to in-process calls into `app.modules.interview_runtime.service`. One Docker image runs three command-variants (nexus, nexus-worker, nexus-engine).

**Architecture:** Single PR off `feat/phase-3c2-interview-engine`, single combined commit/PR per the cutover decision (no soak-window protection needed in early dev). Code, Alembic migration 0025 (drop `engine_dispatch_tokens` + `engine_token_uses`), env-var removal, and Dockerfile collapse all land together. The engine reads `tenant_id` + `correlation_id` from LiveKit dispatch metadata and calls `build_session_config` / `record_session_result` directly via a bypass-RLS DB session.

**Tech Stack:** Python 3.13, FastAPI, livekit-agents 1.5.4+, livekit-plugins-{openai,deepgram,cartesia,silero,turn-detector,ai-coustics,noise-cancellation}, Dramatiq, structlog, OpenTelemetry (pinned per Phase 1), openai 2.x + instructor 1.15.x (per Phase 2).

**Spec reference:** `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md` § Phase 3.

**Cutover decision (locked during brainstorm 2026-05-01):** Option B — single PR. With no users in production, the rollback-safety value of a phased cutover is gone; one atomic change is simpler to review and revert.

---

## Open questions resolved during brainstorming

| Spec question | Resolution |
|---|---|
| `get_bypass_db()` outside request scope | `app.database` already exposes `get_bypass_session()` — `@asynccontextmanager`, no Request param, used by Dramatiq actors today. Use it from the engine. |
| `nexus-engine` health check mechanism | LiveKit Agents `AgentServer` ships a built-in HTTP probe at `0.0.0.0:8081/` (200 when registered with LiveKit, 503 otherwise). Lock the port via `AgentServer(host="0.0.0.0", port=8081)` and add a `healthcheck:` block in compose. |

## Implementation cleanups discovered (not in spec's file-move table)

| Item | Disposition |
|---|---|
| `backend/interview_engine/context_loader.py` (84 lines) | Move to `app/modules/interview_engine/context_loader.py` |
| `backend/interview_engine/results/` directory | Delete — local-disk fallback is dead code once the HTTP boundary is removed |
| `backend/interview_engine/agents/__init__.py` | Delete — flat module structure post-merge |
| `backend/interview_engine/AGENTS.md` | Delete; merge realtime-tuning + observability sections into `nexus/CLAUDE.md` |

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `backend/nexus/app/modules/interview_engine/__init__.py` | Package marker; re-exports `server` for the CLI entrypoint |
| `backend/nexus/app/modules/interview_engine/__main__.py` | LiveKit Agents CLI entrypoint (`python -m app.modules.interview_engine`) |
| `backend/nexus/app/modules/interview_engine/agent.py` | Moved + rewired from `backend/interview_engine/agent.py` |
| `backend/nexus/app/modules/interview_engine/interviewer.py` | Moved + rewired from `backend/interview_engine/agents/interviewer.py` |
| `backend/nexus/app/modules/interview_engine/state_machine.py` | Moved verbatim from `backend/interview_engine/state_machine.py` |
| `backend/nexus/app/modules/interview_engine/prompt_builder.py` | Moved verbatim from `backend/interview_engine/prompt_builder.py` |
| `backend/nexus/app/modules/interview_engine/context_loader.py` | Moved verbatim from `backend/interview_engine/context_loader.py` |
| `backend/nexus/livekit.toml` | Moved from `backend/interview_engine/livekit.toml` |
| `backend/nexus/tests/interview_engine/__init__.py` | New tests package marker |
| `backend/nexus/tests/interview_engine/test_engine_dispatch_in_process.py` | New — verifies in-process build_session_config + record_session_result happy path |
| `backend/nexus/migrations/versions/0025_drop_engine_dispatch_tables.py` | Alembic migration |

### Files modified

| Path | Change |
|---|---|
| `backend/nexus/pyproject.toml` | Add `livekit-agents[silero,turn-detector]>=1.5.4`, `livekit-plugins-{openai,deepgram,cartesia,ai-coustics,noise-cancellation}` |
| `backend/nexus/uv.lock` | Regenerated |
| `backend/nexus/app/config.py` | Absorb `InterviewEngineConfig` fields; remove `interview_engine_jwt_secret`, `nexus_internal_base_url`; rename `interview_agent_name` → `engine_agent_name` |
| `backend/nexus/.env.example` | Remove `INTERVIEW_ENGINE_JWT_SECRET`, `NEXUS_INTERNAL_BASE_URL`; add engine-mechanic vars |
| `backend/nexus/docker-compose.yml` | Add `nexus-engine` service with healthcheck |
| `backend/nexus/app/main.py` | Remove `interview_runtime` router registration; remove `/api/internal/` middleware exemption; remove `engine_dispatch_tokens` from `_TENANT_SCOPED_TABLES` |
| `backend/nexus/app/middleware/auth.py` | Remove `/api/internal/` path-prefix exemption |
| `backend/nexus/app/models.py` | Remove `EngineDispatchToken` and `EngineTokenUse` ORM classes |
| `backend/nexus/app/modules/session/livekit.py` | Delete `mint_engine_dispatch_jwt`; simplify `dispatch_agent` (drop `engine_jwt` param) |
| `backend/nexus/app/modules/session/service.py` | Drop `mint_engine_dispatch_jwt(...)` call from `start_session` + `engine_jwt=` from `dispatch_agent(...)` |
| `backend/nexus/app/modules/interview_runtime/service.py` | Drop `jti` param from `record_session_result` (no JWT to prefix); replace audit `jti_prefix` with `correlation_id` |
| `backend/nexus/app/modules/interview_runtime/errors.py` | Delete `EngineTokenInvalidError` |
| `backend/nexus/app/modules/auth/service.py` | Remove `from app.modules.interview_runtime.errors import EngineTokenInvalidError` (the inverted-edge import) |
| `backend/nexus/CLAUDE.md` | Module table updates; mark Phase 3C.2 → "merged in Phase 3"; absorb relevant AGENTS.md content; lift two-venv hack carve-out entirely |
| `CLAUDE.md` (root) | Phase 3C.2 status row update |
| `docs/security/threat-model.md` | Document engine-dispatch trust-boundary change (RLS-only defense) |

### Files deleted

| Path | Reason |
|---|---|
| `backend/interview_engine/` (entire directory) | Source moved; container retired |
| `backend/nexus/app/modules/interview_runtime/router.py` | HTTP boundary removed |
| `backend/nexus/tests/test_engine_tokens_rls.py` | Tests for deleted JWT layer |
| `backend/nexus/tests/test_interview_runtime_authz.py` | Tests for deleted JWT auth |
| `backend/nexus/tests/test_interview_runtime_config.py` | HTTP-shaped — replaced by in-process variant |
| `backend/nexus/tests/test_interview_runtime_results.py` | HTTP-shaped — replaced by in-process variant |

The `tests/test_interview_runtime_config.py` and `_results.py` business-logic tests get replaced by `tests/interview_engine/test_engine_dispatch_in_process.py` (Task 16) which exercises the same `build_session_config` / `record_session_result` paths but without the HTTP wrapper.

---

## Stage A — Pre-flight verification

### Task 1: Verify livekit-agents pin versions

**Files:** none (research-only)

- [ ] **Step 1.1: Check current livekit-agents + plugins releases**

Run:
```bash
for pkg in livekit-agents livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia livekit-plugins-silero livekit-plugins-turn-detector livekit-plugins-ai-coustics livekit-plugins-noise-cancellation; do
  echo -n "$pkg: "
  curl -sL "https://pypi.org/pypi/$pkg/json" | python -c "import sys, json; print(json.load(sys.stdin)['info']['version'])"
done
```

Expected: every package resolves to a version `>= 1.5.4`. Capture the actual numbers — they go into Task 3's pin set.

- [ ] **Step 1.2: Confirm the existing engine pyproject's pins are still satisfiable**

Open `backend/interview_engine/pyproject.toml` and read the current pins. They are the source of truth for what works today against openai 2.x (Phase 2 already lifted those caps). The merge inherits this set; do NOT bump majors.

If a plugin no longer publishes against the version range the engine uses today, flag it before proceeding — Phase 5 sweeps deps; Phase 3 doesn't.

- [ ] **Step 1.3: Snapshot pytest baseline on the merged base**

The Phase 3 merge is going to delete tests for the removed engine-token layer (`test_engine_tokens_rls.py`, etc.) and add new tests under `tests/interview_engine/`. Capture the pre-Phase-3 baseline now so Stage J can compare apples-to-apples.

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase3_baseline_fails.txt
wc -l /tmp/phase3_baseline_fails.txt
```

Expected: ~673 passed / 9 failed (matches the post-Phase-2 baseline). Save the failure list — Stage J's diff is `(baseline) − (Phase 3-deleted tests) + (Phase 3-added tests) − (any new failures)`.

---

## Stage B — Add engine deps to nexus + absorb config

### Task 2: Add livekit-agents + plugins to nexus

**Files:**
- Modify: `backend/nexus/pyproject.toml`
- Modify: `backend/nexus/uv.lock` (regenerated)

- [ ] **Step 2.1: Add livekit-agents + plugin pins**

Edit `backend/nexus/pyproject.toml`. Find the `# --- Real-time video infrastructure (Phase 3C.2) ---` block (around line 60–61):

```toml
    # --- Real-time video infrastructure (Phase 3C.2) ---
    "livekit-api>=1.0,<2",
```

Replace with:
```toml
    # --- Real-time video infrastructure (Phase 3C.2 + Phase 3 engine merge) ---
    "livekit-api>=1.0,<2",
    # livekit-agents and plugins. Bringing the engine into nexus on a single
    # venv (Phase 3 of the modular-monolith spec) absorbs the engine's deps
    # here. Pin versions inherited from the (about-to-be-deleted)
    # backend/interview_engine/pyproject.toml — they're already verified
    # against openai 2.x via Phase 2.
    "livekit-agents[silero,turn-detector]>=1.5.4,<2",
    "livekit-plugins-openai>=1.5.4,<2",
    "livekit-plugins-deepgram>=1.5.4,<2",
    "livekit-plugins-cartesia>=1.5.4,<2",
    "livekit-plugins-ai-coustics>=0.2.7,<1",
    "livekit-plugins-noise-cancellation~=0.2",
```

- [ ] **Step 2.2: Regenerate uv.lock**

Run:
```bash
cd backend/nexus
uv lock 2>&1 | tail -20
```

Expected: `Resolved N packages` with N greater than the prior lockfile's count by ~50–80 (the livekit-agents subgraph). No `error: no solution found`.

If resolution fails: most likely cause is a livekit-plugin transitive that hasn't published a Python 3.13 wheel. Read `uv lock --verbose 2>&1 | tail -30` to see which package. If a plugin is the blocker, pin its lower bound forward to its first 3.13-compatible release.

- [ ] **Step 2.3: Sanity-grep the resolved livekit + openai versions in the lock**

Run:
```bash
cd backend/nexus
grep -E '^name = "livekit-agents"|^name = "livekit-plugins-openai"|^name = "livekit-plugins-deepgram"|^name = "livekit-plugins-silero"|^name = "openai"|^name = "instructor"|^name = "wrapt"' -A 1 uv.lock
```

Expected:
- `livekit-agents` resolved `>=1.5.4`
- `openai` still `2.X.Y` (Phase 2 baseline)
- `instructor` still `1.15.X`
- `wrapt` still `1.X.Y` (must remain `<2`)

If `wrapt` jumped to `2.x` or `openai` jumped majors, **STOP**. The OTel auto-instrumentor breaks; investigate which transitive moved.

- [ ] **Step 2.4: Rebuild image**

Run:
```bash
cd backend/nexus
docker compose build --no-cache nexus 2>&1 | tail -20
```

Expected: clean build. The image now has livekit-agents + plugins available, even though no nexus code uses them yet.

- [ ] **Step 2.5: Smoke-import the new packages inside the container**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "
from livekit.agents import AgentServer, JobContext, cli
from livekit.plugins import silero, openai, deepgram, cartesia, ai_coustics
from livekit.plugins.turn_detector import multilingual
print('livekit imports ok')
"
```

Expected: `livekit imports ok`. Any ImportError points at a missing transitive.

---

### Task 3: Absorb `InterviewEngineConfig` fields into nexus settings

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/.env.example`

- [ ] **Step 3.1: Add the engine-mechanic fields to `Settings`**

Open `backend/nexus/app/config.py`. Locate the existing engine-integration block (search for `interview_agent_name` — currently around the LiveKit + engine settings region).

Replace the existing engine block:
```python
    interview_engine_jwt_secret: str = ""
    interview_agent_name: str = "Dakota-1785"
    nexus_internal_base_url: str = "http://nexus:8000"
```

With:
```python
    # --- Interview engine (in-process, Phase 3 merged) ---
    # The engine no longer runs as a separate Docker image with its own
    # config. These fields are read directly by app/modules/interview_engine.
    # `interview_engine_jwt_secret` and `nexus_internal_base_url` are gone —
    # the HTTP boundary was retired in Phase 3.
    engine_agent_name: str = "Dakota-1785"
    # State machine
    engine_max_probes_per_question: int = 3
    engine_time_warning_threshold: float = 0.8
    # Turn detection / endpointing (forwarded to AgentSession)
    engine_endpointing_min_delay: float = 0.3
    engine_endpointing_max_delay: float = 2.5
    # Silero VAD prewarm
    engine_silero_activation_threshold: float = 0.3
    engine_silero_min_speech_duration: float = 0.05
    engine_silero_min_silence_duration: float = 0.55
    # Observability
    engine_log_audio_events: bool = True
    engine_log_user_transcripts: bool = False
```

- [ ] **Step 3.2: Update `.env.example`**

Open `backend/nexus/.env.example`. Find the engine-integration section. Remove these lines:
```
INTERVIEW_ENGINE_JWT_SECRET=...
NEXUS_INTERNAL_BASE_URL=http://nexus:8000
INTERVIEW_AGENT_NAME=Dakota-1785
```

Replace with:
```
# --- Interview engine (Phase 3 merged: in-process, no JWT, no HTTP boundary) ---
ENGINE_AGENT_NAME=Dakota-1785
ENGINE_MAX_PROBES_PER_QUESTION=3
ENGINE_TIME_WARNING_THRESHOLD=0.8
ENGINE_ENDPOINTING_MIN_DELAY=0.3
ENGINE_ENDPOINTING_MAX_DELAY=2.5
ENGINE_SILERO_ACTIVATION_THRESHOLD=0.3
ENGINE_SILERO_MIN_SPEECH_DURATION=0.05
ENGINE_SILERO_MIN_SILENCE_DURATION=0.55
ENGINE_LOG_AUDIO_EVENTS=true
ENGINE_LOG_USER_TRANSCRIPTS=false
```

- [ ] **Step 3.3: Update local `.env` (developer's responsibility, NOT committed)**

The repo's `.env` (gitignored) needs the same renames. The implementer should manually update it to match `.env.example`. After the change:

```bash
cd backend/nexus
grep -E "INTERVIEW_ENGINE_JWT_SECRET|NEXUS_INTERNAL_BASE_URL|INTERVIEW_AGENT_NAME" .env
```

Expected: zero hits. If hits remain, edit `.env` and remove them.

- [ ] **Step 3.4: Verify settings load**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "
from app.config import settings
print('engine_agent_name:', settings.engine_agent_name)
print('engine_max_probes:', settings.engine_max_probes_per_question)
print('engine_silero_activation:', settings.engine_silero_activation_threshold)
# These should NOT exist anymore:
for old in ('interview_engine_jwt_secret', 'nexus_internal_base_url', 'interview_agent_name'):
    has = hasattr(settings, old)
    assert not has, f'leftover: {old}'
print('config: OK')
"
```

Expected: prints the three engine values + `config: OK`. If any of the deleted fields still resolves, the rename in Step 3.1 is incomplete.

---

## Stage C — Land the merged engine module

### Task 4: Create the engine package skeleton

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/__init__.py`
- Create: `backend/nexus/app/modules/interview_engine/__main__.py`

- [ ] **Step 4.1: Write `__init__.py`**

Create `backend/nexus/app/modules/interview_engine/__init__.py`:

```python
"""Interview engine — LiveKit Agent worker (in-process with nexus).

This module is the per-session voice agent dispatched by LiveKit when
nexus's /api/candidate-session/{token}/start mints a candidate room and
publishes a CreateAgentDispatchRequest.

Phase 3 of the modular-monolith uplift moved this module into nexus's
source tree and replaced the HTTP boundary at /api/internal/* with
direct in-process calls into ``app.modules.interview_runtime.service``.

Run as: ``python -m app.modules.interview_engine`` (see __main__.py).
"""

from app.modules.interview_engine.agent import server  # noqa: F401

__all__ = ["server"]
```

- [ ] **Step 4.2: Write `__main__.py`**

Create `backend/nexus/app/modules/interview_engine/__main__.py`:

```python
"""LiveKit Agents CLI entrypoint.

Run with: ``python -m app.modules.interview_engine``

Equivalent to the old ``backend/interview_engine/agent.py`` ``__main__``
block, but driven via the package's __main__ so the nexus-engine compose
service can spawn it without needing a top-level script path.
"""

from livekit.agents import cli

from app.modules.interview_engine.agent import server


if __name__ == "__main__":
    cli.run_app(server)
```

That's the entire entrypoint. The actual work happens inside `agent.py` (Task 5) and `interviewer.py` (Task 6).

---

### Task 5: Move + rewire `agent.py`

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/agent.py`
- Reference (do NOT delete yet): `backend/interview_engine/agent.py`

- [ ] **Step 5.1: Copy + rewire `agent.py`**

The new file is the existing `backend/interview_engine/agent.py` (518 lines) with these specific edits:

1. Replace the engine-config import block:

   Find:
   ```python
   from agents.interviewer import InterviewerAgent
   from config import InterviewEngineConfig
   from nexus_client import fetch_session_config
   ```

   Replace with:
   ```python
   from app.config import settings
   from app.database import get_bypass_session
   from app.modules.interview_engine.interviewer import InterviewerAgent
   from app.modules.interview_runtime.service import build_session_config
   ```

2. Replace the engine-config singleton at module scope:

   Find:
   ```python
   engine_cfg = InterviewEngineConfig()
   server = AgentServer()
   ```

   Replace with:
   ```python
   # AgentServer with explicit health-check port. LiveKit Agents auto-spawns
   # a health endpoint at host:port/ — 200 when registered with LiveKit,
   # 503 otherwise. Locking the port (vs the default random in dev mode)
   # so docker-compose's healthcheck: directive can probe a deterministic URL.
   server = AgentServer(host="0.0.0.0", port=8081)
   ```

   Then remove every reference to `engine_cfg.<field>` in the file by replacing them with `settings.engine_<field>`. The Silero prewarm block changes:

   Find:
   ```python
   def prewarm(proc: JobProcess) -> None:
       """..."""
       proc.userdata["vad"] = silero.VAD.load(
           activation_threshold=engine_cfg.silero_activation_threshold,
           min_speech_duration=engine_cfg.silero_min_speech_duration,
           min_silence_duration=engine_cfg.silero_min_silence_duration,
       )
       log.info(
           "engine.vad.prewarmed",
           activation_threshold=engine_cfg.silero_activation_threshold,
           min_speech_duration=engine_cfg.silero_min_speech_duration,
           min_silence_duration=engine_cfg.silero_min_silence_duration,
       )
   ```

   Replace with:
   ```python
   def prewarm(proc: JobProcess) -> None:
       """..."""
       proc.userdata["vad"] = silero.VAD.load(
           activation_threshold=settings.engine_silero_activation_threshold,
           min_speech_duration=settings.engine_silero_min_speech_duration,
           min_silence_duration=settings.engine_silero_min_silence_duration,
       )
       log.info(
           "engine.vad.prewarmed",
           activation_threshold=settings.engine_silero_activation_threshold,
           min_speech_duration=settings.engine_silero_min_speech_duration,
           min_silence_duration=settings.engine_silero_min_silence_duration,
       )
   ```

3. Replace the `@server.rtc_session` decorator's agent_name reference:

   Find:
   ```python
   @server.rtc_session(agent_name=engine_cfg.agent_name)
   async def entrypoint(ctx: JobContext) -> None:
   ```

   Replace with:
   ```python
   @server.rtc_session(agent_name=settings.engine_agent_name)
   async def entrypoint(ctx: JobContext) -> None:
   ```

4. **Replace the dispatch-metadata + config-fetch block** — this is the central rewire. Find:

   ```python
       metadata = json.loads(ctx.job.metadata or "{}")

       # Required keys. ...
       session_id = metadata["session_id"]
       engine_jwt = metadata["engine_jwt"]
       correlation_id = metadata.get("correlation_id", session_id)

       structlog.contextvars.bind_contextvars(
           session_id=session_id,
           correlation_id=correlation_id,
       )
       log.info("engine.dispatch.received", agent_name=engine_cfg.agent_name)

       config = await fetch_session_config(
           session_id=session_id,
           jwt=engine_jwt,
           base_url=engine_cfg.nexus_internal_base_url,
       )
   ```

   Replace with:
   ```python
       import uuid as _uuid_lib
       metadata = json.loads(ctx.job.metadata or "{}")

       # Phase 3 metadata shape: session_id + tenant_id + correlation_id.
       # engine_jwt is gone — RLS + explicit-tenant-filter at the
       # application layer is the new defense.
       session_id = metadata["session_id"]
       tenant_id = metadata["tenant_id"]
       correlation_id = metadata.get("correlation_id", session_id)

       structlog.contextvars.bind_contextvars(
           session_id=session_id,
           tenant_id=tenant_id,
           correlation_id=correlation_id,
       )
       log.info("engine.dispatch.received", agent_name=settings.engine_agent_name)

       # In-process config fetch. build_session_config runs on a bypass-RLS
       # session and filters every query by tenant_id explicitly — see
       # app/modules/interview_runtime/service.py docstring.
       async with get_bypass_session() as db:
           config = await build_session_config(
               db,
               session_id=_uuid_lib.UUID(session_id),
               tenant_id=_uuid_lib.UUID(tenant_id),
           )
   ```

5. Replace the `InterviewerAgent` constructor call. Find:
   ```python
       agent = InterviewerAgent(
           session_config=config,
           engine_config=engine_cfg,
           nexus_jwt=engine_jwt,
           nexus_base_url=engine_cfg.nexus_internal_base_url,
       )
   ```

   Replace with:
   ```python
       agent = InterviewerAgent(
           session_config=config,
           tenant_id=_uuid_lib.UUID(tenant_id),
           correlation_id=correlation_id,
       )
   ```

   The `InterviewerAgent.__init__` signature changes accordingly in Task 6.

6. Replace the endpointing knob references. Find:
   ```python
           endpointing={
               "mode": "dynamic",
               "min_delay": engine_cfg.endpointing_min_delay,
               "max_delay": engine_cfg.endpointing_max_delay,
           },
   ```

   Replace with:
   ```python
           endpointing={
               "mode": "dynamic",
               "min_delay": settings.engine_endpointing_min_delay,
               "max_delay": settings.engine_endpointing_max_delay,
           },
   ```

7. Replace the observability gates. Find:
   ```python
       if engine_cfg.log_audio_events:
           _wire_session_observability(
               session,
               log_verbose_content=engine_cfg.log_user_transcripts,
           )
   ```

   Replace with:
   ```python
       if settings.engine_log_audio_events:
           _wire_session_observability(
               session,
               log_verbose_content=settings.engine_log_user_transcripts,
           )
   ```

8. The `_log_session_setup` function takes `engine_cfg: InterviewEngineConfig` as a positional arg. Update its signature + body. Find:
   ```python
   def _log_session_setup(config, engine_cfg: InterviewEngineConfig) -> None:
   ```

   Replace with:
   ```python
   def _log_session_setup(config) -> None:
   ```

   And inside the function body, replace every `engine_cfg.<field>` with `settings.engine_<field>` (matching the field renames from Task 3.1). Specifically:
   - `engine_cfg.max_probes_per_question` → `settings.engine_max_probes_per_question`
   - `engine_cfg.time_warning_threshold` → `settings.engine_time_warning_threshold`
   - `engine_cfg.endpointing_min_delay` → `settings.engine_endpointing_min_delay`
   - `engine_cfg.endpointing_max_delay` → `settings.engine_endpointing_max_delay`
   - `engine_cfg.silero_*` → `settings.engine_silero_*`

   Also update the call site:
   ```python
       _log_session_setup(config)
   ```
   (was `_log_session_setup(config, engine_cfg)`).

9. Delete the bottom `if __name__ == "__main__":` block — `__main__.py` (Task 4.2) owns that now. Find:
   ```python
   if __name__ == "__main__":
       cli.run_app(server)
   ```

   Delete those two lines.

10. Update the file's docstring (top of file). Find the current docstring opening:
    ```python
    """ProjectX Interview Engine — LiveKit Agent entrypoint.
    ```

    Replace docstring step 3 (was: "Fetch SessionConfig from nexus's /api/internal/sessions/{id}/config.") with:
    ```
      3. Fetch SessionConfig in-process via build_session_config (no HTTP).
    ```

The remaining ~440 lines of `agent.py` (the `_wire_session_observability`, `_wire_close_handler`, `_handle_close`, the entire AgentSession lifecycle wiring) are kept verbatim except as noted in the edits above.

- [ ] **Step 5.2: Verify the file imports cleanly**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "
from app.modules.interview_engine.agent import server, prewarm, entrypoint
print('agent module imports ok')
print('server type:', type(server).__name__)
"
```

Expected: `agent module imports ok` + `server type: AgentServer`. Any ImportError indicates a missed rewire — re-check Step 5.1.

---

### Task 6: Move + rewire `interviewer.py`

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/interviewer.py`
- Reference (do NOT delete yet): `backend/interview_engine/agents/interviewer.py`

- [ ] **Step 6.1: Copy + rewire `interviewer.py`**

The new file is the existing `backend/interview_engine/agents/interviewer.py` (397 lines) with these edits:

1. Replace the import block:

   Find:
   ```python
   from app.modules.interview_runtime.schemas import (
       QuestionResult,
       SessionConfig,
       SessionResult,
       SteeringObservation,
       TranscriptEntry,
   )
   from nexus_client import (
       ResultPostFailedError,
       ResultRejectedError,
       post_session_result,
   )
   from state_machine import InterviewStateMachine, Action
   from prompt_builder import build_system_prompt
   from config import InterviewEngineConfig
   ```

   Replace with:
   ```python
   import uuid

   from app.config import settings
   from app.database import get_bypass_session
   from app.modules.interview_runtime.schemas import (
       QuestionResult,
       SessionConfig,
       SessionResult,
       SteeringObservation,
       TranscriptEntry,
   )
   from app.modules.interview_runtime.service import record_session_result
   from app.modules.interview_engine.state_machine import InterviewStateMachine, Action
   from app.modules.interview_engine.prompt_builder import build_system_prompt
   ```

2. Update the `InterviewerAgent.__init__` signature. Find:
   ```python
       def __init__(
           self,
           *,
           session_config: SessionConfig,
           engine_config: InterviewEngineConfig,
           nexus_jwt: str,
           nexus_base_url: str,
       ) -> None:
           self.state_machine = InterviewStateMachine(
               session_config=session_config,
               max_probes_per_question=engine_config.max_probes_per_question,
               time_warning_threshold=engine_config.time_warning_threshold,
           )
           self.session_config = session_config
           self.engine_config = engine_config
           self.nexus_jwt = nexus_jwt
           self.nexus_base_url = nexus_base_url

           system_prompt = build_system_prompt(session_config, engine_config)
           logger.info(
               "interview.system_prompt.built",
               chars=len(system_prompt),
               agent_name=engine_config.agent_name,
               session_id=session_config.session_id,
           )
           if engine_config.log_user_transcripts:
   ```

   Replace with:
   ```python
       def __init__(
           self,
           *,
           session_config: SessionConfig,
           tenant_id: uuid.UUID,
           correlation_id: str,
       ) -> None:
           self.state_machine = InterviewStateMachine(
               session_config=session_config,
               max_probes_per_question=settings.engine_max_probes_per_question,
               time_warning_threshold=settings.engine_time_warning_threshold,
           )
           self.session_config = session_config
           self.tenant_id = tenant_id
           self.correlation_id = correlation_id

           system_prompt = build_system_prompt(session_config)
           logger.info(
               "interview.system_prompt.built",
               chars=len(system_prompt),
               agent_name=settings.engine_agent_name,
               session_id=session_config.session_id,
           )
           if settings.engine_log_user_transcripts:
   ```

   Note: `build_system_prompt` is now called with one arg instead of two — Task 7 updates its signature accordingly.

3. **Replace `_persist_result` body** — this is the second central rewire. Find the method (search `async def _persist_result` in the file):

   ```python
       async def _persist_result(self, result: SessionResult) -> None:
           """Post the session result to nexus, falling back to local disk
           if the HTTP call fails after retries."""
           try:
               await post_session_result(
                   session_id=self.session_config.session_id,
                   jwt=self.nexus_jwt,
                   result=result,
                   base_url=self.nexus_base_url,
               )
               logger.info("interview.result.posted", session_id=self.session_config.session_id)
           except (ResultRejectedError, ResultPostFailedError) as exc:
               logger.error(
                   "interview.result.post_failed",
                   error=str(exc),
                   error_type=type(exc).__name__,
               )
               # Fallback: write to local disk for forensics.
               # ... (the local-disk fallback block)
   ```

   Replace with:
   ```python
       async def _persist_result(self, result: SessionResult) -> None:
           """Persist the session result via in-process call to nexus's
           interview_runtime.service. No HTTP boundary, no JWT — Phase 3
           merged engine and nexus into a single venv.

           Tenant scope is enforced application-side via the explicit
           tenant_id filter in record_session_result; the bypass-RLS
           session is necessary because the engine has no Supabase user
           context to bind ``app.current_tenant`` against."""
           async with get_bypass_session() as db:
               # `jti` was previously the engine JWT's identifier used in
               # the audit-log payload. Phase 3 retired the JWT — use a
               # UUID derived from the correlation_id so the audit row
               # still has a stable per-session breadcrumb.
               try:
                   audit_jti = uuid.UUID(self.correlation_id)
               except ValueError:
                   audit_jti = uuid.uuid4()
               await record_session_result(
                   db,
                   session_id=uuid.UUID(self.session_config.session_id),
                   tenant_id=self.tenant_id,
                   result=result,
                   jti=audit_jti,
               )
               await db.commit()
           logger.info(
               "interview.result.persisted",
               session_id=self.session_config.session_id,
           )
   ```

   The local-disk-fallback branch is gone — the in-process path can't fail in the way the HTTP path could (network), and a real DB outage is a SEV-1 the engine can't paper over.

4. Find any lingering reference to `self.engine_config.<field>` and replace with `settings.engine_<field>`. Specifically anywhere in the file the agent reads `max_probes_per_question`, `time_warning_threshold`, or `agent_name` from `self.engine_config`.

5. Find any lingering reference to `self.nexus_jwt` or `self.nexus_base_url` — these attributes don't exist anymore. Delete those lines (they were only used by `_persist_result`'s old HTTP path).

- [ ] **Step 6.2: Verify the file imports cleanly**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "
from app.modules.interview_engine.interviewer import InterviewerAgent
print('interviewer module imports ok')
"
```

Expected: `interviewer module imports ok`. Any ImportError indicates a missed rewire.

---

### Task 7: Move `state_machine.py`, `prompt_builder.py`, `context_loader.py`

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/state_machine.py` (copy from `backend/interview_engine/state_machine.py`)
- Create: `backend/nexus/app/modules/interview_engine/prompt_builder.py` (copy from `backend/interview_engine/prompt_builder.py`)
- Create: `backend/nexus/app/modules/interview_engine/context_loader.py` (copy from `backend/interview_engine/context_loader.py`)

- [ ] **Step 7.1: Copy `state_machine.py` verbatim**

```bash
cp backend/interview_engine/state_machine.py backend/nexus/app/modules/interview_engine/state_machine.py
```

The `state_machine.py` has no nexus-imports (it only consumes `SessionConfig` shape via duck-typing). No edits needed.

- [ ] **Step 7.2: Copy `prompt_builder.py` and adjust signature**

```bash
cp backend/interview_engine/prompt_builder.py backend/nexus/app/modules/interview_engine/prompt_builder.py
```

Edit `backend/nexus/app/modules/interview_engine/prompt_builder.py`. Find the `build_system_prompt` signature:

```python
def build_system_prompt(session_config: SessionConfig, engine_config: InterviewEngineConfig) -> str:
```

Replace with:
```python
def build_system_prompt(session_config: SessionConfig) -> str:
```

Inside the function body, replace any `engine_config.<field>` references with `settings.engine_<field>`. Add the import at the top:
```python
from app.config import settings
```

If the function only used `engine_config.agent_name` (likely), the body change is `engine_config.agent_name` → `settings.engine_agent_name`. Verify by grepping for `engine_config.` after the rewrite — should be zero hits.

```bash
grep -n "engine_config" backend/nexus/app/modules/interview_engine/prompt_builder.py
```

Expected: zero hits.

- [ ] **Step 7.3: Copy `context_loader.py`**

```bash
cp backend/interview_engine/context_loader.py backend/nexus/app/modules/interview_engine/context_loader.py
```

Inspect imports — if it imports `from config import ...` or `from nexus_client import ...`, rewire similarly. Run:
```bash
grep -n "^from \(config\|nexus_client\|state_machine\|prompt_builder\|agents\.\)" backend/nexus/app/modules/interview_engine/context_loader.py
```

Expected: zero hits. If hits exist, rewire each import to its new path:
- `from config import ...` → `from app.config import settings`
- `from state_machine import ...` → `from app.modules.interview_engine.state_machine import ...`
- `from agents.interviewer import ...` → `from app.modules.interview_engine.interviewer import ...`

- [ ] **Step 7.4: Verify all five module files import cleanly**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "
from app.modules.interview_engine import (
    agent, interviewer, state_machine, prompt_builder, context_loader,
)
from app.modules.interview_engine import server
print('all interview_engine submodules import ok')
print('server:', server)
"
```

Expected: clean output. Any ImportError indicates a missed rewire — re-check the relevant Task 5/6/7 step.

---

### Task 8: Move `livekit.toml` + tests directory

**Files:**
- Create: `backend/nexus/livekit.toml` (copy of `backend/interview_engine/livekit.toml`)
- Create: `backend/nexus/tests/interview_engine/__init__.py`
- Create: `backend/nexus/tests/interview_engine/<every test from backend/interview_engine/tests/>`

- [ ] **Step 8.1: Move `livekit.toml`**

```bash
cp backend/interview_engine/livekit.toml backend/nexus/livekit.toml
```

The `livekit.toml` is the LiveKit CLI's project config (used by `lk app deploy` etc.). Inspect it briefly for hard-coded paths — most likely it just declares the Python entrypoint. If it references `agent.py` or `interview_engine` as a module name, leave it for now; the `lk` CLI is run from the new path post-cleanup. (If it references `backend/interview_engine`, that's a path in the file the implementer should update to point at `app/modules/interview_engine` — read the file to decide.)

- [ ] **Step 8.2: Move tests directory**

```bash
mkdir -p backend/nexus/tests/interview_engine
cp -r backend/interview_engine/tests/* backend/nexus/tests/interview_engine/
touch backend/nexus/tests/interview_engine/__init__.py
```

- [ ] **Step 8.3: Rewire test imports**

The moved tests probably have `from agents.interviewer import ...` / `from state_machine import ...` style imports. Run a sweep:

```bash
cd backend/nexus
grep -rn "^from \(config\|nexus_client\|state_machine\|prompt_builder\|context_loader\|agents\.\)" tests/interview_engine/
```

For each match, rewrite the import as in Task 7.3:
- `from agents.interviewer import X` → `from app.modules.interview_engine.interviewer import X`
- `from state_machine import X` → `from app.modules.interview_engine.state_machine import X`
- `from config import InterviewEngineConfig` → tests should be rewritten to use `settings` directly OR construct a minimal mock; `InterviewEngineConfig` no longer exists. Inspect each test that uses it; usually it can be deleted (the field references move to `settings.engine_*` or get monkey-patched).
- `from nexus_client import ...` → these tests test the HTTP path that's being deleted. Mark them for deletion in Task 11 instead of rewiring.

After the sweep, re-run the grep — should be zero hits.

- [ ] **Step 8.4: Verify the moved tests collect**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/interview_engine/ --collect-only 2>&1 | tail -10
```

Expected: a list of collected tests, no `ERROR`s. Test failures during collection mean an import is still wrong. Test failures at run time are fine for now — we'll run them in Stage J.

---

## Stage D — Cutover: simplify dispatch + delete the JWT layer

### Task 9: Drop `engine_jwt` from dispatch

**Files:**
- Modify: `backend/nexus/app/modules/session/livekit.py`
- Modify: `backend/nexus/app/modules/session/service.py`

- [ ] **Step 9.1: Simplify `dispatch_agent` signature in `livekit.py`**

Open `backend/nexus/app/modules/session/livekit.py`. Find `dispatch_agent`:

```python
async def dispatch_agent(
    *,
    room_name: str,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    engine_jwt: str,
    correlation_id: str,
) -> None:
    """Explicitly dispatch the named agent into a room. ..."""
    metadata = json.dumps({
        "session_id": str(session_id),
        "tenant_id": str(tenant_id),
        "engine_jwt": engine_jwt,
        "correlation_id": correlation_id,
    })
    lk = _lk_client()
    try:
        await lk.agent_dispatch.create_dispatch(
            livekit_api.CreateAgentDispatchRequest(
                agent_name=settings.interview_agent_name,
                room=room_name,
                metadata=metadata,
            )
        )
    finally:
        await lk.aclose()
```

Replace with:
```python
async def dispatch_agent(
    *,
    room_name: str,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """Explicitly dispatch the named agent into a room.

    LiveKit auto-creates the room if it doesn't exist. The metadata JSON
    is what reaches the agent worker via JobContext.job.metadata — the
    engine reads session_id + tenant_id + correlation_id and uses the
    tenant_id to scope its DB queries via in-process ``build_session_config``
    / ``record_session_result`` (Phase 3 retired the engine-dispatch JWT;
    RLS + explicit-tenant filters are the new defense layer).

    Exceptions from the SDK propagate to start_session, which translates
    them to AgentDispatchFailedError before the candidate token is
    consumed.
    """
    metadata = json.dumps({
        "session_id": str(session_id),
        "tenant_id": str(tenant_id),
        "correlation_id": correlation_id,
    })
    lk = _lk_client()
    try:
        await lk.agent_dispatch.create_dispatch(
            livekit_api.CreateAgentDispatchRequest(
                agent_name=settings.engine_agent_name,
                room=room_name,
                metadata=metadata,
            )
        )
    finally:
        await lk.aclose()
```

Note both edits: the `engine_jwt` parameter is gone, the metadata payload no longer carries it, and the agent_name comes from `settings.engine_agent_name` (renamed in Task 3.1).

- [ ] **Step 9.2: Delete `mint_engine_dispatch_jwt` from `livekit.py`**

In the same file, find the entire `mint_engine_dispatch_jwt` function (~40 lines, lines 76–115) and delete it. Also delete the imports it uses:

```python
import jwt as pyjwt
from app.models import EngineDispatchToken
```

(only if no other function in the file uses them — verify with `grep "pyjwt\|EngineDispatchToken" backend/nexus/app/modules/session/livekit.py` after deletion; expected zero hits).

- [ ] **Step 9.3: Update `start_session` in `service.py`**

Open `backend/nexus/app/modules/session/service.py`. Find the `mint_engine_dispatch_jwt` call inside `start_session` (around line 456):

```python
    engine_jwt = await mint_engine_dispatch_jwt(
        db,
        session_id=sess.id,
        tenant_id=sess.tenant_id,
        ttl_minutes=min(duration_minutes + 5, 90),
    )
    try:
        await dispatch_agent(
            room_name=room_name,
            session_id=sess.id,
            tenant_id=sess.tenant_id,
            engine_jwt=engine_jwt,
            correlation_id=correlation_id,
        )
    except Exception as exc:
```

Replace with:
```python
    try:
        await dispatch_agent(
            room_name=room_name,
            session_id=sess.id,
            tenant_id=sess.tenant_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
```

(the `engine_jwt = await mint_engine_dispatch_jwt(...)` block is removed entirely; the `dispatch_agent` call drops the `engine_jwt=` kwarg).

- [ ] **Step 9.4: Update the docstring of `start_session`**

In the same file, find the order-of-operations docstring:

```python
    Order-of-operations (spec Q7a Option 2):
        1. Load + state-gate + OTP-gate the session.
        2. Mint LiveKit candidate JWT (sync, no DB).
        3. Mint engine dispatch JWT (DB INSERT — same transaction).
        4. Dispatch agent to LiveKit. ...
        5. Atomically consume candidate_session_tokens.used_at. ...
        6. Transition session → active, ...
```

Replace with:
```python
    Order-of-operations (Phase 3 simplified — no engine JWT):
        1. Load + state-gate + OTP-gate the session.
        2. Mint LiveKit candidate JWT (sync, no DB).
        3. Dispatch agent to LiveKit. On failure → AgentDispatchFailedError;
           no DB rollback needed since no engine token row was ever written.
           Candidate token unconsumed; candidate can retry.
        4. Atomically consume candidate_session_tokens.used_at. On rowcount=0
           (concurrent /start consumed it first), best-effort cancel_room
           and raise TokenAlreadyUsedError.
        5. Transition session → active, stamp livekit_room_name +
           started_at, audit 'session.token_used'.
```

Renumber (5→4, 6→5) and remove the lines about `engine_dispatch_tokens` rollback.

- [ ] **Step 9.5: Update the import in `service.py`**

In the same file, find the imports near the top:

```python
from app.modules.session.livekit import (
    cancel_room,
    dispatch_agent,
    mint_candidate_lk_token,
    mint_engine_dispatch_jwt,
)
```

Replace with:
```python
from app.modules.session.livekit import (
    cancel_room,
    dispatch_agent,
    mint_candidate_lk_token,
)
```

(remove `mint_engine_dispatch_jwt` from the import).

- [ ] **Step 9.6: Verify no leftover references**

Run:
```bash
cd backend/nexus
grep -rn "mint_engine_dispatch_jwt\|engine_jwt" app/ tests/ --include="*.py" | grep -v "engine_dispatch_tokens"
```

Expected: zero hits in `app/`. Hits in `tests/` are tests of the deleted code — Task 12 deletes them.

---

### Task 10: Delete `interview_runtime/router.py`

**Files:**
- Delete: `backend/nexus/app/modules/interview_runtime/router.py`
- Modify: `backend/nexus/app/main.py` (remove router registration)
- Modify: `backend/nexus/app/middleware/auth.py` (remove `/api/internal/` exemption)

- [ ] **Step 10.1: Delete the router file**

```bash
rm backend/nexus/app/modules/interview_runtime/router.py
```

- [ ] **Step 10.2: Remove the router registration from `app/main.py`**

Open `backend/nexus/app/main.py`. Find the `interview_runtime` router import + registration. The import line will look like:
```python
from app.modules.interview_runtime.router import router as interview_runtime_router
```

And the registration line:
```python
app.include_router(interview_runtime_router)
```

Delete both lines. Verify by grep:
```bash
grep -n "interview_runtime" backend/nexus/app/main.py
```

Expected: zero hits.

- [ ] **Step 10.3: Remove the `/api/internal/` middleware exemption**

Open `backend/nexus/app/middleware/auth.py`. Search for the exemption — it's typically a path-prefix list passed to the auth middleware that lets `/api/internal/*` skip JWT verification. The line(s) might look like:

```python
EXEMPT_PATHS = ("/api/internal/", "/health", "/api/auth/login")
```

Remove `"/api/internal/",` from the tuple. After the edit, run:
```bash
grep -n "/api/internal" backend/nexus/app/middleware/auth.py
```

Expected: zero hits.

- [ ] **Step 10.4: Verify the app still boots**

Run:
```bash
cd backend/nexus
docker compose up nexus -d
sleep 6
docker compose logs --tail=30 nexus | grep -E "startup|ERROR|Traceback" | head -10
docker compose stop nexus
```

Expected: `nexus.startup`, `otel.openai_instrumented`, `Application startup complete`, no `Traceback`. If startup fails, the most likely cause is a leftover import of the deleted router or a router-registration line.

---

### Task 11: Delete `verify_engine_token` and `EngineTokenInvalidError`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/service.py`
- Modify: `backend/nexus/app/modules/interview_runtime/errors.py`
- Modify: `backend/nexus/app/modules/auth/service.py` (the inverted-edge import)

- [ ] **Step 11.1: Delete `verify_engine_token` from `service.py`**

Open `backend/nexus/app/modules/interview_runtime/service.py`. Find `verify_engine_token` and delete the entire function. Verify:

```bash
grep -n "verify_engine_token\|engine_dispatch_tokens\|engine_token_uses" backend/nexus/app/modules/interview_runtime/service.py
```

Expected: zero hits in this file.

- [ ] **Step 11.2: Update `record_session_result` to drop the `jti` parameter**

Find:
```python
async def record_session_result(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    result: SessionResult,
    jti: uuid.UUID,
) -> None:
```

Replace with:
```python
async def record_session_result(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    result: SessionResult,
    correlation_id: str,
) -> None:
```

Inside the function body, find the audit log payload:
```python
        payload={
            "jti_prefix": str(jti)[:8],
            "questions_asked": result.questions_asked,
            "result_status": derived_status,
        },
```

Replace with:
```python
        payload={
            "correlation_id": correlation_id,
            "questions_asked": result.questions_asked,
            "result_status": derived_status,
        },
```

- [ ] **Step 11.3: Update `interviewer.py` to pass `correlation_id` instead of `jti`**

Open `backend/nexus/app/modules/interview_engine/interviewer.py`. Find the `_persist_result` body's `record_session_result` call (added in Task 6.1):

```python
                audit_jti = uuid.UUID(self.correlation_id)
            except ValueError:
                audit_jti = uuid.uuid4()
            await record_session_result(
                db,
                session_id=uuid.UUID(self.session_config.session_id),
                tenant_id=self.tenant_id,
                result=result,
                jti=audit_jti,
            )
```

Replace with:
```python
            await record_session_result(
                db,
                session_id=uuid.UUID(self.session_config.session_id),
                tenant_id=self.tenant_id,
                result=result,
                correlation_id=self.correlation_id,
            )
```

(simplifies — no UUID cast needed; correlation_id is just a string).

- [ ] **Step 11.4: Delete `EngineTokenInvalidError` from `errors.py`**

Open `backend/nexus/app/modules/interview_runtime/errors.py`. Find and delete the `EngineTokenInvalidError` class (typically 3–5 lines). Verify:

```bash
grep -n "EngineTokenInvalid" backend/nexus/app/modules/interview_runtime/errors.py
```

Expected: zero hits.

- [ ] **Step 11.5: Remove the inverted-edge import from `auth/service.py`**

Open `backend/nexus/app/modules/auth/service.py`. Search for:
```python
from app.modules.interview_runtime.errors import EngineTokenInvalidError
```

Delete the import. Then look for any code that catches or raises `EngineTokenInvalidError` in this file — there shouldn't be any (the import was the inverted edge the spec called out for deletion). If any references remain, decide if they were:
- Catching the exception type defensively → safe to delete the catch block
- Raising the exception → that path is dead code (engine JWT is gone); delete the surrounding logic

Run:
```bash
grep -rn "EngineTokenInvalidError" backend/nexus/app/ backend/nexus/tests/ --include="*.py"
```

Expected: zero hits anywhere in the project (deleted in Step 11.4 + 11.5; references in tests get cleaned up in Task 12).

---

### Task 12: Delete obsolete tests

**Files:**
- Delete: `backend/nexus/tests/test_engine_tokens_rls.py`
- Delete: `backend/nexus/tests/test_interview_runtime_authz.py`
- Delete: `backend/nexus/tests/test_interview_runtime_config.py`
- Delete: `backend/nexus/tests/test_interview_runtime_results.py`

- [ ] **Step 12.1: Delete the four obsolete test files**

```bash
cd backend/nexus
rm tests/test_engine_tokens_rls.py \
   tests/test_interview_runtime_authz.py \
   tests/test_interview_runtime_config.py \
   tests/test_interview_runtime_results.py
```

These tests covered:
- `test_engine_tokens_rls.py` — RLS on `engine_dispatch_tokens` (table dropped in Task 13)
- `test_interview_runtime_authz.py` — JWT-gated access to `/api/internal/*` (router deleted)
- `test_interview_runtime_config.py` — HTTP-shaped tests of `/config` endpoint
- `test_interview_runtime_results.py` — HTTP-shaped tests of `/results` endpoint

The business logic of `build_session_config` and `record_session_result` is not lost — Task 16 adds `tests/interview_engine/test_engine_dispatch_in_process.py` that exercises the same code paths in-process.

- [ ] **Step 12.2: Sweep for any remaining test imports from the deleted modules**

```bash
cd backend/nexus
grep -rn "verify_engine_token\|EngineTokenInvalidError\|EngineDispatchToken\|EngineTokenUse\|mint_engine_dispatch_jwt\|nexus_internal_base_url\|interview_engine_jwt_secret" tests/ --include="*.py"
```

Expected: zero hits. If any test still references these symbols, it's stale — delete or rewire it. If the test exercised something other than the deleted JWT layer, rewire its imports; otherwise delete.

---

## Stage E — Database + model cleanup

### Task 13: Alembic migration `0025_drop_engine_dispatch_tables`

**Files:**
- Create: `backend/nexus/migrations/versions/0025_drop_engine_dispatch_tables.py`

- [ ] **Step 13.1: Read migration 0024 for the `down_revision` chain + table shapes**

```bash
ls backend/nexus/migrations/versions/ | tail -5
head -30 backend/nexus/migrations/versions/0024_engine_integration.py
```

Note the `revision` of 0024 — it's the `down_revision` for 0025. Note the `op.create_table("engine_dispatch_tokens", ...)` and `op.create_table("engine_token_uses", ...)` blocks — the `downgrade` of 0025 reverses these (recreating both tables exactly).

- [ ] **Step 13.2: Write the migration**

Create `backend/nexus/migrations/versions/0025_drop_engine_dispatch_tables.py`:

```python
"""drop engine_dispatch_tokens + engine_token_uses

Phase 3 of the modular-monolith uplift retired the engine-dispatch JWT
layer. The interview engine now runs in-process inside nexus and reads
SessionConfig / posts SessionResult via direct calls into
``app.modules.interview_runtime.service``. With no JWT, the
``engine_dispatch_tokens`` (per-issue token rows) and ``engine_token_uses``
(single-use enforcement on jti+endpoint pair) tables are dead weight.

The downgrade recreates the table structure but cannot recover any data.
In-flight dispatches at rollback time would be unrecoverable — but with
zero users in production at the time of this migration, the rollback
hazard is theoretical.

Revision ID: 0025_drop_engine_dispatch_tables
Revises: 0024_engine_integration
Create Date: 2026-05-01
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "0025_drop_engine_dispatch_tables"
down_revision = "0024_engine_integration"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the dependent table first (engine_token_uses references engine_dispatch_tokens via jti).
    op.drop_table("engine_token_uses")
    op.drop_table("engine_dispatch_tokens")


def downgrade():
    # Recreate engine_dispatch_tokens with the exact 0024 schema.
    op.create_table(
        "engine_dispatch_tokens",
        sa.Column("jti", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    # RLS pair (tenant_isolation + service_bypass) using NULLIF guard.
    op.execute("ALTER TABLE engine_dispatch_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON engine_dispatch_tokens
            USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY service_bypass ON engine_dispatch_tokens
            USING (current_setting('app.bypass_rls', true) = 'true')
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON engine_dispatch_tokens TO nexus_app")

    # Recreate engine_token_uses (service-bypass-only, no tenant_id, composite PK).
    op.create_table(
        "engine_token_uses",
        sa.Column("jti", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("endpoint", sa.String(64), primary_key=True),
        sa.Column(
            "used_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["jti"], ["engine_dispatch_tokens.jti"], ondelete="CASCADE"),
    )
    op.execute("ALTER TABLE engine_token_uses ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY service_bypass ON engine_token_uses
            USING (current_setting('app.bypass_rls', true) = 'true')
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON engine_token_uses TO nexus_app")
```

If the actual 0024 migration uses different column names or constraint names, mirror them exactly in the `downgrade` body — the downgrade should round-trip a forward migration without divergence. Open `0024_engine_integration.py` and copy-paste the `op.create_table` blocks into the downgrade above if they differ.

- [ ] **Step 13.3: Apply the migration locally**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus alembic upgrade head 2>&1 | tail -10
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade 0024_engine_integration -> 0025_drop_engine_dispatch_tables, drop engine_dispatch_tokens + engine_token_uses` and a final `OK`.

- [ ] **Step 13.4: Verify the tables are gone**

Run:
```bash
docker compose run --rm nexus python -c "
import asyncio
from sqlalchemy import text
from app.database import async_session_factory

async def check():
    async with async_session_factory() as s:
        async with s.begin():
            r = await s.execute(text(\"SELECT tablename FROM pg_tables WHERE tablename IN ('engine_dispatch_tokens', 'engine_token_uses')\"))
            rows = r.fetchall()
            print('remaining engine_* tables:', rows)
            assert len(rows) == 0, 'tables still present'

asyncio.run(check())
"
```

Expected: `remaining engine_* tables: []`. If non-empty, the migration didn't drop them — re-check Step 13.2.

- [ ] **Step 13.5: Smoke a downgrade-then-upgrade roundtrip**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus alembic downgrade -1 2>&1 | tail -5
docker compose run --rm nexus alembic upgrade head 2>&1 | tail -5
```

Expected: both succeed. Confirms the `downgrade` body works (we won't actually use it, but it's required by the rollback contract).

---

### Task 14: Remove `EngineDispatchToken` + `EngineTokenUse` from `app/models.py`

**Files:**
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 14.1: Locate and delete the two ORM classes**

Open `backend/nexus/app/models.py`. Find `class EngineDispatchToken(Base):` and `class EngineTokenUse(Base):` and delete both class definitions entirely (and any leading import they require). Verify:

```bash
grep -n "EngineDispatchToken\|EngineTokenUse" backend/nexus/app/models.py
```

Expected: zero hits.

- [ ] **Step 14.2: Verify no other code imports these classes**

Run:
```bash
cd backend/nexus
grep -rn "EngineDispatchToken\|EngineTokenUse" app/ tests/ --include="*.py"
```

Expected: zero hits. (Task 11.1 already deleted the only imports in `service.py`; Task 9.2 deleted them from `livekit.py`; Task 12 removed the test references.)

- [ ] **Step 14.3: Verify the app still boots after model deletion**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "
from app.models import Base
# Just importing should not fail with mapper-config errors
print('models import ok; tables in metadata:', len(Base.metadata.tables))
"
```

Expected: prints a count (typically ~25), no Traceback. A SQLAlchemy mapper-config failure here means a remaining model still has a relationship() referencing the deleted classes — read the traceback and find the offending relationship to clean up.

---

### Task 15: Update `_TENANT_SCOPED_TABLES` in `app/main.py`

**Files:**
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 15.1: Remove `engine_dispatch_tokens` from the enumerated table list**

Open `backend/nexus/app/main.py`. Search for `_TENANT_SCOPED_TABLES`. The list is a tuple/list of table-name strings used by `_assert_rls_completeness` at startup. Remove `"engine_dispatch_tokens"` from the list.

`engine_token_uses` is service-bypass-only (not tenant-scoped) — verify it isn't in the list at all. If a different bypass-only table list also exists, remove `engine_token_uses` from there.

Verify:
```bash
grep -n "engine_dispatch_tokens\|engine_token_uses" backend/nexus/app/main.py
```

Expected: zero hits.

- [ ] **Step 15.2: Verify `_assert_rls_completeness` still passes at startup**

Run:
```bash
cd backend/nexus
docker compose up nexus -d
sleep 6
docker compose logs --tail=20 nexus | grep -E "rls.completeness_check|CRITICAL|Traceback" | head -10
docker compose stop nexus
```

Expected: `rls.completeness_check_ok bypass_only_tables_verified=N tenant_scoped_tables_verified=M` where M is one less than before (because `engine_dispatch_tokens` is gone). No `CRITICAL`. If a CRITICAL line shows up about a missing table, the table still has data in `pg_policies` from a stale migration state — re-run `alembic upgrade head` first.

---

## Stage F — Compose service + smoke

### Task 16: Add `nexus-engine` compose service + in-process smoke test

**Files:**
- Modify: `backend/nexus/docker-compose.yml`
- Create: `backend/nexus/tests/interview_engine/test_engine_dispatch_in_process.py`

- [ ] **Step 16.1: Add the `nexus-engine` service to compose**

Open `backend/nexus/docker-compose.yml`. Find the existing `nexus` and `nexus-worker` service definitions (they share the same `build:`). Add a new `nexus-engine` service immediately after `nexus-worker`:

```yaml
  nexus-engine:
    build:
      context: .
      dockerfile: Dockerfile
    command: python -m app.modules.interview_engine start
    env_file: .env
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: ${REDIS_URL}
      LIVEKIT_URL: ${LIVEKIT_URL}
      LIVEKIT_API_KEY: ${LIVEKIT_API_KEY}
      LIVEKIT_API_SECRET: ${LIVEKIT_API_SECRET}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      DEEPGRAM_API_KEY: ${DEEPGRAM_API_KEY}
      CARTESIA_API_KEY: ${CARTESIA_API_KEY}
    volumes:
      - hf-cache:/app/.cache/huggingface
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      # AgentServer's built-in HTTP probe at port 8081/. Returns 200 when
      # the worker is registered with LiveKit, 503 otherwise. See
      # https://docs.livekit.io/agents/server/options/#health-check
      test: ["CMD", "curl", "-fsS", "http://localhost:8081/"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

Note `command:` uses `start` (production mode) per the LiveKit Agents CLI — `dev` mode uses a random health-check port and isn't suitable for compose-level probes.

If `nexus-worker` already declares a `hf-cache` volume, the volume already exists at compose top-level. If not, also add at the bottom of the file:

```yaml
volumes:
  hf-cache:
```

- [ ] **Step 16.2: Boot the new service and watch the health probe flip**

Run:
```bash
cd backend/nexus
docker compose up nexus-engine -d 2>&1 | tail -5
# 503 expected immediately (not yet registered with LiveKit)
sleep 2
docker compose exec nexus-engine sh -c "curl -fsS http://localhost:8081/ -w '\nHTTP %{http_code}\n' || true" 2>&1 | tail -3
sleep 25
# 200 expected once registered
docker compose exec nexus-engine sh -c "curl -fsS http://localhost:8081/ -w '\nHTTP %{http_code}\n' || true" 2>&1 | tail -3
docker compose ps nexus-engine 2>&1 | tail -3
```

Expected:
- First curl: HTTP 503 (worker still connecting) OR HTTP 200 (already connected — depends on timing)
- Second curl after 25s: **HTTP 200**
- `docker compose ps`: `nexus-engine` status `(healthy)`

If the second curl is still 503, the worker is failing to register — read `docker compose logs nexus-engine` for the cause (most often: invalid LIVEKIT_URL/key/secret in `.env`).

- [ ] **Step 16.3: Stop the engine container before next steps**

```bash
docker compose stop nexus-engine
```

- [ ] **Step 16.4: Write the in-process integration smoke test**

Create `backend/nexus/tests/interview_engine/test_engine_dispatch_in_process.py`:

```python
"""Phase 3 in-process dispatch contract — pin the no-HTTP-no-JWT path.

These tests exercise the path the merged engine actually walks at runtime:
  1. Dispatch metadata carries session_id + tenant_id + correlation_id.
  2. The engine opens get_bypass_session() (no Request).
  3. build_session_config(db, session_id=..., tenant_id=...) returns a
     SessionConfig identical to what the old HTTP endpoint produced.
  4. record_session_result(db, session_id=..., tenant_id=..., result=...,
     correlation_id=...) atomically transitions session.state from
     active → completed and writes the audit row.

We do NOT spin up a real LiveKit room — these are pure DB integration
tests against the schemas and services. Real LiveKit dispatch is
exercised in the staging end-to-end smoke (Stage J)."""

from __future__ import annotations

import uuid

import pytest

from app.database import get_bypass_session
from app.models import Session as SessionRow
from app.modules.interview_runtime.schemas import (
    QuestionResult,
    SessionResult,
    TranscriptEntry,
)
from app.modules.interview_runtime.service import (
    build_session_config,
    record_session_result,
)


@pytest.mark.asyncio
async def test_build_session_config_in_process_returns_full_config(
    db, seeded_active_session  # fixtures defined in tests/conftest.py
):
    """build_session_config from outside a request scope returns the
    same SessionConfig the old /api/internal/sessions/{id}/config did."""
    session_id, tenant_id = seeded_active_session
    config = await build_session_config(
        db, session_id=session_id, tenant_id=tenant_id,
    )
    assert config.session_id == str(session_id)
    assert len(config.stage.questions) > 0
    assert config.stage.stage_type in ("ai_screening", "phone_screen")
    assert config.company.industry  # company profile filled


@pytest.mark.asyncio
async def test_build_session_config_cross_tenant_returns_not_found(
    db, seeded_active_session
):
    """build_session_config with the wrong tenant_id raises ValueError —
    the application-layer filter is the defense layer post-Phase-3.
    Cross-tenant must produce the same error shape as 'unknown session'
    so existence is not leaked."""
    session_id, _real_tenant_id = seeded_active_session
    wrong_tenant_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await build_session_config(
            db, session_id=session_id, tenant_id=wrong_tenant_id,
        )


@pytest.mark.asyncio
async def test_record_session_result_in_process_completes_session(
    seeded_active_session,
):
    """record_session_result transitions the active session to completed
    using a fresh bypass session — same path the merged engine walks."""
    session_id, tenant_id = seeded_active_session
    result = SessionResult(
        session_id=str(session_id),
        ended_at_iso="2026-05-01T00:00:00Z",
        result_status="ok",
        questions_asked=2,
        questions_skipped=0,
        total_probes_fired=1,
        full_transcript=[
            TranscriptEntry(
                role="user", text="hello", timestamp_ms=1000,
            ),
            TranscriptEntry(
                role="agent", text="welcome", timestamp_ms=1500,
            ),
        ],
        question_results=[
            QuestionResult(
                question_id=str(uuid.uuid4()),
                final_status="completed",
                probes_fired=1,
                meets_bar=True,
                evaluation_summary="ok",
                evidence_quotes=[],
            ),
        ],
    )
    correlation_id = "test-corr-" + uuid.uuid4().hex[:8]
    async with get_bypass_session() as db:
        await record_session_result(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            result=result,
            correlation_id=correlation_id,
        )
        await db.commit()

    # Verify the session row transitioned.
    async with get_bypass_session() as db:
        from sqlalchemy import select
        sess = (
            await db.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        assert sess.state == "completed"
        assert sess.questions_asked == 2
        assert sess.agent_completed_at is not None


@pytest.mark.asyncio
async def test_record_session_result_idempotent_on_retry(
    seeded_active_session,
):
    """If the engine retries record_session_result after a successful
    first call, the second call is a silent no-op — engineering the
    state-machine post-Phase-3 to handle retries without writing a
    duplicate audit row."""
    session_id, tenant_id = seeded_active_session
    result = SessionResult(
        session_id=str(session_id),
        ended_at_iso="2026-05-01T00:00:00Z",
        result_status="ok",
        questions_asked=1,
        questions_skipped=0,
        total_probes_fired=0,
        full_transcript=[],
        question_results=[],
    )
    correlation_id = "test-corr-" + uuid.uuid4().hex[:8]

    async with get_bypass_session() as db:
        await record_session_result(
            db, session_id=session_id, tenant_id=tenant_id,
            result=result, correlation_id=correlation_id,
        )
        await db.commit()

    # Second call — no exception, silent no-op.
    async with get_bypass_session() as db:
        await record_session_result(
            db, session_id=session_id, tenant_id=tenant_id,
            result=result, correlation_id=correlation_id,
        )
        await db.commit()
    # No assertion needed beyond "no exception raised" — silent idempotence
    # is the contract.
```

The `seeded_active_session` fixture must be added to `tests/conftest.py` if it doesn't exist. It seeds: tenant, org_unit with company_profile, job_posting, signal_snapshot (confirmed), pipeline_instance, pipeline_stage (ai_screening), question_bank (confirmed, not stale), questions, candidate, candidate_job_assignment, session in `state='active'`. Reuse the seed helpers from `tests/conftest.py` (Phase 3C.1's tests already need most of this scaffolding).

If creating a new fixture is too invasive for this plan, the alternative is to write the test as an integration test that uses the existing recipe in `tests/test_interview_runtime_*` (which Task 12 deleted). Take whichever lift is smaller for the implementing engineer; the contract being tested is what matters, not the fixture mechanics.

- [ ] **Step 16.5: Run the new in-process tests**

```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/interview_engine/test_engine_dispatch_in_process.py -v 2>&1 | tail -20
```

Expected: 4 passed. If the fixture isn't ready, the test errors will identify what's missing — fix the fixture, not the test.

---

## Stage G — Source-tree cleanup

### Task 17: Delete the old `backend/interview_engine/` directory

**Files:**
- Delete: `backend/interview_engine/` (entire directory)

- [ ] **Step 17.1: Pre-delete grep — confirm nothing in nexus still imports the old paths**

Run:
```bash
cd backend/nexus
grep -rn "from agents\.\|from nexus_client\|from state_machine import\|from prompt_builder import\|from config import InterviewEngineConfig\|from context_loader import" app/ tests/ --include="*.py"
```

Expected: zero hits. Any hit means a file in nexus still imports from the old engine source layout — fix it (rewire to `app.modules.interview_engine.*`) before deleting.

- [ ] **Step 17.2: Merge `AGENTS.md` content into nexus/CLAUDE.md** (Task 18 covers the full merge)

Skip ahead to Task 18 if you haven't already done it. The deletion in Step 17.3 must come after AGENTS.md content has been preserved.

- [ ] **Step 17.3: Delete the directory**

```bash
rm -rf backend/interview_engine
ls backend/ | grep -i interview && echo "FAIL: interview_engine still present" || echo "OK"
```

Expected: `OK`. The `backend/` directory now contains only `nexus/` and (if any siblings exist) other top-level dirs.

- [ ] **Step 17.4: Verify nexus still passes its own pytest**

```bash
cd backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
```

Expected: a clean pass/fail summary close to the post-Phase-3 baseline (computed in Stage J). No NEW failures from the deletion.

---

### Task 18: Merge `AGENTS.md` into `nexus/CLAUDE.md`

**Files:**
- Reference: `backend/interview_engine/AGENTS.md`
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 18.1: Read the existing AGENTS.md and identify keep-worthy sections**

```bash
cat backend/interview_engine/AGENTS.md
```

The keep-worthy sections (relevant post-merge):
- LiveKit realtime tuning rationale (endpointing, VAD, interruption modes)
- Audio-pipeline observability pattern + PII discipline
- The general "engine is a LiveKit Agent worker" framing

The discard-worthy sections (Phase 3 makes them wrong):
- Two-venv hack details
- HTTP boundary contract
- nexus_client retry policy
- Engine JWT lifecycle
- references to "the engine container" as a separate process — it's still a separate compose service, but on the same image and venv now

- [ ] **Step 18.2: Add a new section to `backend/nexus/CLAUDE.md`**

Open `backend/nexus/CLAUDE.md`. Find the "Module Responsibilities → Phase 3C — Implemented" table and add a new row for `interview_engine`:

```
| `interview_engine` | Phase 3 — LiveKit Agents worker, in-process with nexus. `agent.py` is the per-session entrypoint (`@server.rtc_session`); `interviewer.py` is the `Agent` subclass that runs the InterviewStateMachine and records observations via `@function_tool`. Reads `SessionConfig` via in-process `build_session_config` and writes `SessionResult` via `record_session_result` (no HTTP boundary, no JWT — RLS + explicit-tenant filters in `app.modules.interview_runtime.service` are the defense layer). Runs as a separate compose service (`nexus-engine`) using `python -m app.modules.interview_engine`. Health check via the LiveKit `AgentServer` built-in HTTP at `:8081/`. |
```

Then add a new section AFTER the "AI Provider & Prompt Management" section, titled "Real-time Interview Engine":

```markdown
## Real-time Interview Engine — Load-Bearing Tuning

The interview engine drives candidate-facing voice interviews. Latency budgets and conversational quality are the load-bearing constraints; misconfigured tuning breaks the experience.

### Endpointing (when does the engine decide the candidate is done?)

`AgentSession.turn_handling.endpointing` is `dynamic` mode with `min_delay=0.3` and `max_delay=2.5`. Lower than LiveKit's default of `0.5` because interview turns tend to be punchy (yes/no, short factual answers); too long a floor feels sluggish. `max_delay=2.5` is below the doc-recommended ceiling for conversational agents — keeps the agent from feeling unresponsive when the candidate has finished but VAD isn't confident.

### Interruption (does the engine resume after a false interruption?)

`AgentSession.turn_handling.interruption.mode = "adaptive"` with `resume_false_interruption: True`. Coughs, background noise, and brief acknowledgments don't cut off the agent's speech.

### Silero VAD

`silero.VAD.load(activation_threshold=0.3, ...)`. Lower than LiveKit's default 0.5 because `ai_coustics` noise cancellation runs *before* VAD and attenuates real voice alongside noise — at 0.5 the cleaned signal often doesn't cross the speech bar in real-world environments. Drop further (0.2–0.25) if 0.3 misses voice; raise to 0.4–0.5 if VAD trips on non-speech noise.

All three knobs are env-driven via `app.config.settings.engine_*`. Do NOT hardcode in `agent.py`.

### Observability + PII discipline

`agent.py::_wire_session_observability` attaches structlog listeners covering every AgentSession event: VAD state, STT finality, EOU decisions, agent state, LLM/TTS metrics, function-tool calls, false interruptions, overlapping speech, errors, close.

Each record carries `elapsed_ms` (relative to session start) and `wall_ms` (event `created_at`) so per-turn latency waterfalls can be reconstructed by grepping `session_id` + sorting by `elapsed_ms`.

**Always-on fields are metadata only** (state names, finality flags, character counts, token counts, latency numbers, error types). **Verbose content** (verbatim STT transcripts, LLM message bodies, function-tool args/outputs) is gated behind `settings.engine_log_user_transcripts` and **must never be enabled in production**. Per CLAUDE.md PII discipline.

### Why a separate compose service?

`nexus-engine` runs `python -m app.modules.interview_engine` instead of uvicorn. It registers as a worker with LiveKit Cloud and accepts dispatched jobs. The compose service is separate from `nexus` and `nexus-worker` because the LiveKit Agent CLI manages its own process model (per-session subprocesses, prewarm, draining). Sharing the image is what consolidates the deps; sharing the *process* would require multiple LiveKit-server connections in one process and is not how the SDK is designed to run.

Health check: LiveKit Agents' `AgentServer` ships an HTTP endpoint at `0.0.0.0:8081/` returning 200 when registered, 503 otherwise. Compose uses it via `healthcheck:`. See `app.modules.interview_engine.agent` for the constructor pinning the port.
```

- [ ] **Step 18.3: Update the two-venv hack carve-out to its terminal state**

Phase 2 made the carve-out partially obsolete (openai 2.x + instructor 1.15 unblocked the two-venv requirement). Phase 3 closes it: there's only one venv now, no hack to document. Find the carve-out paragraph in CLAUDE.md (Phase 2's edit left it acknowledging the layout was still in place "until Phase 3 merges its source tree"). Replace the entire paragraph with:

```
- The interview-engine container was previously a separately-built Docker image with its own `pyproject.toml` and venv (a workaround for the openai 1.x cap that Phase 1 of the modular-monolith spec held in place). Phase 2 lifted the openai cap; Phase 3 merged the engine source tree into nexus's `app/modules/interview_engine/`. There is now exactly one image and one venv — `nexus`, `nexus-worker`, and `nexus-engine` all run from the same Dockerfile with different `command:` directives.
```

- [ ] **Step 18.4: Update the Phase 3C.2 status row in `nexus/CLAUDE.md`**

Find the "Phase 3C.2" row in the "Current State" table (top of CLAUDE.md). It currently reads something like "engine container ships independently". Replace with:

```
- **Phase 3C.2** — done: `/start` provisions a LiveKit room + mints candidate access token + dispatches the engine agent worker. Engine merged into nexus monolith in Phase 3 of the modular-monolith spec; now runs as the `nexus-engine` compose service from the same Docker image. Phase 3 also retired the `/api/internal/*` HTTP boundary and `engine_dispatch_tokens`/`engine_token_uses` tables; `build_session_config` and `record_session_result` are called in-process via `get_bypass_session()` with explicit tenant_id filters as the defense layer.
```

- [ ] **Step 18.5: Now safe to delete `backend/interview_engine/` (cycle back to Task 17.3)**

If Task 17.3 was deferred, run it now:
```bash
rm -rf backend/interview_engine
```

---

## Stage H — Documentation updates

### Task 19: Update root `CLAUDE.md` Phase 3C.2 row

**Files:**
- Modify: `CLAUDE.md` (root)

- [ ] **Step 19.1: Find the Phase 3C.2 row in the root phases table**

Open the root `CLAUDE.md`. Locate the "Current Phase Status" table — the Phase 3C.2 row mentions things like "engine worker container with structured-interview state machine; candidate live UI ported to LiveKit's agent-starter-react template". Add a parenthetical at the end:

```
| 3C.2 | LiveKit room + token provisioning on `/start`; `interview_runtime` internal API; engine worker container with structured-interview state machine; candidate live UI ported to LiveKit's `agent-starter-react` template via `@agents-ui` shadcn enclave (`<AgentSessionView_01>`, audio visualizers, control bar); engine graceful-close attribute (`session_outcome`); mid-session rejoin endpoint (`POST /rejoin`); realtime tuning (preemptive generation, dynamic endpointing, adaptive interruption). **Engine merged into nexus monolith in Phase 3 (single image, in-process API).** | ✅ done |
```

(only the bold sentence is added; everything else stays.)

- [ ] **Step 19.2: Verify the table renders cleanly**

Markdown tables don't have a strict syntax checker, but confirm the row is one logical line with the same number of `|` separators as the table header. Eyeball it.

---

### Task 20: Update `docs/security/threat-model.md`

**Files:**
- Modify: `docs/security/threat-model.md` (or create if it doesn't exist)

- [ ] **Step 20.1: Verify the file exists**

```bash
ls docs/security/threat-model.md 2>&1
```

If it doesn't exist, that's a separate gap in the project (the root CLAUDE.md mandates one). For Phase 3, **create** a minimal one that documents the engine-dispatch trust-boundary change. The full threat model is out of scope; we just need the section relevant to Phase 3.

- [ ] **Step 20.2: Add the engine-dispatch section**

Append (or create the file with) this content:

```markdown
# ProjectX Threat Model

Per-trust-boundary STRIDE notes. Updated when external services join, auth surfaces are added, tenant-isolation boundaries change, or the candidate-facing surface changes.

## Engine dispatch (Phase 3 — modular-monolith uplift)

### Boundary

Nexus's `start_session` dispatches an agent into a LiveKit room via `livekit_api.create_dispatch`. The engine receives a `metadata` JSON in the LiveKit dispatch carrying `session_id`, `tenant_id`, and `correlation_id`.

### Pre-Phase-3 controls

- HMAC-SHA256 JWT (`engine_dispatch_tokens` table; single-use per `(jti, endpoint)` via `engine_token_uses` composite PK).
- HTTP boundary at `/api/internal/sessions/{id}/{config,results}` gated by Bearer JWT.
- Auth-middleware exemption for `/api/internal/`.

### Post-Phase-3 controls

- HTTP boundary deleted; `build_session_config` and `record_session_result` are pure Python calls inside the same Python process.
- Both helpers run on a `get_bypass_session()` (RLS bypass) DB session AND filter every query by an explicit `tenant_id` parameter — the application-layer filter is the defense.

### Residual threats

| Threat | Pre-Phase-3 mitigation | Post-Phase-3 mitigation | Residual risk |
|---|---|---|---|
| Forged dispatch (attacker injects fake job into LiveKit) | Required a valid `engine_dispatch_tokens` JWT signed by `INTERVIEW_ENGINE_JWT_SECRET` | Requires LiveKit API key + secret to forge a CreateAgentDispatchRequest | LOW. Equivalent to leaking the LiveKit API secret which would already give the attacker far broader capabilities. |
| Cross-tenant config read (engine for tenant A reads tenant B's `SessionConfig`) | JWT's `tenant_id` claim was checked against the request | The `tenant_id` from dispatch metadata is filter-applied to every query; cross-tenant queries return "not found" (same shape as unknown session, no leak) | LOW. Compromising the LiveKit dispatch metadata channel is equivalent to compromising the LiveKit API secret. |
| Replay of a result POST | `engine_token_uses` composite PK rejected the second use of `(jti, '/results')` | `record_session_result` is gated on session.state='active' (atomic UPDATE); a second call after `state='completed'` is a silent no-op, not a duplicate | LOW. The state-machine atomicity replaces the JWT single-use enforcement. |
| Rogue compose service connecting to the DB and impersonating the engine | The engine's HTTP token was the only path to write SessionResult | Anything inside the same Postgres network can write directly to the `sessions` table — but this was always true for any service holding `nexus_app` role credentials | UNCHANGED. The DB perimeter is the boundary; engine merge does not change it. |

### When to revisit

Reintroduce a signed dispatch envelope (HMAC over the metadata payload) if any of the following change:
- The LiveKit dispatch path becomes reachable from a less-trusted actor (e.g., third-party can dispatch into our project).
- The engine moves out of the same Postgres network (e.g., enterprise client requires the engine in their VPC).
- A real-world incident demonstrates LiveKit-credential leakage as a viable attack path.

```

If the file already exists with prior content, append the "Engine dispatch (Phase 3 — modular-monolith uplift)" section to it; do not overwrite.

---

## Stage I — Verification + cleanup commits

### Task 21: Full pytest baseline check

**Files:** none (verification)

- [ ] **Step 21.1: Run the full suite**

```bash
cd backend/nexus
docker compose run --rm nexus pytest --tb=short -q 2>&1 | tail -15
```

Expected pass count: `(pre-Phase-3 baseline) − (deleted tests) + (added tests)`. The deleted tests:
- `test_engine_tokens_rls.py` — count tests with `grep -c "^def test_" /tmp/<the file>` BEFORE Task 12 deletes it (record this as `D` in Task 12)
- `test_interview_runtime_authz.py` — same
- `test_interview_runtime_config.py` — same
- `test_interview_runtime_results.py` — same

The added tests:
- `tests/interview_engine/test_engine_dispatch_in_process.py` — 4 tests (Task 16)
- Whatever moved over from `backend/interview_engine/tests/` (Task 8) — count once moved

Expected: **same number of FAILED tests as the pre-Phase-3 baseline (9), zero new failures.**

- [ ] **Step 21.2: Compare failure list**

```bash
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase3_post_fails.txt
diff /tmp/phase3_baseline_fails.txt /tmp/phase3_post_fails.txt
```

Expected: only added/removed lines correspond to the deleted Phase-3 test files. No new failures from existing tests.

If a previously-passing test now fails, **STOP** and fix it before moving on. The most common Phase-3-induced failures:
- Import errors from a missed rewire — fix the import.
- A test that constructed `EngineDispatchToken` directly — the test was testing the deleted layer; delete the test.
- Mapper-config errors — a relationship still references the deleted ORM class. Read the traceback to find it.

---

### Task 22: End-to-end smoke (full session, real LiveKit)

**Files:** none (operational verification)

- [ ] **Step 22.1: Bring up all four services**

```bash
cd backend/nexus
docker compose up nexus nexus-worker nexus-engine -d
sleep 30
docker compose ps 2>&1 | grep -E "nexus|engine|redis"
```

Expected: `nexus`, `nexus-worker`, `nexus-engine`, `redis` all running. `nexus-engine` reports `(healthy)`.

- [ ] **Step 22.2: Drive a real candidate session end-to-end**

Use whichever path is most convenient — recruiter UI to invite a test candidate, candidate UI to do the pre-check / consent / OTP / start, then complete the interview. The verification gates:
1. `nexus-engine` logs show `engine.dispatch.received` (the in-process metadata read works)
2. `nexus-engine` logs show `interview_runtime.session_config.built` (build_session_config ran)
3. The interview proceeds — candidate hears greeting + first question, can answer
4. On close, `nexus-engine` logs show `interview.result.persisted`
5. The session row in Postgres transitions `active` → `completed` with `agent_completed_at` populated

Run:
```bash
docker compose logs -f nexus-engine
```

In a separate terminal, drive the session and watch the log stream. Capture the full session log on success — it goes into the PR body.

- [ ] **Step 22.3: Verify the candidate UI still observes `session_outcome`**

The frontend's `useSessionOutcome` hook reads the LiveKit `session_outcome` participant attribute the engine publishes on close. The contract is frozen per the umbrella spec § Q5. After a successful session, the candidate sees the completion screen — that confirms the attribute still flows.

- [ ] **Step 22.4: Stop the services**

```bash
docker compose stop nexus nexus-worker nexus-engine
```

---

## Stage J — Commit + open PR

### Task 23: Stage and review the diff

**Files:** all of the above

- [ ] **Step 23.1: Inspect the full diff one more time**

```bash
cd /home/ishant/Projects/ProjectX
git status
git diff --stat
```

Expected files modified/created/deleted:
- Modified: `backend/nexus/pyproject.toml`, `uv.lock`, `app/config.py`, `.env.example`, `docker-compose.yml`, `app/main.py`, `app/middleware/auth.py`, `app/models.py`, `app/modules/session/livekit.py`, `app/modules/session/service.py`, `app/modules/interview_runtime/service.py`, `app/modules/interview_runtime/errors.py`, `app/modules/auth/service.py`, `CLAUDE.md` (nexus + root), `docs/security/threat-model.md`
- Created: `app/modules/interview_engine/` (entire package), `migrations/versions/0025_drop_engine_dispatch_tables.py`, `tests/interview_engine/` (entire dir + new contract test)
- Deleted: `app/modules/interview_runtime/router.py`, `tests/test_engine_tokens_rls.py`, `tests/test_interview_runtime_authz.py`, `tests/test_interview_runtime_config.py`, `tests/test_interview_runtime_results.py`, `backend/interview_engine/` (entire dir)

If the diff includes anything else, investigate before committing.

---

### Task 24: Commit

**Files:** all changes from Stages B–H.

- [ ] **Step 24.1: Stage everything**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/pyproject.toml \
        backend/nexus/uv.lock \
        backend/nexus/app/config.py \
        backend/nexus/.env.example \
        backend/nexus/docker-compose.yml \
        backend/nexus/app/main.py \
        backend/nexus/app/middleware/auth.py \
        backend/nexus/app/models.py \
        backend/nexus/app/modules/session/livekit.py \
        backend/nexus/app/modules/session/service.py \
        backend/nexus/app/modules/interview_runtime/service.py \
        backend/nexus/app/modules/interview_runtime/errors.py \
        backend/nexus/app/modules/auth/service.py \
        backend/nexus/app/modules/interview_engine \
        backend/nexus/migrations/versions/0025_drop_engine_dispatch_tables.py \
        backend/nexus/tests/interview_engine \
        backend/nexus/livekit.toml \
        backend/nexus/CLAUDE.md \
        CLAUDE.md \
        docs/security/threat-model.md
git rm -rf backend/interview_engine
git rm backend/nexus/app/modules/interview_runtime/router.py \
       backend/nexus/tests/test_engine_tokens_rls.py \
       backend/nexus/tests/test_interview_runtime_authz.py \
       backend/nexus/tests/test_interview_runtime_config.py \
       backend/nexus/tests/test_interview_runtime_results.py
```

- [ ] **Step 24.2: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(engine): merge interview engine into nexus monolith (Phase 3)

Phase 3 of the modular-monolith uplift. Moves backend/interview_engine/
into backend/nexus/app/modules/interview_engine/, drops the engine-
dispatch JWT + /api/internal/* HTTP boundary, and switches the engine
to in-process calls into app.modules.interview_runtime.service. One
Docker image runs three command-variants (nexus, nexus-worker, nexus-
engine).

What changed:
- Engine source moved into app/modules/interview_engine/ (agent.py,
  interviewer.py, state_machine.py, prompt_builder.py, context_loader.py,
  __init__.py, __main__.py).
- nexus pyproject.toml absorbs livekit-agents + plugin pins. uv.lock
  regenerated. wrapt + opentelemetry pin cluster from Phase 1 unchanged;
  openai 2.x + instructor 1.15.x from Phase 2 unchanged.
- Engine reads SessionConfig via in-process build_session_config and
  writes SessionResult via record_session_result. Both run on
  get_bypass_session() with explicit tenant_id filters as the new
  defense layer (RLS bypass + app-layer filter).
- mint_engine_dispatch_jwt + verify_engine_token + EngineTokenInvalidError
  deleted. The auth → interview_runtime inverted import edge resolved by
  deletion.
- /api/internal/* router deleted; auth middleware exemption removed.
- nexus-engine compose service added with LiveKit AgentServer's built-in
  HTTP healthcheck at port 8081.
- Alembic 0025 drops engine_dispatch_tokens + engine_token_uses tables.
- InterviewEngineConfig fields absorbed into nexus Settings as
  engine_*. interview_engine_jwt_secret + nexus_internal_base_url env
  vars retired. interview_agent_name renamed to engine_agent_name.
- AGENTS.md content (realtime tuning + observability) merged into
  nexus/CLAUDE.md as a "Real-time Interview Engine" section. Two-venv
  hack carve-out lifted entirely.
- threat-model.md updated with the engine-dispatch trust-boundary
  change (RLS+app-layer-filter as defense; residual threat documented).

Verification:
- pytest baseline preserved minus the 4 deleted JWT/HTTP-layer tests
  plus the new in-process contract test (4 added). Same 9 pre-existing
  failures unchanged.
- nexus-engine compose service boots, registers with LiveKit Cloud,
  health endpoint at :8081 returns 200.
- End-to-end smoke: real candidate interview in staging completed
  cleanly through the new in-process path; session row transitioned
  active → completed; session_outcome attribute published.

Cutover decision: single PR per Phase 3 brainstorming (no users in
production; rollback safety is a code revert + alembic downgrade).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 25: Open PR

**Files:** none (git/gh).

- [ ] **Step 25.1: Push the branch**

```bash
git push -u origin feat/phase-3-engine-merge
```

(The branch is created from `feat/phase-3c2-interview-engine` at Phase 3 start — see "Branch strategy" in the Phase 3 handoff.)

- [ ] **Step 25.2: Open the PR**

```bash
gh pr create \
  --base feat/phase-3c2-interview-engine \
  --title "feat(engine): merge interview engine into nexus monolith (Phase 3)" \
  --body "$(cat <<'EOF'
## Summary

Phase 3 of the modular-monolith uplift (`docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md` § Phase 3).

Moves `backend/interview_engine/` into `backend/nexus/app/modules/interview_engine/` and retires the HTTP/JWT boundary between them. The engine now runs as the `nexus-engine` compose service, built from the same Docker image as `nexus` and `nexus-worker`, just with a different `command:`.

- **Engine source merged** under `app/modules/interview_engine/` (agent.py, interviewer.py, state_machine.py, prompt_builder.py, context_loader.py, __init__.py, __main__.py).
- **HTTP boundary retired** — `/api/internal/sessions/{id}/{config,results}` deleted; engine calls `build_session_config` and `record_session_result` directly from `app.modules.interview_runtime.service`.
- **JWT layer retired** — `mint_engine_dispatch_jwt`, `verify_engine_token`, `EngineTokenInvalidError`, and the `auth → interview_runtime` inverted import edge all gone.
- **Tables dropped** — Alembic 0025 drops `engine_dispatch_tokens` + `engine_token_uses`.
- **Config consolidated** — `InterviewEngineConfig` fields absorbed into nexus `Settings` as `engine_*`. `INTERVIEW_ENGINE_JWT_SECRET` + `NEXUS_INTERNAL_BASE_URL` env vars retired.
- **Frontend untouched** — frozen contract per umbrella spec § Q5.

Phase 1's OTel pin cluster and Phase 2's openai/instructor/Python pins are preserved unchanged.

## Cutover decision

Single PR. Confirmed during brainstorming on the basis that no users are in production; rollback is a code revert + `alembic downgrade -1`. The phased-cutover safety value (preserving an HTTP fallback path during a soak window) is moot in this state.

## Test plan

- [x] `pytest tests/interview_engine/test_engine_dispatch_in_process.py` — 4 new contract tests pass
- [x] Full pytest baseline preserved: pre-Phase-3 baseline minus 4 deleted tests (`test_engine_tokens_rls`, `test_interview_runtime_authz`, `test_interview_runtime_config`, `test_interview_runtime_results`) plus 4 new tests; same 9 pre-existing failures unchanged
- [x] `docker compose build --no-cache nexus` — clean rebuild on Python 3.13 with livekit-agents + plugins absorbed
- [x] `docker compose up nexus nexus-worker nexus-engine -d` — all three boot; `nexus-engine` reports `(healthy)` after registering with LiveKit Cloud (port 8081 health probe flips 503 → 200)
- [x] `_assert_rls_completeness` passes after migration 0025 (`engine_dispatch_tokens` removed from `_TENANT_SCOPED_TABLES`)
- [x] **End-to-end real-LiveKit smoke**: drove a full candidate interview through invite → pre-check → consent → OTP → start → engine dispatch → in-process config fetch → interview turn-taking → close → in-process result persist. Session row transitions `active` → `completed`; `agent_completed_at` populated; `session_outcome` LiveKit attribute published; candidate sees completion screen.
- [x] `pip-audit` — same advisories as Phase 2 baseline; no NEW criticals

## Frontend impact

None. Phases 1–4 are frozen contract per Q5. The candidate UI continues to consume `/api/candidate-session/{token}/start` (unchanged), `session_outcome` participant attribute (unchanged), and progress attributes (unchanged).

## Out of scope (per spec)

- Models split + module public-API discipline (Phase 4)
- Package upgrade sweep beyond engine deps (Phase 5)
- Sentry wiring (deferred per Q1.1)
- Custom observability dashboard

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: gh prints the PR URL. Capture it; the agent driving the plan returns it as the result.

---

## Self-Review

**1. Spec coverage:**
- ✅ File moves (interview_engine/agent.py → modules/interview_engine/agent.py, etc.) — Tasks 4–7
- ✅ Cross-module imports rewired (no `from agents.*`, no `from nexus_client`, no `from config import InterviewEngineConfig`) — Tasks 5.1, 6.1, 7.2, 7.3, 8.3
- ✅ `__main__.py` LiveKit CLI entrypoint — Task 4.2
- ✅ livekit-agents + plugins absorbed into nexus pyproject.toml — Task 2
- ✅ `dispatch_agent` simplified, `engine_jwt` parameter dropped — Task 9.1
- ✅ `mint_engine_dispatch_jwt` deleted — Task 9.2
- ✅ `start_session` updated — Task 9.3
- ✅ `/api/internal/*` router + middleware exemption deleted — Task 10
- ✅ `verify_engine_token` + `EngineTokenInvalidError` deleted — Task 11
- ✅ Inverted auth → interview_runtime edge fixed — Task 11.5
- ✅ Migration 0025 drops `engine_dispatch_tokens` + `engine_token_uses` — Task 13
- ✅ `EngineDispatchToken` + `EngineTokenUse` ORM classes removed — Task 14
- ✅ `_TENANT_SCOPED_TABLES` updated — Task 15
- ✅ `nexus-engine` compose service with LiveKit-built-in healthcheck — Task 16
- ✅ get_bypass_session() pattern (resolved spec open Q1) — Tasks 5.1, 6.1
- ✅ `livekit.toml` moved to nexus root — Task 8.1
- ✅ Tests moved + rewired — Tasks 8.2, 8.3
- ✅ Obsolete tests deleted — Task 12
- ✅ AGENTS.md content merged into nexus/CLAUDE.md before deletion — Task 18
- ✅ `backend/interview_engine/` directory deleted — Task 17
- ✅ Frontend frozen contract preserved — no frontend changes anywhere
- ✅ Phase 3C.2 status row updated in both nexus/CLAUDE.md (Task 18.4) and root CLAUDE.md (Task 19)
- ✅ threat-model.md updated — Task 20
- ✅ End-to-end smoke verifying session_outcome + state transition — Task 22
- ✅ Migration 0025 round-trip verified — Task 13.5
- ✅ Old engine container's `results/` disk-fallback deleted — implicit in Task 17.3 (whole dir gone)
- ✅ `interview_runtime/schemas.py` kept (in-process contract) — never touched, by design

**2. Placeholder scan:**

- No `TBD`, `TODO`, `implement later`. Each step has the actual command, code, or decision rule it needs.
- Step 8.1's "If it references `agent.py` or `interview_engine` as a module name" is a real conditional based on file content — not a placeholder, but a directive to inspect first.
- Step 16.4's `seeded_active_session` fixture is referenced; I noted it must be added if missing OR the test rewritten to use existing seed helpers — both options are concrete enough to follow.

**3. Type / signature consistency:**

- `dispatch_agent`: pre-Phase-3 signature `(*, room_name, session_id, tenant_id, engine_jwt, correlation_id)` → post `(*, room_name, session_id, tenant_id, correlation_id)` — Task 9.1 matches.
- `record_session_result`: pre `(*, session_id, tenant_id, result, jti)` → post `(*, session_id, tenant_id, result, correlation_id)` — Tasks 11.2 + 11.3 + 16.4 all match.
- `InterviewerAgent.__init__`: pre `(*, session_config, engine_config, nexus_jwt, nexus_base_url)` → post `(*, session_config, tenant_id, correlation_id)` — Tasks 5.1 (call site) + 6.1 (definition) match.
- `build_system_prompt`: pre `(session_config, engine_config)` → post `(session_config)` — Tasks 6.1 (call site) + 7.2 (definition) match.
- Settings field renames: `interview_agent_name` → `engine_agent_name` consistent across Tasks 3.1, 5.1, 9.1.

No gaps surfaced. Plan is internally consistent.

---

**End of plan.**
