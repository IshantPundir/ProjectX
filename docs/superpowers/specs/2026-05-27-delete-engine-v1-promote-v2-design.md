# Delete Interview Engine v1 — Promote v2 to the Sole `interview_engine`

**Date:** 2026-05-27
**Status:** Approved (design) — pending implementation plan
**Author:** Engine cleanup pass

---

## 1. Goal

Remove the legacy interview engine (`app/modules/interview_engine/`, "v1") entirely, rename
the current `app/modules/interview_engine_v2/` → `app/modules/interview_engine/`, and rip the
v1↔v2 selection machinery out end-to-end.

**End state:** one engine, no `interview_engine_version` flag anywhere, no `_v2` suffix, and no
dead prompts / config / tests / docs referring to v1 or to engine selection.

This is a **pure deletion + rename + flag-removal**. No behavioral feature work. The two open
engine-lifecycle bugs in `tmp/ERROR.md` (reaper threshold; record-on-non-clean-end) are
explicitly **out of scope** and remain a separate task.

### Context: why this is safe now

- Full development mode — no real users, no production data, no backward-compatibility constraint
  (per user). v2 is already validated across many live talk-tests ("working now", 2026-05-24).
- v2 (`interview_engine_v2/`) is default-OFF today (`INTERVIEW_ENGINE_DEFAULT_VERSION=v1`,
  per-job `job_postings.interview_engine_version` opt-in). The user currently flips jobs to `v2`
  manually. After this change there is nothing to flip — v2 is the only path.

---

## 2. Current architecture (what we're changing)

- **Entrypoint / dispatch:** `docker-compose.yml` runs the engine container as
  `python -m app.modules.interview_engine start`. v1's `agent.py` owns the LiveKit harness:
  - `server = AgentServer(host="0.0.0.0", port=8081)`
  - `prewarm(proc)` (bootstraps the OTel tracer provider) wired via `server.setup_fnc = prewarm`
  - process-startup plugin imports for `download-files` (`livekit.plugins.turn_detector.multilingual`)
  - `@server.rtc_session(agent_name=settings.engine_agent_name)` `entrypoint(ctx)` →
    `_run_entrypoint(...)` wrapped by `_handle_entrypoint_failure(...)`
  - `_run_entrypoint` builds the `SessionConfig` (+ `tenant_settings`) and then **branches**:
    `if should_run_v2(session_config): await run_v2(ctx, config, …); return` — else it runs the
    v1 orchestrator/judge/speaker/state stack.
- **v2** (`interview_engine_v2/agent.py`) has a self-contained `run(ctx, session_config, tenant_id,
  correlation_id)` — its own connect / turn-loop / mouth / brain / triage — but **no standalone
  bootstrap**. It relies on v1's `agent.py` to set up the worker and fetch the config.
- **Selection flag:** `job_postings.interview_engine_version` (`'v1'|'v2'|NULL`, migration `0044`,
  CHECK `ck_job_postings_interview_engine_version`). Resolved in `interview_runtime/service.py`:
  `job.interview_engine_version or AIConfig().interview_engine_default_version`, written onto
  `SessionConfig.interview_engine_version`. `selection.should_run_v2(config)` reads it. The flag is
  **purely backend-internal** — verified absent from `frontend/` and `jd/schemas.py`.
- **Shared, NOT being deleted:** `app/modules/interview_runtime/` (`build_session_config`,
  `record_session_result`, the `SessionConfig` / `SessionResult` wire contract). It only loses the
  version-resolution logic and the `interview_engine_version` field.

---

## 3. Work areas

### Area 1 — Absorb the worker bootstrap into the (renamed) engine's `agent.py`

Port the LiveKit harness from v1's `agent.py` into v2's `agent.py`, **dropping the branch**:

