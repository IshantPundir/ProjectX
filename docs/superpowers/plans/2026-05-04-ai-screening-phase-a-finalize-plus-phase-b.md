# AI Screening Agent — Phase A finalize + Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the small Phase A tail (3 commits) and execute Phase B end-to-end (6 commits + smoke gate + close-out): replace `GenericInterviewAgent` with `StructuredInterviewAgent`, wire the deterministic state machine + three-layer guardrail, drive every utterance via hardcoded English strings through `await session.say(...)`, and ship integration coverage.

**Architecture:** Pattern 2 from impl doc §4 — the realtime LLM is fully bypassed by overriding `Agent.llm_node` to emit zero chunks; the orchestrator drives every utterance via `await session.say(text)` after a `safety.check_safety` gate. State machine + ledger live in `app/modules/interview_engine/orchestrator/`. The structured agent class lives at `app/modules/interview_engine/structured_agent.py` and is wired into the existing `agent.py` entrypoint without touching `_wire_session_observability` / `_wire_close_handler` / `_wire_participant_disconnect`. Hardcoded utterances are throwaway and live at `_phase_b_utterances.py` with a deletion-criterion docstring + tripwire sentence; Phase C replaces them.

**Tech Stack:** Python 3.13 async, SQLAlchemy async / asyncpg, `livekit-agents>=1.5.4`, `openai>=2.10` + `instructor>=1.15`, `redis.asyncio` (via `app.pubsub._get_client()`), structlog, pytest-asyncio, mypy `--strict`, ruff.

**Spec:** `docs/superpowers/specs/2026-05-04-ai-screening-phase-a-finalize-plus-phase-b-design.md` (commits `a65c1c4` + `086cf74` + `f12775d`).

**Working directory:** `backend/nexus/` for all backend file paths in this plan unless an absolute path is given. All `pytest` / `mypy` / `ruff` invocations assume `cd backend/nexus/` first.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `app/modules/interview_engine/orchestrator/persistence.py` | Modify (docstrings) | Add `_get_client()` reuse contract to module + class docstrings. |
| `app/modules/interview_runtime/schemas.py` | Modify | Add `job_id: str`, `candidate_id: str` to `SessionConfig`. |
| `app/modules/interview_runtime/service.py` | Modify | Populate the two new fields in `build_session_config`. |
| `app/modules/interview_engine/speech/templates.py` | Create | Engine-scoped `TemplateLoader` instance binding (~10 lines). |
| `app/modules/interview_engine/speech/__init__.py` | Modify | Re-export `template_loader`. |
| `app/modules/interview_engine/orchestrator/flow.py` | Create | Pure functions: `pick_next_question`, `evaluate_exit_condition`. |
| `app/modules/interview_engine/orchestrator/__init__.py` | Modify | Re-export `pick_next_question`, `evaluate_exit_condition`. |
| `app/modules/interview_engine/_phase_b_utterances.py` | Create | Throwaway hardcoded strings + module-import-time safety assertion. |
| `app/modules/interview_engine/structured_agent.py` | Create | `StructuredInterviewAgent(Agent)` class. |
| `app/modules/interview_engine/agent.py` | Modify | Swap `GenericInterviewAgent` → `StructuredInterviewAgent`; flip `preemptive_generation`; wire `LedgerPersistence`; replace `_build_system_prompt` with the inert string. |
| `tests/interview_runtime/test_signal_metadata_plumbing.py` | Modify | Extend with `test_build_session_config_populates_job_and_candidate_ids`. |
| `tests/interview_engine/speech/test_template_loader_binding.py` | Create | 3 cases for the binding. |
| `tests/interview_engine/orchestrator/test_flow.py` | Create | 6 cases for `pick_next_question` + `evaluate_exit_condition`. |
| `tests/interview_engine/test_structured_agent_integration.py` | Create | Happy-path + disconnect + safety-fallback sub-cases (mocked LiveKit transport). |
| `tests/interview_engine/test_say_call_sites.py` | Create | AST invariant scan over `app/modules/interview_engine/`. |

**Files NOT touched:** `event_log/*`, `event_kinds.py`, `app/ai/realtime.py`, `app/ai/config.py`, `app/ai/client.py`, `app/ai/prompts.py`, `tenant_settings/*`, frontend, migrations, Alembic. No new migrations are required.

**Deferred to Phase E:** `orchestrator/question_selection.py`, `orchestrator/time_budget.py`. Reintroduced when Sufficiency Checker provides input data.

---

## Task 1: Document `_get_client()` reuse contract in `LedgerPersistence` (C1)

**Files:**
- Modify: `app/modules/interview_engine/orchestrator/persistence.py` (docstrings only — module + class `__init__`).

- [ ] **Step 1: Update the module docstring.**

Open `app/modules/interview_engine/orchestrator/persistence.py`. Replace the existing module-level docstring (lines 1-56) with the version below. The change adds three lines naming `app.pubsub._get_client()` as the canonical client source.

```python
"""LedgerPersistence — async fire-and-forget Redis writeback.

Maintains a Redis side-copy of `InterviewState` + `SignalLedger` so a
fresh agent process after a candidate reconnect can rehydrate without
re-reading the entire audit envelope.

Persistence model:

* **Best-effort writes.** Redis is a safety net, not the source of
  truth. Every public mutation method returns ``True`` on a successful
  write, ``False`` if Redis was unreachable / errored / timed out.
  The methods do NOT raise — a Redis outage must not break a live
  interview turn. Failures are logged at warning level.
* **Fire-and-forget.** Writes are wrapped in ``asyncio.shield`` so a
  cancelled caller doesn't tear down a half-sent write.
* **Gap detection.** Each successful write stamps the source object's
  ``sequence_number`` into ``self._last_<state|ledger>_seq_persisted``.
  At session close, ``detect_gaps`` compares current in-memory seq vs
  last-persisted-seq. Any non-zero delta means writes were lost in
  flight; the result is logged into the audit envelope so the Report
  Builder can flag the session for review.
* **TTL** (default 6h) ensures abandoned sessions don't leak.

* **Gap-detection scope is the current process lifetime — v1
  limitation.** ``_last_state_seq_persisted`` and
  ``_last_ledger_seq_persisted`` are instance attributes, scoped to
  the LedgerPersistence object that lives in one ``StructuredInterviewAgent``
  process. On agent crash + fresh dispatch (or any cross-process
  rehydrate), the new instance starts with both attributes = ``None``;
  ``detect_gaps`` at the new agent's session-close will report "all
  of current_seq missing" because the new instance never wrote
  anything itself, even though the OLD process had successfully
  written most updates before crashing. This is acceptable for v1
  because crash-resume (the v2 capability that would make this a
  real bug) is explicitly out of scope per design doc §9.3 and
  §16. Cross-process gap tracking would require persisting the
  last-persisted-seq somewhere durable (Postgres or a Redis
  metadata key) and reading it on rehydrate — deferred to v2
  alongside hot-resume from Redis state. Until then, gap detection
  is a single-process correctness tool, not a crash-recovery audit.

Redis client source — process-level memoized singleton from
``app.pubsub._get_client()``. Construct ``LedgerPersistence`` per
session passing the shared client; do NOT build a fresh
``aioredis.Redis`` per ``LedgerPersistence`` instance. The pubsub
module owns the connection lifecycle. The leading-underscore on
``_get_client`` is acknowledged; if a third consumer appears we
promote it to ``get_redis_client`` in a separate small commit.

Key layout (tenant-scoped — `nexus_app` role enforces no cross-tenant
key access at the Redis ACL level when that lands; today the prefix
is the only fence, sufficient because nothing else writes these keys):

```
tenant:{tenant_id}:session:{session_id}:state
tenant:{tenant_id}:session:{session_id}:ledger
```

The structured agent (Phase B) calls ``write_state`` on every phase
change and ``write_ledger`` on every ledger mutation. Both calls are
``await``-ed but neither blocks the turn — a slow Redis adds latency
proportional to its slowness, capped by ``socket_timeout`` on the
client; a hard failure fails-fast and logs.
"""
```

- [ ] **Step 2: Update `LedgerPersistence.__init__` docstring.**

Find `class LedgerPersistence:` and add the docstring directly under the class signature. Replace the existing one-liner `"""Tenant-scoped Redis writeback for one in-flight session."""` with:

```python
class LedgerPersistence:
    """Tenant-scoped Redis writeback for one in-flight session.

    Construct one per session, passing the process-level memoized
    ``aioredis.Redis`` client returned by ``app.pubsub._get_client()``.
    Do not construct a fresh client per session — the pubsub module
    owns connection lifecycle and pooling.
    """
```

- [ ] **Step 3: Verify mypy + ruff still clean.**

Run from `backend/nexus/`:

```bash
docker compose run --rm nexus mypy --strict app/modules/interview_engine/orchestrator/persistence.py
docker compose run --rm nexus ruff check app/modules/interview_engine/orchestrator/persistence.py
```