- Add at module level: `server = AgentServer(...)`, `prewarm` + `server.setup_fnc = prewarm`, the
  turn-detector process-startup import, the `@server.rtc_session(agent_name=settings.engine_agent_name)`
  `entrypoint(ctx)`, `_run_entrypoint`, and `_handle_entrypoint_failure` (the latter reuses
  `classify_engine_exception` / `transition_to_error` from `app.modules.session`, exactly as v1 does).
- `_run_entrypoint` builds the `SessionConfig` via `build_session_config` and calls the existing
  `run(...)` **unconditionally** (no `should_run_v2`, no v1 fall-through). Confirm during
  implementation whether `run` needs `tenant_settings` fetched here or fetches its own — v1 fetched
  it for the v1 StateEngine but did **not** pass it to `run_v2`, so the new `_run_entrypoint` only
  needs `build_session_config` unless `run` is changed to accept it.
- New `__init__.py` for the renamed package: eager-export the pure (livekit-free) artifacts already
  exported today (`Directive`, `DirectiveAct`, `DirectiveTone`, `DirectiveController`,
  `TurnDecisionRecord`) **minus `should_run_v2`**, plus lazy `server` and `run` via PEP-562
  `__getattr__` (keeps livekit out of the FastAPI/nexus process — load-bearing, see backend
  CLAUDE.md "AI Provider" carve-out).
- New `__main__.py`: `from <pkg>.agent import server; cli.run_app(server)`.

**Risk note:** this is the only step with behavioral risk. The port is near-verbatim from v1's
working `entrypoint` / `_run_entrypoint` minus the branch. Validated by a real session start
(Area 7 verification).

### Area 2 — Rename `interview_engine_v2` → `interview_engine`

Performed **after** v1 is deleted (so there is no path collision).

- `git mv app/modules/interview_engine_v2 app/modules/interview_engine` (after `git rm -r` of v1).
- Rewrite every `from app.modules.interview_engine_v2 …` / `app.modules.interview_engine_v2.…`
  → `interview_engine` across `app/` and `tests/`.
- Delete `selection.py` + `should_run_v2` (no selection remains).
- Update the v1-branch call site (the `from app.modules.interview_engine_v2 import run as run_v2,
  should_run_v2` import is removed entirely; the bootstrap from Area 1 calls `run` directly).
- Audit module docstrings / comments that say "v2", "legacy `interview_engine`", "parallel module",
  "default-off", "reference-only" — rewrite to describe a single engine.

### Area 3 — Rip out the `interview_engine_version` flag, end-to-end

- **Migration `0047_drop_interview_engine_version`** (down_revision `0046`):
  - `upgrade`: drop CHECK `ck_job_postings_interview_engine_version`, then
    `op.drop_column("job_postings", "interview_engine_version")`.
  - `downgrade`: re-add the column (`sa.Text()`, nullable) + the CHECK (mirrors `0044` upgrade) —
    this is the required rollback script. Migration `0044` is left untouched (no editing shipped
    migrations).
- `app/modules/jd/models.py`: remove the `interview_engine_version` mapped column.
- `app/modules/interview_runtime/schemas.py`: remove `SessionConfig.interview_engine_version`
  (field + its docstring block).
- `app/modules/interview_runtime/service.py`: remove the `resolved_engine_version` computation and
  both `interview_engine_version=resolved_engine_version` kwargs (in the `SessionConfig(...)` build
  and the `logger.info(...)` call). Drop the now-unused `AIConfig` import if nothing else needs it.
- `app/config.py`: remove `interview_engine_default_version` (line ~496) + its comment block.
- `app/ai/config.py`: remove the `interview_engine_default_version` property (lines ~172–174).
- `app/modules/reporting/router.py` (~line 190): drop
  `AND (sr.id IS NOT NULL OR j.interview_engine_version = 'v2')` from the hub query (every completed
  session is now scoreable). Update the docstring (lines ~171–177) accordingly. Remove the
  now-dead `LEFT JOIN session_reports sr` only if nothing else in the query references `sr` — it
  still selects `sr.status`/`sr.verdict`/`sr.overall_score`, so **keep the join**; only the
  version predicate is removed.

### Area 4 — Config / `realtime.py` cleanup (careful, not blind)

**Method (mandatory):** for each candidate field below, `grep -rn` its usages across `app/` first.
Remove **only** if every remaining consumer is a file being deleted in this change. Where a *shared*
factory defaults to a v1 field, rebase the default onto the surviving v2/shared field before removal.

Cleanly v1-only (delete from `app/config.py`, `app/ai/config.py` properties, `.env.example`):
- Judge: `engine_judge_model`, `engine_judge_total_budget_ms`, `engine_judge_retry_wait_ms`,
  `engine_judge_prompt_version`
- Speaker: `engine_speaker_model`, `engine_speaker_max_output_tokens`, `engine_speaker_prompt_version`
- State: `engine_checkpoint_turns`, `engine_checkpoint_seconds`, `engine_claims_pool_max`
- Continuation watcher: `engine_continuation_enabled`, `engine_continuation_min_word_count`,
  `engine_continuation_consecutive_abort_cap`
- Idle ladder (v1; v2 has its own unresponsive ladder): `engine_idle_first_nudge_seconds`,
  `engine_idle_second_nudge_seconds`, `engine_idle_give_up_seconds`
- v1 endpointing (v2 has `engine_v2_endpointing_*`): `engine_endpointing_mode`,
  `engine_endpointing_min_delay`, `engine_endpointing_max_delay`
- v1 realtime LLM: `interview_llm_model`, `interview_reasoning_effort` + `realtime.build_llm_plugin()`
- v1 turn-detector default: `interview_turn_detector_unlikely_threshold`

**Traps to handle explicitly:**
- `realtime.build_turn_detector()` defaults its `unlikely_threshold` to
  `ai_config.interview_turn_detector_unlikely_threshold`. v2 passes
  `engine_v2_turn_detector_unlikely_threshold`. Before removing the v1 field, change the factory's
  default to the v2 field (or make the arg required and confirm v2 always passes it).
- Verify each of `engine_task_budget_overhead_seconds`, `engine_closing_drain_timeout_seconds`,
  `engine_session_ended_message`, `engine_log_audio_events`, `engine_log_user_transcripts`,
  `engine_event_log_*` by grep — keep any the renamed v2 engine still reads; remove the rest.

**Keep (shared or v2):** `engine_agent_name`, all `engine_v2_*`, all `engine_brain_*` /
`engine_mouth_*` / `engine_triage_*`, `interview_noise_cancellation`, `interview_nc_enhancement_level`,
`interview_stt_*`, `interview_tts_*`, `reaper_*`, `build_mouth_llm_plugin`, `build_stt_plugin`,
`build_tts_plugin`, `build_vad`, `build_noise_cancellation`, `build_interruption_options`.

### Area 5 — Prompts