Expected: both clean (docstring-only changes don't affect types or linting).

- [ ] **Step 4: Verify existing persistence tests still green.**

```bash
docker compose run --rm nexus pytest tests/interview_engine/orchestrator/test_persistence.py -v
```

Expected: PASS (docstring-only changes; no runtime behavior touched).

- [ ] **Step 5: Commit.**

```bash
git add app/modules/interview_engine/orchestrator/persistence.py
git commit -m "$(cat <<'EOF'
docs(interview_engine): document _get_client() reuse contract in LedgerPersistence

Module docstring + class docstring now name app.pubsub._get_client()
as the canonical client source. Phase A close-out carryforward Flag 5;
LedgerPersistence is the second legitimate consumer of that pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Plumb `job_id` and `candidate_id` into `SessionConfig` (C2)

**Files:**
- Modify: `app/modules/interview_runtime/schemas.py:150-178` — add two fields to `SessionConfig`.
- Modify: `app/modules/interview_runtime/service.py:~178` — populate the new fields in `build_session_config`.
- Modify: `tests/interview_runtime/test_signal_metadata_plumbing.py` — add one new test case (extend existing file; do NOT create a new file).

- [ ] **Step 1: Write the failing test.**

Open `tests/interview_runtime/test_signal_metadata_plumbing.py`. Add this test at the bottom of the file. The test reuses the existing fixtures that build a session row via `make_assignment_with_stage`:

```python
async def test_build_session_config_populates_job_and_candidate_ids(
    db,
    make_assignment_with_stage,
):
    """A.1 part 2 — `build_session_config` projects `job_id` and
    `candidate_id` from the walked rows so `InterviewState` can be
    constructed with required identity fields.
    """
    from app.modules.interview_runtime.service import build_session_config

    setup = await make_assignment_with_stage(db)
    config = await build_session_config(
        db,
        session_id=setup.session_id,
        tenant_id=setup.tenant_id,
    )

    assert config.job_id == str(setup.job_id), (
        f"expected job_id={setup.job_id!s}, got {config.job_id!r}"
    )
    assert config.candidate_id == str(setup.candidate_id), (
        f"expected candidate_id={setup.candidate_id!s}, got {config.candidate_id!r}"
    )
```

If `make_assignment_with_stage`'s return shape doesn't expose `job_id` / `candidate_id` directly, look up the existing fixture in `tests/conftest.py` and use whatever attribute it surfaces (e.g., `setup.assignment.job_posting_id`, `setup.assignment.candidate_id`). The assertion shape stays the same — what matters is comparing the projected `config.job_id` / `config.candidate_id` against the source-of-truth row IDs.

- [ ] **Step 2: Run the test, verify it fails.**

```bash
docker compose run --rm nexus pytest tests/interview_runtime/test_signal_metadata_plumbing.py::test_build_session_config_populates_job_and_candidate_ids -v
```

Expected: FAIL with `AttributeError: 'SessionConfig' object has no attribute 'job_id'` (Pydantic raises on field access for an undefined attribute).

- [ ] **Step 3: Add the two fields to `SessionConfig` in `schemas.py`.**

Open `app/modules/interview_runtime/schemas.py`. Find `class SessionConfig(BaseModel):` (line ~150). Add `job_id` and `candidate_id` fields directly under `session_id`:

```python
class SessionConfig(BaseModel):
    """The full input contract sent from Nexus to the interview engine.

    In standalone mode this is loaded from a fixture JSON file.
    In integration mode this arrives via /api/internal/sessions/{id}/config.
    """

    session_id: str
    job_id: str = Field(
        min_length=1,
        description=(
            "UUID-as-string of the source JobPosting. Required by the "
            "structured agent's InterviewState identity fields (state.py "
            "wire-format invariant). Populated by build_session_config."
        ),
    )
    candidate_id: str = Field(
        min_length=1,
        description=(
            "UUID-as-string of the candidate. Required by the structured "
            "agent's InterviewState identity fields (state.py wire-format "
            "invariant). Populated by build_session_config."
        ),
    )
    job_title: str
    role_summary: str
    seniority_level: str
    company: CompanyContext
    candidate: CandidateContext
    stage: StageConfig
    signals: list[str] = Field(
        default_factory=list,
        description="Top-level signal dimensions the evaluator cares about.",
    )
    signal_metadata: list[SignalMetadata] = Field(
        default_factory=list,
        description=(
            "Per-signal metadata (weight, knockout, priority, stage, "
            "evaluation method) projected from the latest confirmed "
            "signal snapshot. One entry per ``signals`` value (same order). "
            "Empty list is the additive default — pre-rebuild engines "
            "ignore it; the structured agent (Phase B+) reads it."
        ),
    )
```

- [ ] **Step 4: Populate the two fields in `service.py::build_session_config`.**

Open `app/modules/interview_runtime/service.py`. Find the `config = SessionConfig(` construction (around line 178). The walker already has `job` (the `JobPosting` row) and `assignment` (the `CandidateJobAssignment` row) in scope from the joins above. Add two lines to the constructor — placement directly under `session_id=str(session_id),`:

```python
    config = SessionConfig(
        session_id=str(session_id),
        job_id=str(job.id),
        candidate_id=str(assignment.candidate_id),
        job_title=job.title,
        role_summary=snapshot.role_summary,
        seniority_level=snapshot.seniority_level,
        company=CompanyContext(
            ...
        ),
        ...
    )
```

(Replace `...` placeholders with the existing field assignments — the construction already has them; only `job_id` and `candidate_id` are new.)

- [ ] **Step 5: Run the new test, verify it passes.**

```bash
docker compose run --rm nexus pytest tests/interview_runtime/test_signal_metadata_plumbing.py::test_build_session_config_populates_job_and_candidate_ids -v
```

Expected: PASS.

- [ ] **Step 6: Run all `interview_runtime` tests, verify no regressions.**

```bash
docker compose run --rm nexus pytest tests/interview_runtime/ -v
```

Expected: PASS (existing tests construct `SessionConfig` only via `build_session_config`, which now populates the new fields).

- [ ] **Step 7: Verify mypy + ruff clean.**

```bash
docker compose run --rm nexus mypy --strict app/modules/interview_runtime/
docker compose run --rm nexus ruff check app/modules/interview_runtime/
```

Expected: both clean.

- [ ] **Step 8: Verify module boundaries test still green.**

```bash
docker compose run --rm nexus pytest tests/test_module_boundaries.py -v
```

Expected: PASS (no boundary changes; we only modified existing public-API symbols).

- [ ] **Step 9: Commit.**

```bash
git add app/modules/interview_runtime/schemas.py app/modules/interview_runtime/service.py tests/interview_runtime/test_signal_metadata_plumbing.py
git commit -m "$(cat <<'EOF'
feat(interview_runtime): plumb job_id and candidate_id into SessionConfig

InterviewState (state.py) requires job_id and candidate_id as required
str fields for Redis wire-format stability. Neither was exposed on
SessionConfig — surfaced when drafting the StructuredInterviewAgent
(Phase B). Resolution: additive Pydantic field extensions populated by
build_session_config from rows already walked during signal-metadata
projection. Same pattern as A.1's signal_metadata addition.

No migration, no schema break, no new query.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Bind engine-scoped `TemplateLoader` instance (C3)

**Files:**
- Create: `app/modules/interview_engine/speech/templates.py`.
- Modify: `app/modules/interview_engine/speech/__init__.py` — re-export `template_loader`.
- Create: `tests/interview_engine/speech/test_template_loader_binding.py`.

- [ ] **Step 1: Write the failing test.**

Create `tests/interview_engine/speech/test_template_loader_binding.py` with three cases:

```python
"""Tests for the engine-scoped TemplateLoader binding (Phase A tail).

The TemplateLoader class itself lives in app/ai/prompts.py and has its
own coverage; this file only validates the engine-side binding (correct
base path, dev/prod reload flag derived from settings.environment).
"""

import pytest

from app.modules.interview_engine.speech.templates import (
    ENGINE_PROMPTS_DIR,
    template_loader,
)


def test_loads_intro_v1():
    """The binding loads intro.v1.txt from the engine prompts dir."""
    body = template_loader.get("speech_agent", "intro", "v1")
    assert body, "intro.v1.txt body should be non-empty"
    # Sanity: known-content from the prompt file
    assert "interviewer" in body.lower() or "screener" in body.lower()


def test_missing_version_raises_file_not_found():
    """A missing template version raises FileNotFoundError loudly."""
    with pytest.raises(FileNotFoundError):
        template_loader.get("speech_agent", "intro", "v999")


def test_engine_prompts_dir_points_at_correct_directory():
    """ENGINE_PROMPTS_DIR is the engine's own prompts dir, not the
    repo-root prompts/ used by PromptLoader."""
    # The binding sits at app/modules/interview_engine/speech/templates.py;
    # ENGINE_PROMPTS_DIR == .../app/modules/interview_engine/prompts
    assert ENGINE_PROMPTS_DIR.name == "prompts"
    assert ENGINE_PROMPTS_DIR.parent.name == "interview_engine"
    # And the speech_agent subdir exists with the three filled v1 templates
    speech_agent_dir = ENGINE_PROMPTS_DIR / "speech_agent"
    assert speech_agent_dir.exists()
    assert (speech_agent_dir / "intro.v1.txt").exists()
    assert (speech_agent_dir / "ask_question_standard.v1.txt").exists()
    assert (speech_agent_dir / "wrap_normal.v1.txt").exists()
```

- [ ] **Step 2: Run the test, verify it fails.**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_template_loader_binding.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.modules.interview_engine.speech.templates'`.

- [ ] **Step 3: Create `app/modules/interview_engine/speech/templates.py`.**

```python
"""Engine-scoped TemplateLoader binding.

Constructs a process-singleton TemplateLoader pointed at
``app/modules/interview_engine/prompts/`` so structured-agent code
can call ``template_loader.get(role, name, version)`` without
re-binding the base path everywhere. The TemplateLoader class itself
lives in ``app.ai.prompts`` (per-template versioning, mtime-based
dev reload, sha256 hash helper, FileNotFoundError on miss).

Dev-mode reload is gated on ``settings.environment == "development"``
so production processes are cache-forever (process restart on deploy
invalidates).
"""
from __future__ import annotations

from pathlib import Path

from app.ai.prompts import TemplateLoader
from app.config import settings

# Repository layout:
# backend/nexus/app/modules/interview_engine/speech/templates.py
# parents[1] == backend/nexus/app/modules/interview_engine
ENGINE_PROMPTS_DIR: Path = Path(__file__).resolve().parents[1] / "prompts"

template_loader: TemplateLoader = TemplateLoader(
    base_path=ENGINE_PROMPTS_DIR,
    reload_on_change=(settings.environment == "development"),
)
```

- [ ] **Step 4: Re-export `template_loader` from `speech/__init__.py`.**

Open `app/modules/interview_engine/speech/__init__.py`. Replace the existing content with:

```python
"""Speech Agent — generates every candidate-facing utterance via a
versioned template and a strict safety gate.

Phase A.5 ships the safety regex; Phase A tail ships the template
loader binding; Phase C wires the LLM-rendering ``SpeechAgent`` class
itself. All three share this module's public API so callers
(``structured_agent.py``) import from one place.
"""
from app.modules.interview_engine.speech.safety import (
    SafetyResult,
    SafetyViolation,
    ViolationCategory,
    check_safety,
)
from app.modules.interview_engine.speech.templates import (
    ENGINE_PROMPTS_DIR,
    template_loader,
)

__all__ = [
    "ENGINE_PROMPTS_DIR",
    "SafetyResult",
    "SafetyViolation",
    "ViolationCategory",
    "check_safety",
    "template_loader",
]
```

- [ ] **Step 5: Run the test, verify it passes.**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_template_loader_binding.py -v
```

Expected: all 3 cases PASS.

- [ ] **Step 6: Verify mypy + ruff + module boundaries clean.**

```bash
docker compose run --rm nexus mypy --strict app/modules/interview_engine/speech/
docker compose run --rm nexus ruff check app/modules/interview_engine/speech/
docker compose run --rm nexus pytest tests/test_module_boundaries.py -v
```

Expected: all clean / PASS.

- [ ] **Step 7: Commit.**

```bash
git add app/modules/interview_engine/speech/templates.py app/modules/interview_engine/speech/__init__.py tests/interview_engine/speech/test_template_loader_binding.py
git commit -m "$(cat <<'EOF'
feat(interview_engine): bind engine-scoped TemplateLoader instance

Adds app/modules/interview_engine/speech/templates.py — a 10-line
binding constructing TemplateLoader pointed at the engine's own
prompts dir, with reload_on_change gated on settings.environment.
Re-exports template_loader through speech/__init__.py.

The TemplateLoader class itself lives in app/ai/prompts.py (Phase A
shipped); this is the engine-scoped instance Phase C will consume.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `orchestrator/flow.py` for Phase B linear progression (C4)

**Files:**
- Create: `app/modules/interview_engine/orchestrator/flow.py`.
- Modify: `app/modules/interview_engine/orchestrator/__init__.py` — re-export the two functions.

This task ships flow.py without tests; tests follow as a separate atomic commit (Task 7) per the spec's commit shape.

- [ ] **Step 1: Create `flow.py`.**

```python
"""Pure orchestration-flow functions for Phase B linear progression.

No I/O, no LLMs, no DB. The structured agent (Phase B) calls these on
every candidate-turn-resolution boundary to decide what to do next.

Phase B scope:
* `pick_next_question` walks `config.stage.questions` in position order
  and returns the first one not yet completed (no ``QuestionState`` for
  it, OR ``QuestionState.completed_at`` is None).
* `evaluate_exit_condition` returns `ExitMode.COMPLETED` exactly when
  `pick_next_question` is None; otherwise returns None.

Phase E reintroduces a richer `pick_next_question` (priority + mandatory-
first + knockout-first within mandatory) once the SignalLedger drives
selection. The Phase-B implementation here remains valid as the no-
signal-data fallback.
"""
from __future__ import annotations

from app.modules.interview_engine.orchestrator.state import (
    ExitMode,
    InterviewState,
    QuestionState,
)
from app.modules.interview_runtime import QuestionConfig, SessionConfig


def pick_next_question(
    state: InterviewState,
    config: SessionConfig,
) -> QuestionConfig | None:
    """Return the next QuestionConfig to ask, or None if all are done.

    Phase B: walks ``config.stage.questions`` in position order. A
    question is considered "done" when there is a corresponding
    ``QuestionState`` in ``state.questions`` with a non-None
    ``completed_at``. The first question with no QuestionState (asked
    yet) — or with QuestionState but ``completed_at is None`` (in
    progress) — is returned.

    Returns None when every question has a QuestionState with
    completed_at set, OR when ``config.stage.questions`` is empty.
    """
    completed_ids: set[str] = {
        qs.question_id
        for qs in state.questions
        if qs.completed_at is not None
    }
    for qc in config.stage.questions:
        if qc.id not in completed_ids:
            return qc
    return None


def evaluate_exit_condition(
    state: InterviewState,
    config: SessionConfig,
) -> ExitMode | None:
    """Return ExitMode.COMPLETED iff every question is done; else None.

    Phase B: COMPLETED is the only exit condition this function detects.
    The orchestrator handles disconnect (TECHNICAL_FAILURE) and
    candidate-initiated exit (CANDIDATE_INITIATED_EXIT) paths
    separately because those are driven by external events, not flow
    state. Phase H/I will add more conditions here as those paths land.
    """
    if pick_next_question(state, config) is None:
        return ExitMode.COMPLETED
    return None


__all__ = ["evaluate_exit_condition", "pick_next_question"]
```

- [ ] **Step 2: Re-export from `orchestrator/__init__.py`.**

Open `app/modules/interview_engine/orchestrator/__init__.py`. Add these two imports + extend `__all__`:

```python
from app.modules.interview_engine.orchestrator.flow import (
    evaluate_exit_condition,
    pick_next_question,
)
```

Add `"evaluate_exit_condition"` and `"pick_next_question"` to the `__all__` list (preserving alphabetical order in the existing list).

- [ ] **Step 3: Verify the import path resolves.**

```bash
docker compose run --rm nexus python -c "from app.modules.interview_engine.orchestrator import pick_next_question, evaluate_exit_condition; print('ok')"
```

Expected: prints `ok` with no traceback.

- [ ] **Step 4: Verify mypy + ruff + module boundaries clean.**

```bash
docker compose run --rm nexus mypy --strict app/modules/interview_engine/orchestrator/flow.py
docker compose run --rm nexus ruff check app/modules/interview_engine/orchestrator/
docker compose run --rm nexus pytest tests/test_module_boundaries.py -v
```

Expected: all clean / PASS.

- [ ] **Step 5: Verify existing orchestrator tests still green.**

```bash
docker compose run --rm nexus pytest tests/interview_engine/orchestrator/ -v
```

Expected: existing `test_ledger.py`, `test_state.py`, `test_persistence.py` PASS. `test_flow.py` does not exist yet.

- [ ] **Step 6: Commit.**

```bash
git add app/modules/interview_engine/orchestrator/flow.py app/modules/interview_engine/orchestrator/__init__.py
git commit -m "$(cat <<'EOF'
feat(interview_engine): add orchestrator/flow.py for Phase B linear progression

Two pure functions: pick_next_question (walks config.stage.questions in
position order) and evaluate_exit_condition (returns COMPLETED when
pick_next_question is None). No I/O, no LLM, no DB.

Phase B scope only — Phase E will reintroduce richer selection once
the SignalLedger drives priority + mandatory-first + knockout-first.
This Phase-B implementation remains valid as the no-signal-data
fallback.

Tests follow in a separate atomic commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add hardcoded-utterance stub for Phase B (C5)

**Files:**
- Create: `app/modules/interview_engine/_phase_b_utterances.py`.

The module-import-time assertion is the only "test" for this commit; it fires at import and prevents the agent from starting if the safety-fallback string ever drifts.

- [ ] **Step 1: Create `_phase_b_utterances.py`.**

```python
"""Throwaway hardcoded utterances for Phase B.

DELETE this file when Phase C ships the LLM-rendered Speech Agent. The
orchestrator's call sites (in structured_agent.py) get repointed to
speech.deliveries.render_<template> at that time. If you are reading this
in Phase C or later, this file should not exist.

Phase B uses these literal strings to exercise the orchestrator's flow
end-to-end before the Speech Agent class is built. The candidate
experience sounds robotic; that is intended. Phase C replaces every call
site with `await speech_agent.render(template, version, inputs)` →
`await session.say(rendered.text)`, with the same single-entry-point
discipline.

Four strings ship in Phase B:
- INTRO with placeholders: {name}, {role}, {minutes}
- ASK_QUESTION_STANDARD with: {question_text}
- WRAP_NORMAL (no placeholders)
- _PHASE_B_SAFETY_FALLBACK_TEXT — load-bearing recovery path used by
  StructuredInterviewAgent._say when an utterance fails check_safety.

All four are designed to pass `speech.safety.check_safety` cleanly. The
fallback's safety is enforced at module import time (see assertion
below) so a regression that would crash the agent in production fails
loudly during boot instead.
"""
from app.modules.interview_engine.speech.safety import check_safety

INTRO = (
    "Hi {name}, I'll be running a short technical screen for the {role} "
    "role today. We'll be about {minutes} minutes. Take your time, and "
    "feel free to ask me to repeat anything. Let's get started."
)

ASK_QUESTION_STANDARD = "Got it. Next question: {question_text}"

WRAP_NORMAL = (
    "That's everything from my side. The recruiting team will be in "
    "touch with next steps."
)

_PHASE_B_SAFETY_FALLBACK_TEXT = (
    "Let me ask you about something else. The recruiting team will "
    "follow up with you."
)

# Module-import-time assertion: a safety regression in the fallback itself
# would otherwise allow the agent to limp on into production. Failing the
# import is the right loudness for a load-bearing recovery path.
assert check_safety(_PHASE_B_SAFETY_FALLBACK_TEXT).is_safe, (
    "_PHASE_B_SAFETY_FALLBACK_TEXT failed check_safety — fix the string "
    "before the agent process is allowed to start."
)
```

- [ ] **Step 2: Verify the import succeeds and assertion holds.**

```bash
docker compose run --rm nexus python -c "from app.modules.interview_engine._phase_b_utterances import INTRO, ASK_QUESTION_STANDARD, WRAP_NORMAL, _PHASE_B_SAFETY_FALLBACK_TEXT; print('all four imports ok')"
```

Expected: prints `all four imports ok` (the assertion runs during import; a failure would raise `AssertionError` immediately).

- [ ] **Step 3: Verify all four strings actually pass `check_safety`.**

```bash
docker compose run --rm nexus python -c "
from app.modules.interview_engine.speech.safety import check_safety
from app.modules.interview_engine._phase_b_utterances import (
    INTRO, ASK_QUESTION_STANDARD, WRAP_NORMAL, _PHASE_B_SAFETY_FALLBACK_TEXT,
)
# INTRO has placeholders; substitute representative values before checking.
intro_rendered = INTRO.format(name='Alex', role='Backend Engineer', minutes=15)
ask_rendered = ASK_QUESTION_STANDARD.format(question_text='Tell me about a project.')
for label, text in [
    ('INTRO', intro_rendered),
    ('ASK_QUESTION_STANDARD', ask_rendered),
    ('WRAP_NORMAL', WRAP_NORMAL),
    ('_PHASE_B_SAFETY_FALLBACK_TEXT', _PHASE_B_SAFETY_FALLBACK_TEXT),
]:
    result = check_safety(text)
    assert result.is_safe, f'{label} failed safety: {result.violations}'
print('all four utterances pass check_safety')
"
```

Expected: prints `all four utterances pass check_safety`.

- [ ] **Step 4: Verify mypy + ruff clean.**

```bash
docker compose run --rm nexus mypy --strict app/modules/interview_engine/_phase_b_utterances.py
docker compose run --rm nexus ruff check app/modules/interview_engine/_phase_b_utterances.py
```

Expected: both clean.

- [ ] **Step 5: Commit.**

```bash
git add app/modules/interview_engine/_phase_b_utterances.py
git commit -m "$(cat <<'EOF'
feat(interview_engine): add hardcoded-utterance stub for Phase B

Throwaway module shipping four strings (INTRO, ASK_QUESTION_STANDARD,
WRAP_NORMAL, _PHASE_B_SAFETY_FALLBACK_TEXT) that the structured agent's
hardcoded-utterance path consumes during Phase B. Module-import-time
assertion verifies the fallback string passes check_safety; a regression
fails the import, preventing the agent from starting.

Top-of-file docstring includes the deletion criterion + tripwire
sentence: "If you are reading this in Phase C or later, this file
should not exist." Phase C's SpeechAgent.render replaces every call
site and this file gets deleted in that diff.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Swap `GenericInterviewAgent` → `StructuredInterviewAgent` end-to-end (C6)

**Files:**
- Create: `app/modules/interview_engine/structured_agent.py`.
- Modify: `app/modules/interview_engine/agent.py` — replace `GenericInterviewAgent` with `StructuredInterviewAgent`; flip `preemptive_generation`; wire `LedgerPersistence`; replace `_build_system_prompt` body.

This is the largest atomic commit in the plan. Squashed per spec §8 to keep no class-without-call-site snapshot in git history. Tests follow in Task 7 + Task 8.

- [ ] **Step 0: Verify LiveKit AgentSession `.on` / `.off` API surface.**

Spec-time verification done at plan-write time and captured here for the executing engineer. Re-run before relying on the API in case the local pinned version drifted:

```bash
docker compose run --rm nexus python -c "
from livekit.agents import AgentSession
import inspect
print('on:', hasattr(AgentSession, 'on'))
print('off:', hasattr(AgentSession, 'off'))
if hasattr(AgentSession, 'off'):
    print('off signature:', inspect.signature(AgentSession.off))
if hasattr(AgentSession, 'on'):
    print('on signature:', inspect.signature(AgentSession.on))
"
```

Expected (verified at plan write 2026-05-05):
```
on: True
off: True
off signature: (self, event: -T_contra, callback: Callable) -> None
on signature: (self, event: 'EventTypes', callback: 'Callable | None' = None) -> 'Callable'
```

If the output diverges from these signatures, halt and amend the plan before writing `_wait_for_final_transcript` (Step 4). The current code in Step 4 assumes `session.on(event, cb)` registers and `session.off(event, cb)` removes — both confirmed.

Also verify the envelope-event constants the new class will import are declared:

```bash
grep -n "SPEECH_FALLBACK_USED\|SPEECH_SAFETY_VIOLATION\|ORCHESTRATOR_PHASE_CHANGED\|ORCHESTRATOR_QUESTION_ASKED\|ORCHESTRATOR_QUESTION_COMPLETED\|ORCHESTRATOR_EXIT\|ORCHESTRATOR_LEDGER_SNAPSHOT\|PERSISTENCE_GAPS_DETECTED" app/modules/interview_engine/event_kinds.py
```

Expected (verified at plan write 2026-05-05): all eight constants are defined (lines 58-71) and re-exported in `ALL_EVENT_KINDS`. If any are missing, halt and either add them to `event_kinds.py` first or update the imports in Step 1 to match the existing names.

- [ ] **Step 1: Create `structured_agent.py` skeleton (imports + class signature + constants).**

```python
"""StructuredInterviewAgent — the deterministic-flow LiveKit Agent.

Phase B drives candidate utterances via:
1. Pattern 2 hard guardrail — `llm_node` is overridden to emit zero
   chunks, fully bypassing the realtime LLM autogen path.
2. The orchestrator's main loop generates each agent utterance from
   `_phase_b_utterances` (hardcoded English strings; throwaway), passes
   through `_say()` for safety + fallback, and calls
   `await self.session.say(text)`.
3. Wait for the next `UserInputTranscribedEvent(final=True)`, treat the
   transcript as substantive, advance.

Phase B scope (no Sufficiency Checker, no Intent Classifier, no Disclaim
Classifier, no follow-ups, no deepening probes, no silence handling
beyond LiveKit's defaults, no reconnect protocol):
* Linear walk through `config.stage.questions` in position order.
* Always `asked_mode="standard"`.
* Three reachable exit modes: `completed` (all questions asked),
  `candidate_disconnected` (participant disconnect handler), `error`
  (unhandled exception). Two wired-but-unreachable modes: `KNOCKOUT_EXIT`
  and `CANDIDATE_INITIATED_EXIT` (their trigger paths land in H / I).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog
from livekit.agents import Agent, AgentSession, UserInputTranscribedEvent

from app.modules.interview_engine._phase_b_utterances import (
    ASK_QUESTION_STANDARD,
    INTRO,
    WRAP_NORMAL,
    _PHASE_B_SAFETY_FALLBACK_TEXT,
)
from app.modules.interview_engine.event_kinds import (
    ORCHESTRATOR_EXIT,
    ORCHESTRATOR_LEDGER_SNAPSHOT,
    ORCHESTRATOR_PHASE_CHANGED,
    ORCHESTRATOR_QUESTION_ASKED,
    ORCHESTRATOR_QUESTION_COMPLETED,
    PERSISTENCE_GAPS_DETECTED,
    SPEECH_FALLBACK_USED,
    SPEECH_SAFETY_VIOLATION,
)
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.orchestrator import (
    ExitMode,
    InterviewPhase,
    InterviewState,
    LedgerPersistence,
    QuestionState,
    SignalLedger,
    evaluate_exit_condition,
    pick_next_question,
)
from app.modules.interview_engine.speech import check_safety
from app.modules.interview_runtime import (
    QuestionConfig,
    QuestionResult,
    SessionConfig,
    SessionResult,
    TranscriptEntry,
)

log = structlog.get_logger("interview-engine.structured-agent")

INERT_SYSTEM_PROMPT = "Wait for explicit instructions. Do not speak unless told."

SessionOutcome = Literal[
    "completed",
    "candidate_ended",
    "candidate_disconnected",
    "error",
]


def _sha256_short(s: str) -> str:
    """First 16 hex chars of sha256(s) — used for safety-violation
    matched_text hashing in the audit envelope."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _wall_ms() -> int:
    return int(time.time() * 1000)


class StructuredInterviewAgent(Agent):
    """Phase B structured interview agent.

    Owns the InterviewState, SignalLedger (no-op-updated in Phase B),
    LedgerPersistence, and the EventCollector reference for envelope
    emission. Inert system prompt + llm_node override + single
    utterance entry point implement the three-layer guardrail from
    spec §3.1.
    """

    def __init__(
        self,
        *,
        config: SessionConfig,
        tenant_id: uuid.UUID,
        correlation_id: str,
        collector: EventCollector,
        persistence: LedgerPersistence,
    ) -> None:
        self._config = config
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._collector = collector
        self._persistence = persistence
        self._envelope_written: bool = False
        self._persisted: bool = False
        self._end_outcome: SessionOutcome | None = None
        self._session_start_monotonic: float = time.monotonic()
        self._main_loop_task: asyncio.Task[None] | None = None

        # Per-question candidate transcripts, keyed by question_id.
        # Built up during the main loop; folded into SessionResult on close.
        self._candidate_transcripts: dict[str, str] = {}

        # Initialize state + ledger from SessionConfig.
        # job_id and candidate_id come from the C2 SessionConfig fields;
        # missing values raise here at construction time — the correct
        # fail-loud boundary for the wire-format identity invariant.
        target_duration_seconds = config.stage.duration_minutes * 60
        self._state = InterviewState(
            session_id=config.session_id,
            tenant_id=str(tenant_id),
            job_id=config.job_id,
            candidate_id=config.candidate_id,
            target_duration_seconds=target_duration_seconds,
            started_at=_now_utc(),
            questions=[
                QuestionState(
                    question_id=q.id,
                    position=q.position,
                    is_mandatory=q.is_mandatory,
                )
                for q in config.stage.questions
            ],
            prompt_versions={
                "speech_agent.intro": "v1",
                "speech_agent.ask_question_standard": "v1",
                "speech_agent.wrap_normal": "v1",
            },
            model_versions={},  # populated in Phase C+ when LLM roles are wired
        )
        self._ledger = SignalLedger.from_metadata(config.signal_metadata)

        super().__init__(instructions=INERT_SYSTEM_PROMPT)

    # ------------------------------------------------------------------
    # Layer 1 — hard guardrail. The realtime LLM autogen path becomes a
    # no-op regardless of system prompt. Verified against
    # livekit-agents>=1.5.4 examples/voice_agents/structured_output.py.
    # ------------------------------------------------------------------
    async def llm_node(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return
        yield  # async-generator contract: yield is unreachable but required

    # ------------------------------------------------------------------
    # Layer 3 — single utterance entry point. AST-invariant test (Task 8)
    # asserts session.say(...) is only called from inside this method.
    # ------------------------------------------------------------------
    async def _say(
        self,
        text: str,
        *,
        allow_interruptions: bool = True,
    ) -> None:
        safety = check_safety(text)
        if not safety.is_safe:
            for v in safety.violations:
                self._collector.append(
                    kind=SPEECH_SAFETY_VIOLATION,
                    payload={
                        "category": v.category,
                        "pattern_name": v.pattern_name,
                        "matched_text_hash": _sha256_short(v.matched_text),
                    },
                    wall_ms=_wall_ms(),
                )
            self._collector.append(
                kind=SPEECH_FALLBACK_USED,
                payload={"reason": "phase_b_hardcoded_safety_violation"},
                wall_ms=_wall_ms(),
            )
            text = _PHASE_B_SAFETY_FALLBACK_TEXT
        await self.session.say(text, allow_interruptions=allow_interruptions)
```

(The class is incomplete; remaining methods land in subsequent steps.)

- [ ] **Step 2: Add the transition + persist helper.**

Append to `structured_agent.py`:

```python
    async def _transition_with_persist(
        self,
        target: InterviewPhase,
        *,
        reason: str,
    ) -> None:
        """Single-entry-point for phase transitions.

        Calls state.transition() (legality check + sequence bump) and
        emits orchestrator.phase_changed envelope event with the old +
        new phase. Persistence write is best-effort and never blocks.
        """
        old_phase = self._state.phase
        self._state.transition(target)
        self._collector.append(
            kind=ORCHESTRATOR_PHASE_CHANGED,
            payload={
                "old_phase": old_phase.value,
                "new_phase": target.value,
                "reason": reason,
            },
            wall_ms=_wall_ms(),
        )
        await self._persistence.write_state(self._state)
```

- [ ] **Step 3: Add `on_enter` (entrypoint hook, launches the main loop).**

Append:

```python
    async def on_enter(self) -> None:
        """LiveKit calls this on session.start(). Launch the main loop
        as a background task so on_enter returns promptly; the loop
        manages the entire interview lifecycle."""
        self._session_start_monotonic = time.monotonic()
        log.info(
            "structured_agent.on_enter",
            session_id=self._config.session_id,
            candidate_name=self._config.candidate.name,
            job_title=self._config.job_title,
            question_count=len(self._config.stage.questions),
        )
        self._main_loop_task = asyncio.create_task(self._run_main_loop())
        # Add a done-callback so a crashing loop produces a logged error
        # rather than an unobserved-task warning.
        self._main_loop_task.add_done_callback(self._on_main_loop_done)

    def _on_main_loop_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            log.info("structured_agent.main_loop.cancelled")
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "structured_agent.main_loop.errored",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._end_outcome = "error"
        else:
            log.info("structured_agent.main_loop.completed")
```

- [ ] **Step 4: Add the wait-for-final-transcript helper.**

Append:

```python
    async def _wait_for_final_transcript(self) -> str:
        """Block until the next UserInputTranscribedEvent(final=True),
        return its transcript text. Phase B treats every utterance as
        substantive (no Intent Classifier — Phase F)."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def _on_user_transcript(ev: UserInputTranscribedEvent) -> None:
            if ev.is_final and not future.done():
                future.set_result(ev.transcript)

        self.session.on("user_input_transcribed", _on_user_transcript)
        try:
            return await future
        finally:
            self.session.off("user_input_transcribed", _on_user_transcript)
```

If the LiveKit version's `AgentSession` does not expose `.off(...)`, the test framework will surface this. The implementation should match whatever the local pinned version of `livekit-agents>=1.5.4` provides for one-shot listener removal — verify before fully relying on `.off`. If `.off` is unavailable, replace with a per-event flag pattern (set a `self._cur_future` field, clear it in the listener after first fire).

- [ ] **Step 5: Add the main orchestration loop.**

Append:

```python
    async def _run_main_loop(self) -> None:
        """The Phase B linear orchestration loop.

        Sequence:
          CONNECTING → CONSENT → INTRO → MAIN_LOOP → NORMAL_WRAP → CLOSED.

        CONSENT is a real state machine step traversed even though the
        candidate already consented in the pre-room wizard; the phase
        exists as a brief audit-recordable acknowledgment (design doc
        §6.1, §4.2 enum comment). Phase F may add behavior here later.
        """
        # 1. Brief CONSENT traversal (wizard already captured consent).
        await self._transition_with_persist(
            InterviewPhase.CONSENT,
            reason="wizard_consent_already_captured",
        )

        # 2. Move into INTRO and play the intro utterance.
        await self._transition_with_persist(
            InterviewPhase.INTRO, reason="intro_phase",
        )
        intro_text = INTRO.format(
            name=self._config.candidate.name.split(" ")[0]
            if self._config.candidate.name else "there",
            role=self._config.job_title,
            minutes=self._config.stage.duration_minutes,
        )
        await self._say(intro_text)

        # 3. Enter MAIN_LOOP and walk the questions.
        await self._transition_with_persist(
            InterviewPhase.MAIN_LOOP, reason="begin_main_loop",
        )
        while True:
            next_q = pick_next_question(self._state, self._config)
            if next_q is None:
                break
            await self._ask_one_question(next_q)

        # 4. Wrap normally.
        await self._transition_with_persist(
            InterviewPhase.NORMAL_WRAP, reason="all_questions_completed",
        )
        await self._say(WRAP_NORMAL)

        # 5. Close.
        await self._transition_with_persist(
            InterviewPhase.CLOSED, reason="normal_close",
        )
        self._state.set_exit_mode(ExitMode.COMPLETED, ended_at=_now_utc())
        self._collector.append(
            kind=ORCHESTRATOR_EXIT,
            payload={
                "exit_mode": ExitMode.COMPLETED.value,
                "reason": "all_questions_completed",
            },
            wall_ms=_wall_ms(),
        )
        # Record the orchestrator's intent — close handler will publish
        # session_outcome=completed.
        self._end_outcome = "completed"

    async def _ask_one_question(self, q: QuestionConfig) -> None:
        """Ask a single question, wait for one transcribed utterance,
        record both into ledger + envelope + state, advance."""
        # Locate the QuestionState for this question (created in __init__).
        qs = next(
            (s for s in self._state.questions if s.question_id == q.id),
            None,
        )
        if qs is None:
            # Defensive — should never happen because __init__ creates
            # one QuestionState per QuestionConfig.
            log.error(
                "structured_agent.question_state.missing",
                question_id=q.id,
            )
            return

        qs.asked_at = _now_utc()
        qs.asked_mode = "standard"
        await self._persistence.write_state(self._state)

        self._collector.append(
            kind=ORCHESTRATOR_QUESTION_ASKED,
            payload={
                "question_id": q.id,
                "position": q.position,
                "mode": "standard",
            },
            wall_ms=_wall_ms(),
        )

        ask_text = ASK_QUESTION_STANDARD.format(question_text=q.text)
        await self._say(ask_text)

        # Wait for the candidate's final transcribed utterance.
        transcript = await self._wait_for_final_transcript()
        self._candidate_transcripts[q.id] = transcript

        qs.completed_at = _now_utc()
        qs.elapsed_seconds = (
            qs.completed_at - qs.asked_at
        ).total_seconds() if qs.asked_at else 0.0

        await self._persistence.write_ledger(self._ledger)

        self._collector.append(
            kind=ORCHESTRATOR_QUESTION_COMPLETED,
            payload={
                "question_id": q.id,
                "elapsed_seconds": qs.elapsed_seconds,
                "followups_asked": qs.followups_asked,
                # Phase B omits coverage_at_close; added in Phase D when
                # Sufficiency Checker provides it.
            },
            wall_ms=_wall_ms(),
        )
```

- [ ] **Step 6: Add SessionResult builder + close-path helpers (parallel to existing `agent.py` patterns).**

Append:

```python
    def _build_session_result(self, outcome: SessionOutcome) -> SessionResult:
        """Compose the SessionResult from accumulated per-question state."""
        question_results: list[QuestionResult] = []
        full_transcript: list[TranscriptEntry] = []

        questions_asked = 0
        questions_skipped = 0

        # Compute timestamp_ms relative to session start for transcript entries.
        def _ts_ms_for(asked_at: datetime | None) -> int:
            if asked_at is None:
                return 0
            return int((asked_at.timestamp() - self._state.started_at.timestamp()) * 1000)

        for q in self._config.stage.questions:
            qs = next(
                (s for s in self._state.questions if s.question_id == q.id),
                None,
            )
            asked = qs is not None and qs.asked_at is not None
            transcript_text = self._candidate_transcripts.get(q.id)

            entries: list[TranscriptEntry] = []
            if asked and transcript_text is not None:
                entries.append(
                    TranscriptEntry(
                        role="candidate",
                        text=transcript_text,
                        timestamp_ms=_ts_ms_for(qs.asked_at if qs else None),
                        question_id=q.id,
                    )
                )
                full_transcript.extend(entries)

            was_skipped = not asked
            if was_skipped:
                questions_skipped += 1
            else:
                questions_asked += 1

            question_results.append(
                QuestionResult(
                    question_id=q.id,
                    question_text=q.text,
                    position=q.position,
                    is_mandatory=q.is_mandatory,
                    was_skipped=was_skipped,
                    probes_fired=0,
                    observations=[],
                    transcript_entries=entries,
                )
            )

        return SessionResult(
            session_id=self._config.session_id,
            job_title=self._config.job_title,
            stage_id=self._config.stage.stage_id,
            stage_type=self._config.stage.stage_type,
            candidate_name=self._config.candidate.name,
            duration_seconds=time.monotonic() - self._session_start_monotonic,
            questions_asked=questions_asked,
            questions_skipped=questions_skipped,
            total_probes_fired=0,
            question_results=question_results,
            full_transcript=full_transcript,
            completed_at=_now_utc().isoformat(),
            knockout_failures=[],
        )

    def get_state(self) -> InterviewState:
        """Read-only access for the close handler in agent.py."""
        return self._state

    def get_ledger(self) -> SignalLedger:
        """Read-only access for the close handler in agent.py."""
        return self._ledger

    def get_persistence(self) -> LedgerPersistence:
        """Read-only access for the close handler in agent.py."""
        return self._persistence
```

- [ ] **Step 7: Add persistence + DB helpers parallel to GenericInterviewAgent.**

Append:

```python
    async def _persist_session_result(self, outcome: SessionOutcome) -> None:
        """Atomic SessionResult persistence (parallel to GenericInterviewAgent)."""
        if self._persisted:
            return
        from app.database import get_bypass_session  # lazy import to mirror agent.py
        from app.modules.interview_runtime import record_session_result

        result = self._build_session_result(outcome)
        async with get_bypass_session() as db:
            await record_session_result(
                db,
                session_id=uuid.UUID(self._config.session_id),
                tenant_id=self._tenant_id,
                result=result,
                correlation_id=self._correlation_id,
            )
            await db.commit()
        self._persisted = True
        log.info(
            "structured_agent.result.persisted",
            session_id=self._config.session_id,
            outcome=outcome,
        )

    async def _publish_session_outcome(self, outcome: SessionOutcome) -> None:
        try:
            room = self.session.room_io.room
            await room.local_participant.set_attributes(
                {"session_outcome": outcome},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "structured_agent.outcome.publish_failed",
                outcome=outcome,
                error=str(exc),
            )

    async def _finalize_event_log(
        self, *, reason: str, sink, # type: ignore[no-untyped-def]
    ) -> None:
        if self._envelope_written or sink is None:
            return
        self._envelope_written = True
        closed_at = _now_utc().isoformat().replace("+00:00", "Z")
        try:
            envelope = self._collector.close(closed_at=closed_at)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "structured_agent.event_log.envelope_validation_failed",
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        try:
            target = await asyncio.to_thread(sink.write, envelope)
            log.info(
                "structured_agent.event_log.written", reason=reason, target=target,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "structured_agent.event_log.sink_write_failed",
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
```

- [ ] **Step 8: Modify `agent.py` to use `StructuredInterviewAgent`.**

Open `app/modules/interview_engine/agent.py`. Make the following changes:

(a) **Add import** at the top, with the other engine imports:

```python
from app.modules.interview_engine.structured_agent import StructuredInterviewAgent
from app.modules.interview_engine.orchestrator import (
    ExitMode,
    InterviewPhase,
    LedgerPersistence,
)
# Reusing app.pubsub's process-level memoized Redis client per Phase A
# close-out Flag 5; LedgerPersistence is the second legitimate consumer.
# No separate engine pool needed.
from app.pubsub import _get_client as _get_redis_client
```

(b) **Replace `_build_system_prompt`** (the function body, line ~255). The new version returns the inert prompt unconditionally:

```python
def _build_system_prompt(*, config: SessionConfig, agent_name: str) -> str:
    """Inert system prompt for the structured agent.

    The realtime LLM is fully bypassed by StructuredInterviewAgent.llm_node;
    this prompt is defense in depth (Layer 2 of the three-layer guardrail).
    See spec §3.1.
    """
    _ = config  # unused — kept in signature for symmetry with future phases
    _ = agent_name  # unused — agent_name surfacing returns in Phase C
    return "Wait for explicit instructions. Do not speak unless told."
```

(c) **In `entrypoint(...)`, replace the agent construction** (the `agent = GenericInterviewAgent(...)` block, line ~209):

```python
    persistence = LedgerPersistence(
        client=_get_redis_client(),
        tenant_id=tenant_id_str,
        session_id=session_id,
    )

    agent = StructuredInterviewAgent(
        config=config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
        collector=event_collector,
        persistence=persistence,
    )
```

(d) **Flip `preemptive_generation`** in the `AgentSession(...)` construction (line ~218):

```python
    session = AgentSession(
        stt=build_stt_plugin(),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),
            preemptive_generation={"enabled": False},  # Phase B: structured agent drives every utterance
            endpointing={
                "mode": "dynamic",
                "min_delay": settings.engine_endpointing_min_delay,
                "max_delay": settings.engine_endpointing_max_delay,
            },
            interruption={
                "mode": "vad",
                "min_duration": 0.5,
                "resume_false_interruption": True,
            },
        ),
    )
```

(e) **Update `_handle_close` to drive the final state.transition(CLOSED)** when the orchestrator's main loop didn't reach it (disconnect / error path). Replace the existing `_handle_close` body with the version that does this:

```python
async def _handle_close(
    ev: CloseEvent,
    agent: StructuredInterviewAgent,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Persist the SessionResult, publish session_outcome, write envelope.

    Drives the final state.transition(CLOSED) for paths where the
    orchestrator main loop never reached CLOSED itself (disconnect,
    unhandled exception). Legal direct edges to CLOSED exist from
    every non-terminal phase per state.py::_LEGAL_TRANSITIONS.
    """
    log.info(
        "session.close",
        reason=ev.reason.value,
        has_error=bool(ev.error),
        already_persisted=agent._persisted,
    )

    collector.append(
        kind="session.close",
        payload={
            "reason": ev.reason.value,
            "persisted": agent._persisted,
            "has_error": bool(ev.error),
            "controller_end_outcome": getattr(agent, "_end_outcome", None),
        },
        wall_ms=int(time.time() * 1000),
    )

    if ev.reason == CloseReason.ERROR:
        outcome: SessionOutcome = "error"
    elif agent._end_outcome is not None:
        outcome = agent._end_outcome
    else:
        outcome = "candidate_disconnected"

    # Drive the final state-machine transition if the main loop didn't.
    state = agent.get_state()
    if state.phase != InterviewPhase.CLOSED:
        # Capture old_phase BEFORE state.transition() — afterward
        # state.phase is already CLOSED.
        old_phase_value = state.phase.value
        try:
            state.transition(InterviewPhase.CLOSED)
            collector.append(
                kind="orchestrator.phase_changed",
                payload={
                    "old_phase": old_phase_value,
                    "new_phase": "closed",
                    "reason": f"close_handler_{outcome}",
                },
                wall_ms=int(time.time() * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.transition_failed",
                error=str(exc),
                current_phase=state.phase.value,
            )

        exit_mode = (
            ExitMode.COMPLETED if outcome == "completed"
            else ExitMode.TECHNICAL_FAILURE
        )
        try:
            state.set_exit_mode(exit_mode, ended_at=datetime.now(timezone.utc))
            collector.append(
                kind="orchestrator.exit",
                payload={
                    "exit_mode": exit_mode.value,
                    "reason": f"close_handler_{outcome}",
                },
                wall_ms=int(time.time() * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "session.close.set_exit_mode_failed",
                error=str(exc),
            )

    # Emit ledger snapshot + persistence-gap detection envelope events.
    ledger = agent.get_ledger()
    collector.append(
        kind="orchestrator.ledger.snapshot",
        payload={
            "signals": [s.model_dump() for s in ledger.signals.values()],
            "sequence_number": ledger.sequence_number,
        },
        wall_ms=int(time.time() * 1000),
    )

    persistence = agent.get_persistence()
    gaps = persistence.detect_gaps(
        current_state_seq=state.sequence_number,
        current_ledger_seq=ledger.sequence_number,
    )
    collector.append(
        kind="persistence.gaps_detected",
        payload=gaps,
        wall_ms=int(time.time() * 1000),
    )

    if not agent._persisted:
        try:
            await agent._persist_session_result(outcome)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    await agent._publish_session_outcome(outcome)
    await agent._finalize_event_log(reason="close_handler", sink=sink)
```

(f) **Remove `class GenericInterviewAgent`** entirely (it's the existing class in `agent.py`, ~line 272 to ~line 401). Also remove the `_wire_close_handler` and `_wire_participant_disconnect` helpers' type hint references to `GenericInterviewAgent` and replace with `StructuredInterviewAgent`.

(g) **Update type hints** on `_wire_close_handler`, `_wire_participant_disconnect`, and `_handle_close` to use `StructuredInterviewAgent` instead of `GenericInterviewAgent`.

- [ ] **Step 9: Defensive grep for `GenericInterviewAgent` references.**

After step 8(f) removes the class, verify no stale references remain in `app/` or `tests/`:

```bash
docker compose run --rm nexus grep -rn "GenericInterviewAgent" app/modules/interview_engine/ tests/
```

Expected: zero matches. Matches in `docs/` are fine (historical references, including the design and impl docs). Any match in `app/` or `tests/` must be cleaned up before committing — otherwise mypy `--strict` will fail on the type-hint reference. Common offenders: type hints in `_wire_close_handler` / `_wire_participant_disconnect` / `_handle_close` signatures.

- [ ] **Step 10: Verify mypy + ruff clean across the engine module.**

```bash
docker compose run --rm nexus mypy --strict app/modules/interview_engine/
docker compose run --rm nexus ruff check app/modules/interview_engine/
```

Expected: both clean. If `--warn-unreachable` is set globally and the `yield` in `llm_node` trips, add a single `# type: ignore[unreachable]` with the wordy rationale comment from spec §3.1.

- [ ] **Step 11: Verify module boundaries + existing tests still green.**

```bash
docker compose run --rm nexus pytest tests/test_module_boundaries.py tests/interview_engine/orchestrator/ tests/interview_engine/speech/ tests/interview_engine/event_log/ -v
```

Expected: PASS (the structural change shouldn't break existing tests; integration tests for the new agent come in Task 7).

- [ ] **Step 12: Commit (atomic squashed commit per spec §8).**

```bash
git add app/modules/interview_engine/structured_agent.py app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(interview_engine): swap GenericInterviewAgent → StructuredInterviewAgent end-to-end

Atomic structural change. Replaces the clean-slate generic LLM harness
with the structured Phase B agent. Three-layer guardrail per spec §3.1:

1. Hard — Agent.llm_node overridden to emit zero chunks (Pattern 2).
2. Defense in depth — system prompt is "Wait for explicit instructions.
   Do not speak unless told." (inert).
3. Single utterance entry point — every session.say(...) is gated by
   StructuredInterviewAgent._say which runs check_safety first and
   falls back to _PHASE_B_SAFETY_FALLBACK_TEXT on violation. AST
   invariant test (next commit) enforces this.

Orchestration loop is linear (Phase B scope per spec §2.1):
CONNECTING→CONSENT→INTRO→MAIN_LOOP→NORMAL_WRAP→CLOSED. Five
phase_changed envelope events on a happy path; CONSENT is real (per
design doc §6.1) not a workaround. Close handler drives the final
transition for disconnect/error paths.

LedgerPersistence wired via app.pubsub._get_client() (Phase A
close-out Flag 5). preemptive_generation flipped to False (kept for
clarity even though llm_node short-circuits). Tests follow in
separate atomic commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Unit tests for `orchestrator/flow.py` (C7)

**Files:**
- Create: `tests/interview_engine/orchestrator/test_flow.py`.

- [ ] **Step 1: Write the failing tests.**

```python
"""Pure-unit tests for orchestrator/flow.py (Phase B linear progression).

No LiveKit, no DB, no LLM. Tests construct InterviewState + SessionConfig
with the minimum fields to exercise pick_next_question and
evaluate_exit_condition.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.interview_engine.orchestrator import (
    ExitMode,
    InterviewState,
    QuestionState,
    evaluate_exit_condition,
    pick_next_question,
)
from app.modules.interview_runtime import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)


def _make_question(qid: str, position: int) -> QuestionConfig:
    """Build a QuestionConfig with the minimum schema-valid fields."""
    return QuestionConfig(
        id=qid,
        position=position,
        text=f"Tell me about your experience with {qid}.",
        signal_values=[f"signal_{qid}"],
        estimated_minutes=2.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=[
            "Names specific tools",
            "Cites concrete outcomes",
            "Describes step-by-step",
        ],
        red_flags=[
            "Vague generalities",
            "No specifics",
        ],
        rubric=QuestionRubric(
            excellent="Names tools and outcomes",
            meets_bar="Describes general approach",
            below_bar="Refuses to engage",
        ),
        evaluation_hint="Look for specificity in tool naming.",
    )


def _make_config(question_ids: list[str]) -> SessionConfig:
    """Build a SessionConfig with N questions in given order."""
    questions = [_make_question(qid, i) for i, qid in enumerate(question_ids)]
    return SessionConfig(
        session_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        job_title="Backend Engineer",
        role_summary="Backend engineering work",
        seniority_level="mid",
        company=CompanyContext(
            about="A test company building test things for testing.",
            industry="testing",
            company_stage="seed",
            hiring_bar="High bar requiring 30+ chars of bar text.",
        ),
        candidate=CandidateContext(name="Test Candidate"),
        stage=StageConfig(
            stage_id="00000000-0000-0000-0000-000000000004",
            stage_type="ai_screening",
            name="Phone Screen",
            duration_minutes=15,
            difficulty="medium",
            questions=questions,
        ),
        signals=[],
        signal_metadata=[],
    )


def _make_state(question_ids: list[str]) -> InterviewState:
    """Build an InterviewState with one QuestionState per question_id."""
    return InterviewState(
        session_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000005",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        target_duration_seconds=900,
        started_at=datetime.now(timezone.utc),
        questions=[
            QuestionState(question_id=qid, position=i, is_mandatory=True)
            for i, qid in enumerate(question_ids)
        ],
    )


def test_pick_next_returns_first_unasked():
    config = _make_config(["q1", "q2", "q3"])
    state = _make_state(["q1", "q2", "q3"])
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "q1"


def test_pick_next_skips_completed():
    config = _make_config(["q1", "q2", "q3"])
    state = _make_state(["q1", "q2", "q3"])
    state.questions[0].completed_at = datetime.now(timezone.utc)
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "q2"


def test_pick_next_returns_none_when_all_complete():
    config = _make_config(["q1", "q2", "q3"])
    state = _make_state(["q1", "q2", "q3"])
    now = datetime.now(timezone.utc)
    for qs in state.questions:
        qs.completed_at = now
    next_q = pick_next_question(state, config)
    assert next_q is None


def test_pick_next_empty_questions_returns_none():
    config = _make_config([])
    state = _make_state([])
    next_q = pick_next_question(state, config)
    assert next_q is None


def test_evaluate_exit_returns_completed_when_pick_next_none():
    config = _make_config(["q1"])
    state = _make_state(["q1"])
    state.questions[0].completed_at = datetime.now(timezone.utc)
    assert evaluate_exit_condition(state, config) == ExitMode.COMPLETED


def test_evaluate_exit_returns_none_during_progress():
    config = _make_config(["q1", "q2"])
    state = _make_state(["q1", "q2"])
    # q1 done, q2 not yet
    state.questions[0].completed_at = datetime.now(timezone.utc)
    assert evaluate_exit_condition(state, config) is None
```

- [ ] **Step 2: Run the tests, verify all PASS.**

```bash
docker compose run --rm nexus pytest tests/interview_engine/orchestrator/test_flow.py -v
```

Expected: 6 cases PASS.

- [ ] **Step 3: Verify mypy + ruff clean.**

```bash
docker compose run --rm nexus mypy --strict tests/interview_engine/orchestrator/test_flow.py
docker compose run --rm nexus ruff check tests/interview_engine/orchestrator/test_flow.py
```

Expected: both clean.

- [ ] **Step 4: Commit.**

```bash
git add tests/interview_engine/orchestrator/test_flow.py
git commit -m "$(cat <<'EOF'
test(interview_engine): unit tests for orchestrator/flow.py

Six pure-unit tests covering pick_next_question (empty list, first
unasked, skip completed, all completed) and evaluate_exit_condition
(COMPLETED when no more questions, None during progress). No LiveKit,
no DB, no LLM.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Integration test for `StructuredInterviewAgent` (C8)

**Files:**
- Create: `tests/interview_engine/test_structured_agent_integration.py`.

This is the largest test surface in the plan. Three sub-cases: happy path, disconnect mid-session, safety-fallback. All use a mocked LiveKit transport (no real room, no real STT/TTS) but the real `StructuredInterviewAgent` class.

- [ ] **Step 1: Write the test scaffolding (mocks + fixtures).**

The integration tests exercise `StructuredInterviewAgent` end-to-end including the close-path envelope events emitted by `agent.py::_handle_close`. Both the happy-path and disconnect tests invoke `_handle_close(...)` directly to cover the full event sequence (`orchestrator.exit` → `orchestrator.ledger.snapshot` → `persistence.gaps_detected` → `session.close`). Spec §5.3 Case A asserts `last event is session.close`; without invoking the close handler the test cannot verify that contract.

```python
"""Integration test for StructuredInterviewAgent.

Mocked LiveKit transport (no real room, no real STT/TTS). The agent
class itself is exercised end-to-end: state machine traversal,
envelope event emission, SessionResult shape, ExitMode → SessionOutcome
mapping. Both happy-path and disconnect tests invoke
agent.py::_handle_close so the full close-path envelope sequence
(ledger.snapshot, gaps_detected, session.close) is covered.

Three sub-cases:
- Happy path: scripted candidate sends N transcribed utterances
  (UserInputTranscribedEvent fires N times); main loop completes;
  close handler runs; SessionResult.exit_mode maps to "completed",
  envelope events match the first/last/multiset contract from spec
  §5.3 Case A — first event is phase_changed CONNECTING→CONSENT,
  last event is session.close.
- Disconnect mid-session: cancel the orchestrator's main-loop task
  after Q1, set _end_outcome="candidate_disconnected", invoke the
  close handler; SessionResult exit_mode maps to "candidate_disconnected".
- Safety-fallback: inject a deliberately-unsafe string into _say(...);
  assert SPEECH_SAFETY_VIOLATION + SPEECH_FALLBACK_USED envelope
  events are emitted; the candidate hears the fallback text.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.agent import _handle_close
from app.modules.interview_engine.event_kinds import (
    ORCHESTRATOR_EXIT,
    ORCHESTRATOR_LEDGER_SNAPSHOT,
    ORCHESTRATOR_PHASE_CHANGED,
    ORCHESTRATOR_QUESTION_ASKED,
    ORCHESTRATOR_QUESTION_COMPLETED,
    PERSISTENCE_GAPS_DETECTED,
    SPEECH_FALLBACK_USED,
    SPEECH_SAFETY_VIOLATION,
)
from app.modules.interview_engine.orchestrator import ExitMode, InterviewPhase
from app.modules.interview_engine.structured_agent import (
    StructuredInterviewAgent,
)
from app.modules.interview_runtime import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)


def _make_session_config(num_questions: int) -> SessionConfig:
    """Build a minimal-but-valid SessionConfig with N questions."""
    questions = [
        QuestionConfig(
            id=f"q{i}",
            position=i,
            text=f"Tell me about challenge number {i}.",
            signal_values=[f"signal_{i}"],
            estimated_minutes=2.0,
            is_mandatory=True,
            follow_ups=[],
            positive_evidence=[
                "Names specific tools",
                "Cites outcomes",
                "Step-by-step",
            ],
            red_flags=["Vague", "No specifics"],
            rubric=QuestionRubric(
                excellent="excellent",
                meets_bar="meets",
                below_bar="below",
            ),
            evaluation_hint="Look for specificity.",
        )
        for i in range(num_questions)
    ]
    return SessionConfig(
        session_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        candidate_id=str(uuid.uuid4()),
        job_title="Backend Engineer",
        role_summary="Backend engineering work",
        seniority_level="mid",
        company=CompanyContext(
            about="A test company building test things for testing here.",
            industry="testing",
            company_stage="seed",
            hiring_bar="High bar text reaching the 20-character minimum.",
        ),
        candidate=CandidateContext(name="Alex Test"),
        stage=StageConfig(
            stage_id=str(uuid.uuid4()),
            stage_type="ai_screening",
            name="Phone Screen",
            duration_minutes=15,
            difficulty="medium",
            questions=questions,
        ),
        signals=[],
        signal_metadata=[],
    )


class _FakeAgentSession:
    """Minimal AgentSession surface used by StructuredInterviewAgent.

    Records every session.say(text) call. Lets the test fire
    UserInputTranscribedEvent listeners on demand.
    """

    def __init__(self):
        self.said_texts: list[str] = []
        self._user_transcript_listeners: list[Callable[[Any], None]] = []
        # Mock room + room_io for set_attributes call from
        # _publish_session_outcome
        self.room_io = MagicMock()
        self.room_io.room.local_participant.set_attributes = AsyncMock()

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        self.said_texts.append(text)

    def on(self, event_name: str, callback: Callable[[Any], None]) -> None:
        if event_name == "user_input_transcribed":
            self._user_transcript_listeners.append(callback)

    def off(self, event_name: str, callback: Callable[[Any], None]) -> None:
        if event_name == "user_input_transcribed":
            try:
                self._user_transcript_listeners.remove(callback)
            except ValueError:
                pass

    def fire_user_transcript(self, transcript: str, *, is_final: bool = True) -> None:
        ev = MagicMock()
        ev.transcript = transcript
        ev.is_final = is_final
        for cb in list(self._user_transcript_listeners):
            cb(ev)


class _RecordingCollector:
    """Stand-in EventCollector that records every appended kind+payload.

    Real EventCollector requires controller_prompt_hash and other
    fields the tests don't need; this minimal recorder satisfies the
    .append(kind=..., payload=..., wall_ms=...) contract.
    """

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def append(self, *, kind: str, payload: dict, wall_ms: int) -> None:
        self.events.append(
            {"kind": kind, "payload": payload, "wall_ms": wall_ms},
        )

    def close(self, *, closed_at: str):
        return MagicMock()  # not exercised in these tests


class _FakePersistence:
    """No-op LedgerPersistence stand-in."""

    def __init__(self):
        self.state_writes: int = 0
        self.ledger_writes: int = 0

    async def write_state(self, state) -> bool:
        self.state_writes += 1
        return True

    async def write_ledger(self, ledger) -> bool:
        self.ledger_writes += 1
        return True

    def detect_gaps(self, *, current_state_seq: int, current_ledger_seq: int) -> dict:
        return {"state_gap": 0, "ledger_gap": 0}


def _make_agent(
    config: SessionConfig,
) -> tuple[StructuredInterviewAgent, _FakeAgentSession, _RecordingCollector, _FakePersistence]:
    """Construct a StructuredInterviewAgent with mocked surfaces."""
    fake_session = _FakeAgentSession()
    collector = _RecordingCollector()
    persistence = _FakePersistence()
    agent = StructuredInterviewAgent(
        config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="test-correlation-id",
        collector=collector,  # type: ignore[arg-type]
        persistence=persistence,  # type: ignore[arg-type]
    )
    # Bind the fake session so _say + on/off + room_io work.
    agent.session = fake_session  # type: ignore[assignment]
    return agent, fake_session, collector, persistence


def _make_close_event(*, is_error: bool = False):
    """Construct a CloseEvent-shaped object for invoking _handle_close.

    The close handler reads ev.reason (compared against CloseReason.ERROR)
    and ev.error (truthy check) and ev.reason.value (string for envelope).
    """
    from livekit.agents.voice.events import CloseReason

    ev = MagicMock()
    if is_error:
        ev.reason = CloseReason.ERROR
        ev.error = RuntimeError("simulated error")
    else:
        # Use any non-ERROR enum value. `CloseReason.USER_INITIATED` /
        # `CloseReason.SESSION_END` etc. — pick whichever exists in the
        # pinned livekit-agents version. The close handler only branches
        # on ERROR vs not-ERROR, so any non-ERROR enum is fine.
        non_error_values = [v for v in CloseReason if v != CloseReason.ERROR]
        assert non_error_values, "expected at least one non-ERROR CloseReason"
        ev.reason = non_error_values[0]
        ev.error = None
    return ev


async def _persist_session_result_noop(self, outcome):  # noqa: ANN001
    """Patch target for _persist_session_result — DB writes are out of
    scope for this integration test."""
    self._persisted = True
```

- [ ] **Step 2: Add the happy-path test.**

Append:

```python
@pytest.mark.asyncio
async def test_happy_path_produces_completed_session_result(monkeypatch):
    """Scripted candidate sends N transcribed utterances; main loop
    completes naturally; close handler runs; envelope events match
    spec §5.3 Case A first/last/multiset contract."""
    n_questions = 3
    config = _make_session_config(n_questions)
    agent, fake_session, collector, persistence = _make_agent(config)

    # Stub out _persist_session_result — DB writes are out of scope.
    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    # Launch on_enter — it kicks off the main loop as a background task.
    await agent.on_enter()

    # Drive the conversation: one transcribed utterance per question.
    for i in range(n_questions):
        # +1 INTRO, +1 ASK_i (count grows by 1 INTRO + 1 ASK per loop iter)
        await _wait_for_say_count(fake_session, expected=i + 2)
        fake_session.fire_user_transcript(
            f"My answer to challenge {i} mentions specific tools and outcomes.",
        )

    # Wait for the main loop task to complete (after the WRAP_NORMAL utterance).
    assert agent._main_loop_task is not None
    await asyncio.wait_for(agent._main_loop_task, timeout=2.0)

    # Phase B has now reached state.phase == CLOSED via the natural main
    # loop completion path. Now invoke the close handler to exercise
    # the close-path envelope events (ledger.snapshot, gaps_detected,
    # session.close).
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    # Build the SessionResult to verify shape (the close handler would
    # have called _persist_session_result; we stubbed it so we build
    # ourselves for assertion).
    result = agent._build_session_result("completed")

    # Question results
    assert len(result.question_results) == n_questions
    for qr in result.question_results:
        assert qr.was_skipped is False
        assert qr.probes_fired == 0
        assert qr.observations == []
        assert len(qr.transcript_entries) == 1

    assert result.questions_asked == n_questions
    assert result.questions_skipped == 0
    assert result.knockout_failures == []

    # Envelope events — first / last / multiset (spec §5.3 Case A).
    kinds = [ev["kind"] for ev in collector.events]

    # First envelope event: phase_changed CONNECTING→CONSENT.
    assert kinds[0] == ORCHESTRATOR_PHASE_CHANGED
    assert collector.events[0]["payload"]["old_phase"] == "connecting"
    assert collector.events[0]["payload"]["new_phase"] == "consent"

    # Last envelope event: session.close (emitted by _handle_close).
    assert kinds[-1] == "session.close"

    # Multiset counts (no order constraint among these):
    assert kinds.count(ORCHESTRATOR_PHASE_CHANGED) == 5
    assert kinds.count(ORCHESTRATOR_QUESTION_ASKED) == n_questions
    assert kinds.count(ORCHESTRATOR_QUESTION_COMPLETED) == n_questions
    assert kinds.count(ORCHESTRATOR_EXIT) == 1
    assert kinds.count(ORCHESTRATOR_LEDGER_SNAPSHOT) == 1
    assert kinds.count(PERSISTENCE_GAPS_DETECTED) == 1

    # Phase progression sanity:
    phase_payloads = [
        ev["payload"] for ev in collector.events
        if ev["kind"] == ORCHESTRATOR_PHASE_CHANGED
    ]
    transitions = [(p["old_phase"], p["new_phase"]) for p in phase_payloads]
    assert transitions == [
        ("connecting", "consent"),
        ("consent", "intro"),
        ("intro", "main_loop"),
        ("main_loop", "normal_wrap"),
        ("normal_wrap", "closed"),
    ]

    # Persistence calls during the main loop:
    #   write_state: 5 transitions + 2 per question (asked_at + completed_at)
    #   write_ledger: 1 per question completion
    assert persistence.state_writes == 5 + 2 * n_questions
    assert persistence.ledger_writes == n_questions

    # Outcome publishing reached the room mock.
    fake_session.room_io.room.local_participant.set_attributes.assert_awaited_with(
        {"session_outcome": "completed"},
    )


async def _wait_for_say_count(
    fake_session: _FakeAgentSession, *, expected: int, timeout: float = 2.0,
) -> None:
    """Spin briefly until len(said_texts) >= expected."""
    deadline = asyncio.get_event_loop().time() + timeout
    while len(fake_session.said_texts) < expected:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(
                f"timed out waiting for say count {expected}; "
                f"got {len(fake_session.said_texts)}",
            )
        await asyncio.sleep(0.01)
```

- [ ] **Step 3: Add the disconnect test.**

Append:

```python
@pytest.mark.asyncio
async def test_disconnect_mid_session_produces_candidate_disconnected(monkeypatch):
    """Scripted candidate answers Q1 then disconnects. Cancel the main
    loop task (test stand-in for LiveKit's participant-timeout-driven
    close); set _end_outcome="candidate_disconnected" (mirroring the
    real participant-disconnected callback); invoke _handle_close;
    assert it drives MAIN_LOOP→CLOSED and emits the full close-path
    envelope sequence."""
    n_questions = 3
    config = _make_session_config(n_questions)
    agent, fake_session, collector, persistence = _make_agent(config)

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    await agent.on_enter()

    # Answer Q0.
    await _wait_for_say_count(fake_session, expected=2)  # INTRO + ASK Q0
    fake_session.fire_user_transcript("My answer to Q0.")

    # Wait for ASK Q1 to be said — confirms Q0 completion was processed.
    await _wait_for_say_count(fake_session, expected=3)  # +ASK Q1

    # Simulate disconnect: state is MAIN_LOOP, the main loop is awaiting
    # Q1's transcript that never arrives. The real _wire_participant_disconnect
    # callback stamps _end_outcome but does NOT cancel the loop or
    # transition phase. The test simulates LiveKit's eventual session-
    # timeout-driven close by cancelling the loop and invoking
    # _handle_close, which is what _wire_close_handler does in production.
    assert agent._state.phase == InterviewPhase.MAIN_LOOP
    agent._end_outcome = "candidate_disconnected"
    assert agent._main_loop_task is not None
    agent._main_loop_task.cancel()
    try:
        await agent._main_loop_task
    except asyncio.CancelledError:
        pass

    # Phase is still MAIN_LOOP — the close handler has not yet run.
    assert agent._state.phase == InterviewPhase.MAIN_LOOP

    # Invoke the close handler. It must drive MAIN_LOOP→CLOSED, set
    # ExitMode.TECHNICAL_FAILURE, emit ledger.snapshot, gaps_detected,
    # and session.close.
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    # Phase is now CLOSED, exit_mode is TECHNICAL_FAILURE.
    assert agent._state.phase == InterviewPhase.CLOSED
    assert agent._state.exit_mode == ExitMode.TECHNICAL_FAILURE

    # SessionResult shape.
    result = agent._build_session_result("candidate_disconnected")
    assert len(result.question_results) == n_questions
    assert result.question_results[0].was_skipped is False
    assert len(result.question_results[0].transcript_entries) == 1
    for qr in result.question_results[1:]:
        assert qr.was_skipped is True
        assert qr.transcript_entries == []
    assert result.questions_asked == 1
    assert result.questions_skipped == n_questions - 1

    # Envelope events: first event is phase_changed CONNECTING→CONSENT,
    # last is session.close. Multiset counts per spec §5.3 Case B.
    kinds = [ev["kind"] for ev in collector.events]
    assert kinds[0] == ORCHESTRATOR_PHASE_CHANGED
    assert collector.events[0]["payload"]["new_phase"] == "consent"
    assert kinds[-1] == "session.close"

    # 4 phase_changed events: CONNECTING→CONSENT, CONSENT→INTRO,
    # INTRO→MAIN_LOOP, MAIN_LOOP→CLOSED (no NORMAL_WRAP — close
    # handler took the direct edge).
    assert kinds.count(ORCHESTRATOR_PHASE_CHANGED) == 4
    assert kinds.count(ORCHESTRATOR_QUESTION_ASKED) == 1
    assert kinds.count(ORCHESTRATOR_QUESTION_COMPLETED) == 1
    assert kinds.count(ORCHESTRATOR_EXIT) == 1
    assert kinds.count(ORCHESTRATOR_LEDGER_SNAPSHOT) == 1
    assert kinds.count(PERSISTENCE_GAPS_DETECTED) == 1

    # The exit-mode payload reflects TECHNICAL_FAILURE.
    exit_evs = [ev for ev in collector.events if ev["kind"] == ORCHESTRATOR_EXIT]
    assert len(exit_evs) == 1
    assert exit_evs[0]["payload"]["exit_mode"] == ExitMode.TECHNICAL_FAILURE.value

    # Outcome publishing reached the room mock with candidate_disconnected.
    fake_session.room_io.room.local_participant.set_attributes.assert_awaited_with(
        {"session_outcome": "candidate_disconnected"},
    )
```

- [ ] **Step 4: Add the safety-fallback test.**

Append:

```python
@pytest.mark.asyncio
async def test_safety_fallback_emits_violation_and_fallback_events():
    """Inject a deliberately-unsafe string into _say(...). Verify both
    SPEECH_SAFETY_VIOLATION and SPEECH_FALLBACK_USED envelope events
    are emitted, and the candidate hears the fallback text instead."""
    config = _make_session_config(num_questions=1)
    agent, fake_session, collector, _persistence = _make_agent(config)

    # Bypass the orchestrator main loop — call _say directly with an
    # unsafe string.
    UNSAFE_TEXT = "Unfortunately you have failed this screen."  # outcome words

    await agent._say(UNSAFE_TEXT)

    # The candidate should have heard the fallback text, not the unsafe one.
    assert len(fake_session.said_texts) == 1
    assert fake_session.said_texts[0] != UNSAFE_TEXT
    from app.modules.interview_engine._phase_b_utterances import (
        _PHASE_B_SAFETY_FALLBACK_TEXT,
    )
    assert fake_session.said_texts[0] == _PHASE_B_SAFETY_FALLBACK_TEXT

    # Envelope events
    kinds = [ev["kind"] for ev in collector.events]
    assert SPEECH_SAFETY_VIOLATION in kinds
    assert SPEECH_FALLBACK_USED in kinds

    # The violation payload carries category + pattern_name + matched_text_hash
    # (NOT raw matched_text).
    violation_evs = [
        ev for ev in collector.events
        if ev["kind"] == SPEECH_SAFETY_VIOLATION
    ]
    assert len(violation_evs) >= 1  # at least one (UNSAFE_TEXT contains 2: unfortunately + failed)
    for ev in violation_evs:
        assert "category" in ev["payload"]
        assert "pattern_name" in ev["payload"]
        assert "matched_text_hash" in ev["payload"]
        assert "matched_text" not in ev["payload"]  # raw text never logged
```

- [ ] **Step 5: Run the integration tests, verify all PASS.**

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_structured_agent_integration.py -v
```

Expected: 3 cases PASS. If `_FakeAgentSession.on/off` doesn't match the actual `AgentSession` event-emitter API, fix the fake to match (the AgentSession has its own emitter — verify by reading `app.ai.realtime.build_*` plugins or the AgentSession source). Update the fake until tests pass.

- [ ] **Step 6: Verify mypy + ruff clean.**

```bash
docker compose run --rm nexus mypy --strict tests/interview_engine/test_structured_agent_integration.py
docker compose run --rm nexus ruff check tests/interview_engine/test_structured_agent_integration.py
```

Expected: both clean. Some `type: ignore` on the mock-injection lines is acceptable (`agent.session = fake_session`).

- [ ] **Step 7: Commit.**

```bash
git add tests/interview_engine/test_structured_agent_integration.py
git commit -m "$(cat <<'EOF'
test(interview_engine): integration test for StructuredInterviewAgent SessionResult shape

Three sub-cases per spec §5.3:
- Happy path: scripted candidate sends N transcribed utterances;
  SessionResult.exit_mode maps to "completed", first envelope event
  is phase_changed CONNECTING→CONSENT, multiset counts of
  phase_changed×5, question_asked×N, question_completed×N, exit×1.
- Disconnect mid-session: cancel main-loop task after Q1; verify
  state.phase is MAIN_LOOP at cancellation; replicate the
  close-handler MAIN_LOOP→CLOSED transition; SessionResult shows
  Q1 non-skipped and Q2+ skipped.
- Safety-fallback: inject unsafe string into _say; verify
  SPEECH_SAFETY_VIOLATION + SPEECH_FALLBACK_USED events emitted;
  candidate hears _PHASE_B_SAFETY_FALLBACK_TEXT instead of the
  unsafe text. Verifies matched_text is hashed not logged raw.

Mocked LiveKit transport (_FakeAgentSession recording session.say
calls and firing UserInputTranscribedEvent on demand). Real
StructuredInterviewAgent class.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: AST invariant on `session.say` single-entry-point (C9)

**Files:**
- Create: `tests/interview_engine/test_say_call_sites.py`.

- [ ] **Step 1: Write the AST-walking invariant test.**

```python
"""AST invariant: every session.say(...) call in app/modules/interview_engine/
must be inside StructuredInterviewAgent._say.

Precedent: tests/test_module_boundaries.py uses the same pattern
(ast.parse + ast.walk + assertion + helpful failure message).

This test catches future regressions where a contributor adds a direct
session.say(...) call that bypasses the safety gate. The structured
agent's three-layer guardrail (spec §3.1) requires this invariant; the
guard is brittle without enforcement.
"""
from __future__ import annotations

import ast
from pathlib import Path

ENGINE_DIR = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "modules"
    / "interview_engine"
)


def _is_session_say_call(node: ast.AST) -> bool:
    """True if node is a Call whose .func is .say on something containing 'session'."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "say":
        return False
    # Resolve the base — could be `session.say`, `self.session.say`,
    # `agent.session.say`, etc. Walk down to the innermost Name/Attribute
    # and check if any segment contains "session".
    base = func.value
    parts: list[str] = []
    while True:
        if isinstance(base, ast.Attribute):
            parts.append(base.attr)
            base = base.value
            continue
        if isinstance(base, ast.Name):
            parts.append(base.id)
            break
        return False
    return any("session" in p.lower() for p in parts)


def _find_say_method_lineno_range(
    tree: ast.Module,
) -> tuple[int, int] | None:
    """Find StructuredInterviewAgent._say's lineno range, if present."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "StructuredInterviewAgent":
            continue
        for item in node.body:
            if isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if item.name == "_say":
                    return item.lineno, item.end_lineno or item.lineno
    return None


def test_session_say_only_called_inside_structured_agent_say():
    violations: list[str] = []

    for py_file in ENGINE_DIR.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            violations.append(f"{py_file}: SyntaxError {exc}")
            continue

        # In structured_agent.py, find _say's lineno range so we can allow
        # session.say(...) calls inside it.
        say_range: tuple[int, int] | None = None
        if py_file.name == "structured_agent.py":
            say_range = _find_say_method_lineno_range(tree)

        for node in ast.walk(tree):
            if not _is_session_say_call(node):
                continue
            lineno = getattr(node, "lineno", 0)
            inside_say = (
                say_range is not None
                and say_range[0] <= lineno <= say_range[1]
            )
            if not inside_say:
                # Find enclosing function/class for a helpful error message.
                rel = py_file.relative_to(ENGINE_DIR)
                violations.append(
                    f"{rel}:{lineno} — session.say(...) outside "
                    f"StructuredInterviewAgent._say. Every utterance must "
                    f"go through _say so check_safety can gate it."
                )

    assert not violations, (
        "AST invariant failed: session.say(...) must be the sole "
        "responsibility of StructuredInterviewAgent._say.\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
```

- [ ] **Step 2: Run the test, verify it PASSES (no current violations).**

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_say_call_sites.py -v
```

Expected: PASS. (If it fails listing violations, the implementation in Task 6 has a `session.say(...)` outside `_say` — fix the offending site rather than relaxing the test.)

- [ ] **Step 3: Negative-control sanity (REQUIRED).**

Verifying that an invariant test actually catches what it claims to catch is the cheapest insurance available; ~30 seconds. Skip this step and the next contributor who adds `session.say(...)` outside `_say` could ship the regression unnoticed if the test was subtly broken.

In `app/modules/interview_engine/structured_agent.py`, temporarily add a `await self.session.say("test")` line inside `_run_main_loop` (NOT inside `_ask_one_question` and NOT inside `_say`). For example, add it as the first line inside the `while True:` loop body. Then run:

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_say_call_sites.py -v
```

Expected: FAIL. The failure message must name `structured_agent.py:<line>` (the line of the temporary call) and the explanation `"session.say(...) outside StructuredInterviewAgent._say"`. Verify the message identifies the exact line.

Then revert the temporary edit (`git checkout -- app/modules/interview_engine/structured_agent.py`) and re-run the test:

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_say_call_sites.py -v
```

Expected: PASS. Both directions verified.

- [ ] **Step 4: Verify mypy + ruff clean.**

```bash
docker compose run --rm nexus mypy --strict tests/interview_engine/test_say_call_sites.py
docker compose run --rm nexus ruff check tests/interview_engine/test_say_call_sites.py
```

Expected: both clean.

- [ ] **Step 5: Commit.**

```bash
git add tests/interview_engine/test_say_call_sites.py
git commit -m "$(cat <<'EOF'
test(interview_engine): AST invariant on session.say single-entry-point

Walks every .py under app/modules/interview_engine/, asserts every
session.say(...) call falls inside StructuredInterviewAgent._say's
lineno range. Catches future regressions where a contributor adds a
direct session.say(...) bypassing the check_safety gate.

Same shape as tests/test_module_boundaries.py: ast.parse + ast.walk +
helpful error message naming the offending file:line.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Smoke Gate (not a commit)

After C9 lands, perform manual smoke testing in a real LiveKit session before authoring the close-out commit. Local LiveKit and Supabase are running per the user's environment.

**Scenario A — Happy path:**

- [ ] Start the engine + nexus stack: `docker compose up --build`.
- [ ] Trigger a real interview via the candidate frontend (or via a scripted dispatch if available). You play the candidate.
- [ ] Verify: agent speaks the intro (the hardcoded INTRO string with your name and the role). It walks through every question in `stage.questions`. After your last answer, it plays the WRAP_NORMAL string and disconnects cleanly.
- [ ] Postgres: `SELECT session_outcome, knockout_failures, transcript FROM sessions WHERE id = '<session_id>'` — `session_outcome='completed'`, `knockout_failures=[]`, transcript reflects the run.
- [ ] Audit envelope: open `engine-events/<session_id>.json` — first event is `orchestrator.phase_changed` with `old_phase=connecting, new_phase=consent`; last event is `session.close`; the multiset counts match spec §5.3 Case A (`phase_changed×5`, `question_asked×N`, `question_completed×N`, `exit×1`, `ledger.snapshot×1`, `gaps_detected×1`).
- [ ] Frontend: candidate UI reads `session_outcome="completed"`.

**Scenario B — Disconnect mid-session:**

- [ ] Same setup. After Q1 or Q2, refresh the browser tab to disconnect.
- [ ] Verify: Postgres `session_outcome='candidate_disconnected'`, partial transcript, partial `question_results` (early questions non-skipped, later questions skipped).
- [ ] Audit envelope: contains a `participant_disconnected` event; phase_changed multiset includes `MAIN_LOOP→CLOSED` (no NORMAL_WRAP); `exit_mode=technical_failure`.
- [ ] Frontend: candidate UI reads `session_outcome="candidate_disconnected"`.

**Fix commits (zero or more):**

If the smoke gate surfaces issues, ship surgical `fix(interview_engine): <description>` commits. Each is small and atomic. The close-out commit lands AFTER all fix commits.

---

## Close-out Commit (final)

After both smoke gate scenarios pass cleanly (with any required fix commits already landed), author the close-out commit.

- [ ] **Step 1: Author the close-out commit.**

```bash
# No file changes; -m only — but if you want to add a docs note, edit
# docs/ai-screening-agent/ai-screening-agent-implementation.md to mark
# Phases A-tail and B as ✅ shipped, then include in the commit.

git commit --allow-empty -m "$(cat <<'EOF'
docs(ai-screening): Phase A finalize + Phase B close-out — StructuredInterviewAgent shipped

Phase A tail and Phase B ship in commits C1 through C9 (renumbered after
the C2 spec amendment for SessionConfig.job_id/candidate_id plumbing).
Both manual smoke gates passed (happy path + disconnect mid-session).

Carryforward (must surface in subsequent phases):

1. Files deferred to Phase E:
   - orchestrator/question_selection.py (priority + mandatory-first +
     knockout-first selection — needs SignalLedger data)
   - orchestrator/time_budget.py (compression + extension triggers —
     needs Sufficiency Checker data)
   Both reintroduced when Sufficiency Checker provides input data.

2. _phase_b_utterances.py deletion gate: the Phase C diff that
   introduces speech/agent.py + speech/deliveries.py MUST delete
   _phase_b_utterances.py. The tripwire docstring inside the file
   communicates this to future readers.

3. Open product/legal item from design doc §8.1: "Candidate asks if
   it's an AI?" transparency policy. Phase B doesn't need a decision;
   Phase F (Intent Classifier) does — surfaces as a TODO in the F
   spec when that work begins.

4. llm_node Form A held cleanly under mypy --strict + ruff check
   without silencer. [If a `# type: ignore[<code>]` was needed,
   replace this bullet with which code was triggered and why the
   silence is targeted.]

5. Smoke-gate observations: [populate with real-LiveKit findings —
   any rough edges that did not justify a fix commit but are worth
   flagging. Examples: TTS latency on hardcoded strings (Phase C's
   pre-buffering helps), audio pipeline metrics, audio.pipeline.error
   envelope events seen.]

6. Close-path logic consolidation: agent.py::_handle_close and
   structured_agent.py's close-related helpers (_persist_session_result,
   _publish_session_outcome, _finalize_event_log) form a parallel
   implementation that's tolerable for v1 but creates a two-place sync
   requirement. Phase J's hardening pass should consolidate these into
   a single owner — likely the structured agent itself owns the full
   close lifecycle, and agent.py's _handle_close shrinks to a thin
   adapter that constructs the close-event mock and calls
   agent.handle_close(ev). Surfaced during Phase B integration test
   authoring (Task 8 had to import _handle_close to exercise the
   close-path envelope events).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Use `--allow-empty` if no docs file is being modified. Otherwise, edit `docs/ai-screening-agent/ai-screening-agent-implementation.md` to mark Phases A-tail + B as shipped, `git add` the file, drop `--allow-empty`.)

---

## Definition of Done

Phase A tail + Phase B is complete when ALL of:

- [ ] C1–C9 + smoke gate + close-out commit have landed in `main`.
- [ ] All Phase A tail tests green.
- [ ] All Phase B tests green (unit, integration, AST invariant).
- [ ] All previously-green tests still green.
- [ ] `mypy --strict` clean across `app/modules/interview_engine/` and `app/modules/interview_runtime/`.
- [ ] `ruff check` clean on the same paths.
- [ ] `tests/test_module_boundaries.py` green.
- [ ] Both smoke gate scenarios passed in a real LiveKit session, observed by the user.
- [ ] Postgres `sessions.session_outcome` reads `"completed"` after happy-path smoke; reads `"candidate_disconnected"` after disconnect smoke.
- [ ] Audit envelope on disk matches the spec §5.3 first/last/multiset shape.
- [ ] Close-out commit body lists all five carryforward items, including any smoke-gate findings.

The next session can begin Phase C (LLM-rendered Speech Agent) from a clean slate: orchestrator + state machine running real LiveKit interviews end-to-end with deterministic flow, awaiting only LLM-rendered utterances to replace the throwaway `_phase_b_utterances.py`.