- **Delete `prompts/v2/engine/`** (v1's `judge.system.txt`, `speaker/*`, `CHANGELOG.md`).
- **Keep** `prompts/v3/engine/` (brain/triage/mouth — the surviving engine), `prompts/v3/report_scorer/`,
  and all `question_bank_*` + JD prompts under `prompts/v1/` and `prompts/v2/` (these belong to the
  JD / question-bank pipelines — independent prompt versioning, not engine code; untouched).
- Note: prompt-version directories (`v1`/`v2`/`v3`) are a content-versioning scheme decoupled from
  the module name. The renamed engine keeps reading `v3/engine/*` via `engine_*_prompt_version="v3"`.
  No prompt renaming.

### Area 6 — Tests

- `git rm -r tests/interview_engine/` (v1 tests).
- `git mv tests/interview_engine_v2 tests/interview_engine` and rewrite imports.
- Delete the engine-selection tests:
  - `tests/interview_engine_v2/test_entrypoint_branch.py` (the whole branch concept is gone)
  - `tests/interview_runtime/test_build_session_config_engine_version.py`
  - `tests/interview_runtime/test_engine_version_column.py`
  - the two `interview_engine_version` assertions in `tests/interview_runtime/test_schemas.py`
    (and any fixture kwarg that sets it).
- Add a small test asserting the entrypoint dispatches to the single engine (replacing
  `test_entrypoint_branch.py`'s intent): a `SessionConfig` with no version field still routes to the
  engine `run`.
- Re-confirm `tests/test_module_boundaries.py` `KNOWN_DOMAIN_MODULES` — `interview_engine_v2` →
  `interview_engine` if listed; ensure no stale entry.

### Area 7 — Docs

- **`backend/nexus/CLAUDE.md`**: rewrite the Phase 3D.engine (v1 legacy) + 3D.engine-v2 bullets into
  a single "interview engine" description; remove the selection/cutover language; update the Module
  Structure tree (drop `interview_engine` v1 line, rename `interview_engine_v2` → `interview_engine`);
  add migration `0047` to the migrations list and update the head pointer; update the engine compose
  command (`python -m app.modules.interview_engine`, no branch); fix the coverage-workaround
  `--source` path example.
- **Root `CLAUDE.md`**: update the Phase 3D / 3D.engine-v2 rows in the status table to describe one
  engine (no default-OFF / per-job flag language).
- **`docker-compose.yml`**: the engine command is already `python -m app.modules.interview_engine`
  (the package name is unchanged by the rename — v1 had it, v2 inherits it). Update the comment that
  says "dispatches to v1 or v2".
- **Leave dated specs/plans under `docs/superpowers/specs/` + `docs/superpowers/plans/` as-is** —
  they are point-in-time design records (incl. the v2 master plan). Rewriting them would be
  revisionism, not cleanup.

---

## 4. Out of scope (explicitly)

- The two `tmp/ERROR.md` engine-lifecycle bugs (reaper 15-min threshold; engine not recording a
  result on non-clean session end). Separate task.
- The engine-dispatch JWT purpose label `purpose: Literal["interview_engine"]`
  (`auth/schemas.py:52`) — kept. It is a stable dispatch contract, not v1 code, and the label stays
  accurate after the rename.
- Question-bank / JD prompt version directories — untouched.
- Renaming or restructuring `interview_runtime` — only the version field/logic is removed.

---

## 5. Verification

Run after each area, and a full pass at the end:

1. `ruff check .` clean.
2. Zero-residue greps (must all return nothing):
   - `grep -rn "interview_engine_v2\|should_run_v2" app/ tests/`
   - `grep -rn "interview_engine_version\|interview_engine_default_version" app/ tests/ .env.example docker-compose.yml`
   - `grep -rn "engine_judge_\|engine_speaker_\|build_llm_plugin\|interview_llm_model" app/`
   - `grep -rn "prompts/v2/engine\|v2/engine" app/ docker-compose.yml`
3. Migration round-trips: `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head`
   clean (in the dev container).
4. Test suite: the renamed `tests/interview_engine/` subtree + `tests/interview_runtime/` pass
   (`pytest -m "not prompt_quality"`). `tests/test_module_boundaries.py` passes.
5. Engine boots and runs a real session end-to-end:
   `docker compose up -d --force-recreate nexus-engine`, start one interview, confirm the candidate
   gets the v2 opener, the brain/triage/mouth loop runs, and `record_session_result` writes
   `completed` + `coverage_summary` (no `engine_unresponsive`).

---

## 6. Risk & rollback

- **Primary risk:** the bootstrap port (Area 1) drops a setup step → engine won't dispatch.
  Mitigation: near-verbatim port of v1's proven entrypoint; live-session smoke test gates completion.
- **Rollback:** the work lives on a branch; `git` reverts it. The DB migration has a real
  `downgrade`. Nothing is pushed to origin / deployed without the user's explicit say-so (consistent
  with the project's "push = Railway deploy = user's call" rule).
