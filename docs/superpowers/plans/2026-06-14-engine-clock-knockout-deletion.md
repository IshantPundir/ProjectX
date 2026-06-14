# Interview Engine Clock + Knockout Deletion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the interview engine's session-clock/time-budget subsystem (and the now-dead resolver tier/overflow + mandatory-first machinery) and the knockout early-close subsystem full-vertical, leaving zero dead/stale code and a green suite.

**Architecture:** Two phased sweeps on branch `feat/engine-clock-knockout-deletion`. Phase 1A is engine-local (clock + resolver-tier), ending at a green talk-testable checkpoint. Phase 1B is the knockout full-vertical (engine → durable `SessionEvidence` contract → reporting → `tenant_settings` → DB migration `0059`). Each task keeps the suite green and commits.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, Pydantic v2, Alembic, Dramatiq, pytest, Docker Compose. LLM-tier code is plain Python (livekit-free) except `agent.py` (untouched here).

**Spec:** `docs/superpowers/specs/2026-06-14-interview-engine-clock-knockout-deletion-design.md`

---

## Conventions used in every task

- **Test command (engine):** the container must be up first. NOTE: the engine test
  directory is `tests/interview_engine_v3/` (the module is `interview_engine`, but the
  test dir kept the historical `_v3` suffix). Use `docker compose exec -T` from
  non-interactive callers.
  ```bash
  docker compose up -d nexus
  docker compose exec -T nexus python -m pytest tests/interview_engine_v3 -m "not prompt_quality" -q
  ```
- **Test command (a single file):**
  ```bash
  docker compose exec nexus python -m pytest tests/<path> -q
  ```
- **Line numbers are hints** (they drift as you delete). Locate each symbol by name (`grep -n "<symbol>" <file>`) before editing.
- **"Delete X" means** remove the definition AND every reference, then let the run command prove nothing dangles (ImportError / NameError = a missed reference).
- After **every** code task: run the engine suite, expect PASS, then commit.

---

## Pre-flight

- [ ] **Step 1: Confirm branch + clean tree**

Run:
```bash
git -C /home/ishant/Projects/ProjectX branch --show-current
git -C /home/ishant/Projects/ProjectX status --short
```
Expected: branch `feat/engine-clock-knockout-deletion`, only the spec/plan docs present (clean otherwise).

- [ ] **Step 2: Bring the stack up and capture a green baseline**

Run:
```bash
docker compose up -d nexus
docker compose exec nexus python -m pytest tests/interview_engine_v3 -m "not prompt_quality" -q
```
Expected: PASS (this is the baseline; every task must return to PASS).

---

# PHASE 1A — Clock + resolver-tier deletion

## Task A1: Collapse `resolve_next` + slim `ResolverQuestion`, remove budget plumbing

The clock flows through `contracts → input_builder → driver → brain/service → resolver` as one coupled unit. It must be removed in a single green-keeping change.

**Files:**
- Modify: `app/modules/interview_engine/brain/resolver.py`
- Modify: `app/modules/interview_engine/contracts.py`
- Modify: `app/modules/interview_engine/brain/input_builder.py`
- Modify: `app/modules/interview_engine/brain/service.py`
- Modify: `app/modules/interview_engine/driver.py`
- Test: `tests/interview_engine_v3/test_resolver.py` (rewrite budget cases)

- [ ] **Step 1: Rewrite `resolver.py` — `ResolverQuestion`, `resolve_next`; delete budget pieces**

Replace `ResolverQuestion` (currently lines ~52-66) with:
```python
@dataclass(frozen=True)
class ResolverQuestion:
    """The resolver's compact view of one bank question.

    Tiny by design — everything the resolver needs to pick the next question and
    nothing more. Selection is purely positional now (the time-budget + tier
    scheduler was deleted 2026-06-14).
    """
    question_id: str
    primary_signal: str
    position: int          # absolute ordering within the bank (ascending = earlier)
```

Replace `resolve_next` (currently lines ~87-195) with:
```python
def resolve_next(
    *,
    questions: list[ResolverQuestion],
    asked_ids: set[str],
    preferred_next_signal: str | None = None,
) -> ResolverQuestion | None:
    """Return the next question to ask, or None to CLOSE the session.

    1. Filter to unasked; if none remain → None (full coverage, close).
    2. If the brain emitted a preferred_next_signal that matches an unasked
       question, honor it (naturalness hint).
    3. Otherwise return the lowest-position unasked question.
    """
    unasked = [q for q in questions if q.question_id not in asked_ids]
    if not unasked:
        return None
    if preferred_next_signal is not None:
        pref = next(
            (q for q in unasked if q.primary_signal == preferred_next_signal),
            None,
        )
        if pref is not None:
            return pref
    return min(unasked, key=lambda q: q.position)
```

Delete from `resolver.py`: the `BudgetConfig` dataclass (~38-46), `compute_budget_phase` (~72-80), `budget_config_from_ai_config` (~265-274), and the `from app.modules.interview_engine.contracts import BudgetPhase` import. In `build_question_records` (~202-258) delete `tier=QuestionTier(q.tier)` from both `QuestionRecord(...)` constructions and remove the `QuestionTier` import (keep `QuestionOutcome`, `QuestionRecord`, `ThreadClosure`).

> NOTE: `QuestionRecord.tier` and `QuestionTier` themselves are removed in **Task A3**. In this step just stop *passing* `tier=`. To keep the file importable between A1 and A3, temporarily pass `tier=QuestionTier.core` is NOT allowed (no stale). Instead, do A1 and A3's `QuestionRecord`/`QuestionTier` edits together if your runner can't stay green — see A3 note. (Recommended: complete A1 then A3 back-to-back before running the full suite; commit once green.)

- [ ] **Step 2: `contracts.py` — delete `BudgetPhase`**

Delete the `BudgetPhase` enum (~156-160) and the `budget_phase: BudgetPhase` field on `BrainTurnInput` (~289). Remove `BudgetPhase` from any `__all__`/re-export.

- [ ] **Step 3: `input_builder.py` — drop budget from turn input + render**

In `build_turn_input` (~293-326) remove the `budget_phase: BudgetPhase` parameter and stop passing it to `BrainTurnInput(...)`. In `render_suffix` (~470) delete the `budget_block` construction and remove it from the joined `content` tuple (~499-512). Remove the `BudgetPhase` import.

- [ ] **Step 4: `brain/service.py` — drop time-budget from `decide`/resolver/adapter**

- Remove imports `BudgetConfig`, `budget_config_from_ai_config` (~47-50); keep `ResolverQuestion`, `resolve_next`.
- `ControlPlane.__init__`: delete the `budget_cfg: BudgetConfig` param (~133) and `self._budget_cfg` assignment.
- `ControlPlane.decide`: delete the `time_remaining_s: float` keyword param (~167) and every internal `time_remaining_s=...` passthrough (the `resolve_next` call ~458-462, and `_resolve_directive`/helper signatures at ~256, 361, 372, 387, 396, 451, 499, 519). The `resolve_next` call becomes:
  ```python
  nxt = resolve_next(
      questions=self._resolver_questions,
      asked_ids=asked_ids,
      preferred_next_signal=output.preferred_next_signal,
  )
  ```
  (Drop `covered_signals=` and `time_remaining_s=` / `cfg=`.)
- `build_control_plane` (~641-700): delete the `budget_cfg=budget_config_from_ai_config()` construction and stop passing it to `ControlPlane`.

> Knockout lines in this file (`KnockoutTracker`, `gate_knockout`, `_KNOCKOUT_REFLECT_LINE`, `_steer_knockout`, `confirmed_knockout_signals`, the reflect/gate blocks) are handled in **Task B2** — leave them for now; they don't reference the clock.

- [ ] **Step 5: `driver.py` — drop clock state + simplify `ResolverQuestion` build**

- `_BrainAdapter` (~158-177): delete `self.time_remaining_s` (~170) and the `time_remaining_s=self.time_remaining_s` argument in `decide` (~175).
- `SessionDriver.__init__`: delete `self._budget_cfg = budget_config_from_ai_config()` (~284) and its import.
- Delete `_time_remaining_s` method (~319-321).
- Rebuild `ResolverQuestion` (~256-269) as:
  ```python
  self._resolver_questions: list[ResolverQuestion] = [
      ResolverQuestion(
          question_id=q.id,
          primary_signal=q.primary_signal or (q.signal_values[0] if q.signal_values else ""),
          position=q.position,
      )
      for q in config.stage.questions
  ]
  ```
  (Delete the `signal_weight` dict that fed `weight=`.)
- `opener` (~433-439): the `resolve_next` call drops `covered_signals=` and `time_remaining_s=`/`cfg=`:
  ```python
  nxt = resolve_next(questions=self._resolver_questions, asked_ids=self._asked_ids)
  ```
- `handle_turn` (~566-578): drop `budget_phase=compute_budget_phase(...)` from the `build_turn_input(...)` call and remove the `compute_budget_phase` import; delete the `self._brain_adapter.time_remaining_s = self._time_remaining_s()` line (~604). Keep `self._brain_adapter.asked_ids = set(self._asked_ids)`.
- **KEEP** `self._time_budget_s`, the `time_budget_s` constructor param, and `time_budget_s=self._time_budget_s` in the `SessionMeta(...)` build (~812-825) — it is a durable planned-duration artifact, not runtime clock. Update its constructor docstring to: `"time_budget_s: planned stage duration in seconds — persisted to SessionMeta for audit; NOT used for runtime question gating."`

- [ ] **Step 6: Rewrite the budget cases in `test_resolver.py`**

Delete these tests (budget/phase/tier no longer exist): `test_compute_budget_phase_*` (all 5), `test_budget_config_from_ai_config`, `test_preference_honored_on_track_with_budget`, `test_preference_ignored_when_insufficient_budget`, `test_winding_down_prefers_mandatory_over_lower_position`, `test_truncation_not_reached_budget_too_low`, and any test constructing `BudgetConfig`. Add the replacements:
```python
def test_resolve_next_returns_lowest_position_unasked():
    qs = [
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
    ]
    nxt = resolve_next(questions=qs, asked_ids=set())
    assert nxt.question_id == "a"

def test_resolve_next_skips_asked():
    qs = [
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
    ]
    nxt = resolve_next(questions=qs, asked_ids={"a"})
    assert nxt.question_id == "b"

def test_resolve_next_none_when_all_asked():
    qs = [ResolverQuestion(question_id="a", primary_signal="s1", position=0)]
    assert resolve_next(questions=qs, asked_ids={"a"}) is None

def test_resolve_next_honors_preferred_signal():
    qs = [
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
    ]
    nxt = resolve_next(questions=qs, asked_ids=set(), preferred_next_signal="s2")
    assert nxt.question_id == "b"

def test_resolve_next_preferred_miss_falls_back_to_position():
    qs = [
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
    ]
    nxt = resolve_next(questions=qs, asked_ids=set(), preferred_next_signal="nope")
    assert nxt.question_id == "a"
```
Update any other test that constructs `ResolverQuestion(...)` to the 3-field shape, or `build_turn_input(...)` to drop `budget_phase=`.

- [ ] **Step 7: Run the engine suite**

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3 -m "not prompt_quality" -q`
Expected: PASS. (If `QuestionTier`/`QuestionRecord.tier` errors appear, proceed straight into Task A3 and run once after both — see A3 note — then commit.)

- [ ] **Step 8: Commit**

```bash
git add app/modules/interview_engine tests/interview_engine
git commit -m "refactor(interview_engine): delete session-clock/time-budget from resolver, brain, driver

Collapse resolve_next to positional selection (+ optional preferred_next_signal);
drop BudgetPhase/BudgetConfig/compute_budget_phase, time_remaining_s plumbing, and
the AIConfig budget knobs' resolver usage. Keep SessionMeta.time_budget_s as a
durable artifact.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A2: Delete the clock config knobs + prompt language

**Files:**
- Modify: `app/ai/config.py`
- Modify: `app/config.py`
- Modify: `prompts/v4/engine/brain.system.txt`

- [ ] **Step 1: Delete the AIConfig + Settings knobs**

`grep -n "engine_close_reserve_s\|engine_winding_down_s" app/ai/config.py app/config.py` and delete every match (the `AIConfig` properties and the `pydantic-settings` fields). Confirm nothing else references them: `grep -rn "engine_close_reserve_s\|engine_winding_down_s" app/` → expect only the (now-deleted) lines gone.

- [ ] **Step 2: Remove winding-down language from `brain.system.txt`**

In the "WHEN A THREAD IS DONE" section (~164-172) delete the sentence `When \`budget_phase\` is winding_down, probe at most once, then advance.` and reword the preceding line so advancing is coverage-driven only:
```
One good probe beats three. A second probe on the same gap is grinding — don't.
```
Search the whole prompt for `budget` / `winding` and remove any remaining mention.

- [ ] **Step 3: Run the engine suite**

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3 -m "not prompt_quality" -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/ai/config.py app/config.py prompts/v4/engine/brain.system.txt
git commit -m "refactor(interview_engine): delete clock config knobs + winding-down prompt language

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A3: Delete the tier-derived durable-contract fields

These are dead once the resolver is positional (`tier` was always `core`).

**Files:**
- Modify: `app/modules/interview_runtime/evidence.py`
- Modify: `app/modules/interview_engine/brain/resolver.py` (`build_question_records`)
- Modify: `app/modules/interview_engine/brain/input_builder.py` (`BankQuestionIndex` build)
- Modify: `app/modules/interview_engine/contracts.py` (`BankQuestionIndex`)
- Modify: `app/modules/interview_engine/driver.py` (`finalize`)
- Modify: `app/modules/reporting/` (any `QuestionRecord.tier` reader)
- Test: `tests/interview_engine_v3/test_notes.py`, `tests/reporting/` (drop tier assertions)

> NOTE: If your runner could not stay green at A1 Step 7 because of `QuestionTier`, do A1 and A3 as one commit.

- [ ] **Step 1: `evidence.py` — delete `QuestionTier` + `QuestionRecord.tier` + `SessionMeta` counts**

`grep -n "QuestionTier\|tier\|questions_core_total\|questions_overflow_asked" app/modules/interview_runtime/evidence.py`. Delete the `QuestionTier` enum, the `tier:` field on `QuestionRecord`, and the `questions_core_total` / `questions_overflow_asked` fields on `SessionMeta`. Remove `QuestionTier` from `__all__`/re-exports.

- [ ] **Step 2: `resolver.py` — `build_question_records` drops tier**

Confirm both `QuestionRecord(...)` constructions no longer pass `tier=` and the `QuestionTier` import is gone.

- [ ] **Step 3: `driver.py` — `finalize` stops computing the counts**

Delete the `questions_core_total = sum(...)` and `questions_overflow_asked = sum(...)` blocks (~805-811) and remove both kwargs from the `SessionMeta(...)` build.

- [ ] **Step 4: `contracts.py` + `input_builder.py` — drop `tier` from the bank index**

In `contracts.py` `BankQuestionIndex` delete the `tier: str` field. In `input_builder.py` `build_session_context` delete `tier="core"` from the `BankQuestionIndex(...)` build (~140).

- [ ] **Step 5: Clean reporting + tests of `tier`**

`grep -rn "\.tier\|QuestionTier\|questions_core_total\|questions_overflow_asked" app/modules/reporting/ tests/`. Remove any read of `QuestionRecord.tier` / the `SessionMeta` counts and the test assertions on them. (Inventory found no functional reporting consumer of the two counts; verify and delete any stale reference.)

- [ ] **Step 6: Run the full suite (engine + reporting)**

Run:
```bash
docker compose exec nexus python -m pytest tests/interview_engine_v3 tests/reporting -m "not prompt_quality" -q
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/modules tests
git commit -m "refactor(interview_engine): delete dead resolver tier machinery from durable contract

Remove QuestionTier, QuestionRecord.tier, SessionMeta core/overflow counts, and the
BankQuestionIndex.tier field — all constant 'core' since the budget/overflow scheduler
was deleted.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A4: Drop the engine-wire `estimated_minutes` / `is_mandatory` fields

DB columns stay (the reporting actor reads them off `StageQuestion`); only the engine wire + its projection are stale now.

**Files:**
- Modify: `app/modules/interview_runtime/schemas.py` (`QuestionConfig`)
- Modify: `app/modules/interview_runtime/service.py` (`build_session_config`)
- Modify: `app/modules/interview_engine/contracts.py` (`BankQuestionIndex.is_mandatory`)
- Modify: `app/modules/interview_engine/brain/input_builder.py`
- Test: `tests/interview_runtime/`, `tests/interview_engine_v3/` (drop the dropped fields from fixtures)

- [ ] **Step 1: `QuestionConfig` — delete `estimated_minutes` + `is_mandatory`**

In `schemas.py` `QuestionConfig` delete the `estimated_minutes: float` and `is_mandatory: bool` fields. Keep `position`, `text`, `signal_values`, `follow_ups`, `positive_evidence`, `red_flags`, `rubric`, `evaluation_hint`, `question_kind`, `primary_signal`, `difficulty`.

- [ ] **Step 2: `build_session_config` — stop projecting them + order by position**

In `service.py` `build_session_config`: remove `estimated_minutes=...` and `is_mandatory=...` from the `QuestionConfig(...)` build (~235-236), and change the question query ordering from `StageQuestion.is_mandatory.desc(), StageQuestion.position.asc()` to `StageQuestion.position.asc()` only (~168-172). Update the `mandatory_count`/`optional_count` log fields (~279-280) to drop them (they read `q.is_mandatory`).

- [ ] **Step 3: `BankQuestionIndex` — delete `is_mandatory`**

In `contracts.py` delete `is_mandatory: bool` from `BankQuestionIndex`; in `input_builder.py` delete `is_mandatory=q.is_mandatory` from the `BankQuestionIndex(...)` build (~139).

- [ ] **Step 4: Fix fixtures/tests**

`grep -rn "estimated_minutes\|is_mandatory" tests/interview_runtime tests/interview_engine_v3`. Remove those kwargs from any `QuestionConfig(...)` / `BankQuestionIndex(...)` construction in fixtures and tests. (Leave `StageQuestion` fixtures and `tests/reporting` alone — the DB columns stay.)

- [ ] **Step 5: Run the suite**

Run:
```bash
docker compose exec nexus python -m pytest tests/interview_engine_v3 tests/interview_runtime -m "not prompt_quality" -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules tests
git commit -m "refactor(interview_runtime): drop engine-wire estimated_minutes/is_mandatory

Engine was their sole consumer once the clock+tier scheduler was deleted. The
stage_questions DB columns stay — reporting/actors.py reads them off StageQuestion.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A5: Phase-1A guard test + checkpoint

**Files:**
- Create: `tests/interview_engine_v3/test_no_clock_symbols.py`

- [ ] **Step 1: Write the guard test**

```python
"""No-stale-code guard: the session-clock / resolver-tier subsystem is gone.

Fails if any deleted clock/tier symbol reappears in the engine or runtime code.
"""
from __future__ import annotations

import pathlib

_ROOTS = [
    "app/modules/interview_engine",
    "app/modules/interview_runtime",
]
_FORBIDDEN = [
    "BudgetPhase", "BudgetConfig", "compute_budget_phase",
    "budget_config_from_ai_config", "budget_phase",
    "time_remaining_s", "_budget_cfg",
    "engine_close_reserve_s", "engine_winding_down_s",
    "QuestionTier", "questions_core_total", "questions_overflow_asked",
]


def test_no_clock_or_tier_symbols_remain():
    repo = pathlib.Path(__file__).resolve().parents[2]  # backend/nexus
    offenders: list[str] = []
    for root in _ROOTS:
        for py in (repo / root).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for sym in _FORBIDDEN:
                if sym in text:
                    offenders.append(f"{py.relative_to(repo)}: {sym}")
    assert not offenders, "stale clock/tier symbols:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run it**

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3/test_no_clock_symbols.py -q`
Expected: PASS. If it fails, fix the named file (a missed reference) and re-run.

- [ ] **Step 3: Manual talk-test (checkpoint)**

Restart the engine and run a short live screen; confirm: intro plays, questions advance by position, probes fire (and stop at the cap), the screen closes on bank exhaustion, and there is no time/budget-driven behavior.
```bash
docker compose up -d --force-recreate nexus-engine
```
(Use your normal candidate-session flow to talk to agent "Arjun".)

- [ ] **Step 4: Commit**

```bash
git add tests/interview_engine_v3/test_no_clock_symbols.py
git commit -m "test(interview_engine): guard against clock/tier symbol regressions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PHASE 1B — Knockout full-vertical deletion

## Task B1: Delete the knockout policy gate + tracker

**Files:**
- Modify: `app/modules/interview_engine/brain/policy.py`
- Test: `tests/interview_engine_v3/test_policy.py`

- [ ] **Step 1: `policy.py` — delete Gate 1**

Delete `KnockoutStep` (~67-79), `_KNOCKOUT_PROGRESSION` (~75-79), `KnockoutTracker` (~83-119), `KnockoutGate` (~123-128), and `gate_knockout` (~133-187). Trim the module docstring's "1. gate_knockout — verified-knockout state machine" bullet (~11). **KEEP** `scrub_composed_say` (~194) and `coerce_probe_dimension` (~266) untouched. Remove any now-unused imports (e.g. `Sequence`, `StrEnum`) only if nothing else uses them.

- [ ] **Step 2: `test_policy.py` — delete knockout tests**

Delete the knockout test classes/functions (`TestKnockoutTracker`, `TestGateKnockout`, and any `gate_knockout`/`KnockoutTracker`/`KnockoutStep` usage). Keep the `scrub_composed_say` + `coerce_probe_dimension` tests.

- [ ] **Step 3: Run policy tests** (the engine suite will still be red until B2 — that's expected; run the policy file alone here)

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3/test_policy.py -q`
Expected: PASS.

> Do NOT commit yet — `brain/service.py` still imports the deleted gate. Proceed to B2 and commit B1+B2 together.

---

## Task B2: Strip knockout from `ControlPlane` (brain/service.py)

**Files:**
- Modify: `app/modules/interview_engine/brain/service.py`
- Modify: `app/modules/interview_engine/driver.py` (finalize — moved here to keep the suite green: the driver calls the method this task deletes)
- Test: `tests/interview_engine_v3/test_brain_service.py`, `tests/interview_engine_v3/test_driver.py` (+ any `test_service_probe_dimension.py`)

- [ ] **Step 1: Delete imports + constant**

Remove `KnockoutTracker`, `gate_knockout` from the policy import (~41-43). Delete `_KNOCKOUT_REFLECT_LINE` (~74-81).

- [ ] **Step 2: `__init__` — drop tracker state**

Delete the `knockout_tracker: KnockoutTracker | None = None` param (~134), the `self._knockout_tracker = ...` assignment (~143), and `self._knockout_reflect_offered: set[str]` (~147) + its docstring. Trim the class docstring's knockout lines (~119-120).

- [ ] **Step 3: `decide` — delete the reflect/gate/honor blocks**

In `decide` (~160 onward): delete the `reflected_pending` block (~171-181) and the `update={"knockout_reflected": ...}` mutation; delete the knockout-confirmed honor block (~286-340: the `knockout_specs`, the `confirm`-registers-reflect block, the `knockout_confirmed` close-honor block, and the `gate_knockout(...)` call + `_steer_knockout` return ~341-351). In the `BrainDecision(...)`/output mapping (~230-231) delete `knockout_confirmed=output.knockout_confirmed` and `knockout_pending=turn_input.knockout_pending` if present. **KEEP** the candidate-end bypass (`end_requested`) logic (~274-276).

- [ ] **Step 4: Delete helper methods**

Delete `_steer_knockout` (~538-587) and `confirmed_knockout_signals` (~590-600).

- [ ] **Step 5: `build_control_plane` — drop the tracker**

Delete `knockout_tracker=KnockoutTracker()` (~691) from the `ControlPlane(...)` construction; update the factory docstring (~641-642) to drop "fresh KnockoutTracker".

- [ ] **Step 6: `driver.py` — remove the finalize knockout block (coupled to Step 4)**

The driver calls `self._brain.confirmed_knockout_signals()` (deleted in Step 4), so this MUST happen in the same commit. In `finalize`: delete the lazy `from ...brain.policy import KnockoutStep` import (~278) if present, and delete the "Record a KnockoutOutcome" block (~827-846: the `confirmed = self._brain.confirmed_knockout_signals()` call, the `KnockoutOutcome(...)` build, and the `completion → knockout_close` `model_copy`). In the `to_session_evidence(...)` call (~849-855), change `knockout=knockout_outcome` to `knockout=None` (transient — `SessionEvidence.knockout` + the param are deleted in Task B5). Remove the `KnockoutOutcome` import from `driver.py`.

- [ ] **Step 7: `test_brain_service.py` + `test_driver.py` — delete knockout cases**

In `test_brain_service.py` delete every knockout test (reflect-back, knockout-confirmed close, forced reflect, `confirmed_knockout_signals`, gate-blocked close) and drop the `knockout_tracker=`/`budget_cfg=` kwargs + any `knockout_pending`/`knockout_reflected` on `BrainTurnInput` from survivors. In `test_driver.py` delete tests asserting `KnockoutOutcome`/`knockout_close` recording in `finalize`.

- [ ] **Step 8: Run the engine suite**

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3 -m "not prompt_quality" -q`
Expected: PASS (policy + service + driver knockout removals are now mutually consistent).

- [ ] **Step 9: Commit B1 + B2**

```bash
git add app/modules/interview_engine/brain app/modules/interview_engine/driver.py \
        tests/interview_engine_v3/test_policy.py tests/interview_engine_v3/test_brain_service.py \
        tests/interview_engine_v3/test_driver.py
git commit -m "refactor(interview_engine): delete verified-knockout gate, tracker, and driver finalize

Remove gate_knockout/KnockoutTracker/KnockoutStep (policy), all ControlPlane knockout
handling (_steer_knockout, confirmed_knockout_signals, reflect/honor blocks), and the
driver's KnockoutOutcome finalize block (passes knockout=None until B5 deletes the field).
Keep end_requested, confirm, scrub_composed_say, coerce_probe_dimension.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B3: Remove knockout fields from contracts + input_builder

**Files:**
- Modify: `app/modules/interview_engine/contracts.py`
- Modify: `app/modules/interview_engine/brain/input_builder.py`
- Test: `tests/interview_engine_v3/` (input_builder + contracts fixtures)

- [ ] **Step 1: `contracts.py` — delete brain knockout fields**

Delete `BrainTurnOutput.knockout_confirmed` (~141-149), `BrainTurnInput.knockout_pending` (~290-299), and `BrainTurnInput.knockout_reflected` (~300-306). **KEEP** `BrainTurnOutput.end_requested` (~133-140) and `SignalSpec.knockout` (~169, data attribute).

- [ ] **Step 2: `input_builder.py` — delete `knockout_pending()` + render blocks**

Delete `CoverageProjection.knockout_pending` (~260-286). In `build_turn_input` (~293-326) remove the `knockout_pending=projection.knockout_pending(all_specs)` argument. In `render_suffix` delete the `knockout_block` (~440-447), the `knockout_reflected_block` (~449-458), and remove both from the joined `content` tuple (~499-512). Trim the fallback docstring lines that mention `knockout=False`.

- [ ] **Step 3: Fix tests**

`grep -rn "knockout_pending\|knockout_reflected\|knockout_confirmed" tests/interview_engine_v3`. Delete the `test_knockout_pending_*` cases in the input-builder tests and remove those kwargs from any `BrainTurnInput(...)`/`BrainTurnOutput(...)` construction.

- [ ] **Step 4: Run the engine suite**

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3 -m "not prompt_quality" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine tests/interview_engine
git commit -m "refactor(interview_engine): remove knockout fields from brain contracts + coverage

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B4: Remove knockout from the brain prompt

(The driver-finalize removal + `test_driver.py` cleanup were folded into Task B2 to keep the suite green.)

**Files:**
- Modify: `prompts/v4/engine/brain.system.txt`

- [ ] **Step 1: `brain.system.txt` — delete the KNOCKOUT section**

Delete the entire "KNOCKOUT (only when `knockout_pending` lists the signal)" section (~183-203) and the "ABSENCE IS DIFFERENT FOR A KNOCKOUT" lines in "WHEN A THREAD IS DONE" (~173-175). In the `close` move description (~95-100) remove the "a mandatory knockout is confirmed absent" clause and the `knockout_confirmed=true` sentence — keep `end_requested` + ordinary full-coverage close. In `confirm` (~66-72) remove "Use this especially before concluding a mandatory skill is absent — never knockout on a likely mishearing." (keep the general STT-mishearing purpose). `grep -n "knockout" prompts/v4/engine/brain.system.txt` → expect zero matches. Proofread the result for coherence.

- [ ] **Step 2: Run the engine suite**

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3 -m "not prompt_quality" -q`
Expected: PASS (the prompt change doesn't affect mocked-LLM tests; this confirms nothing else broke).

- [ ] **Step 3: Commit**

```bash
git add prompts/v4/engine/brain.system.txt
git commit -m "refactor(interview_engine): delete the KNOCKOUT section from the brain prompt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B5: Delete the durable knockout contract (evidence.py)

**Files:**
- Modify: `app/modules/interview_runtime/evidence.py`
- Modify: `app/modules/interview_engine/driver.py` (drop the transient `knockout=None` arg)
- Modify: `app/modules/interview_engine/notes.py` (if `NoteLog.to_session_evidence` declares a `knockout` param)
- Test: `tests/interview_engine_v3/test_notes.py` + `tests/interview_runtime/`

- [ ] **Step 1: Delete `KnockoutOutcome`, `SessionEvidence.knockout`, `CompletionReason.knockout_close`**

`grep -n "KnockoutOutcome\|knockout_close\|knockout" app/modules/interview_runtime/evidence.py`. Delete the `KnockoutOutcome` class (~202-210), the `knockout: KnockoutOutcome | None = None` field on `SessionEvidence` (~249) + its docstring, and the `knockout_close` member of `CompletionReason` (~78-83). **KEEP** `SignalEvidence.knockout` (~96, data). Remove `KnockoutOutcome` from `__all__`.

- [ ] **Step 1b: Remove the `knockout` param from `to_session_evidence` + the driver's `knockout=None`**

`grep -rn "to_session_evidence" app/modules/interview_engine`. In `notes.py` `NoteLog.to_session_evidence` delete the `knockout` parameter and stop passing it to `SessionEvidence(...)`. In `driver.py` `finalize` remove the now-stale `knockout=None` argument from the `to_session_evidence(...)` call (introduced transiently in Task B2).

- [ ] **Step 2: Audit `CompletionReason` switches**

`grep -rn "knockout_close\|CompletionReason" app/modules tests`. Ensure no match/if branch references `knockout_close` (delete any dead branch). The driver always finalizes with `CompletionReason.completed` (or other surviving reasons).

- [ ] **Step 3: Fix tests**

Remove `knockout=`/`KnockoutOutcome`/`knockout_close` from `to_session_evidence(...)` and `SessionMeta(...)` constructions in tests.

- [ ] **Step 4: Run engine + runtime suites**

Run:
```bash
docker compose exec nexus python -m pytest tests/interview_engine_v3 tests/interview_runtime -m "not prompt_quality" -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_runtime/evidence.py app/modules/interview_engine/notes.py \
        app/modules/interview_engine/driver.py tests
git commit -m "refactor(interview_runtime): delete KnockoutOutcome + knockout_close from SessionEvidence

Also drops the to_session_evidence knockout param + the driver's transient knockout=None.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B6: Delete the `KnockoutFailure` self-disclosure stub

**Files:**
- Modify: `app/modules/interview_runtime/schemas.py`
- Modify: `app/modules/interview_runtime/service.py` (`record_session_result`)
- Modify: `app/modules/session/models.py` (`KnockoutFailure` model + column mapping)
- Test: delete `tests/test_session_result_knockout_failures.py`, `tests/interview_runtime/integration/test_record_session_result_knockout_failures.py`, `tests/test_interview_runtime_knockout_failure.py`

- [ ] **Step 1: `schemas.py` — delete the model + field + orphaned scrubbers**

Delete `KnockoutFailure` (~383-408), `SessionResult.knockout_failures` (~430-437). Then check the PII scrubbers: `grep -n "_scrub_pii\|_EMAIL_RE\|_PHONE_RE" app/modules/interview_runtime/schemas.py` — if their ONLY user was `KnockoutFailure`, delete them too; if anything else uses them, keep. (Document which in the commit.)

- [ ] **Step 2: `service.py` — stop writing `knockout_failures`**

In `record_session_result` delete `knockout_failures=[k.model_dump(mode="json") for k in result.knockout_failures]` from the `update(...).values(...)` (~411).

- [ ] **Step 3: `session/models.py` — delete the ORM column mapping**

Delete the `knockout_failures` mapped column (~74) and the `KnockoutFailure` model if it lives here. (The physical DB column is dropped in Task B8's migration.)

- [ ] **Step 4: Delete the stub's tests**

```bash
git rm tests/test_session_result_knockout_failures.py \
       tests/interview_runtime/integration/test_record_session_result_knockout_failures.py \
       tests/test_interview_runtime_knockout_failure.py
```
(Adjust paths if `grep -rln "KnockoutFailure" tests/` shows different filenames.)

- [ ] **Step 5: Run the suite**

Run:
```bash
docker compose exec nexus python -m pytest tests/interview_runtime tests/interview_engine_v3 -m "not prompt_quality" -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules tests
git commit -m "refactor: delete never-wired KnockoutFailure self-disclosure stub

Removes KnockoutFailure model, SessionResult.knockout_failures, the ORM column mapping,
and the orphaned PII scrubbers. Physical column dropped in the 0059 migration.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B7: Remove the reporting knockout gate (keep must-have awareness)

**Files:**
- Modify: `app/modules/reporting/scoring/evidence_adapter.py`
- Modify: `app/modules/reporting/scoring/holistic.py`
- Modify: the verdict resolver (find via `grep -rn "def resolve_verdict" app/modules/reporting`)
- Modify: `app/modules/reporting/service.py`
- Modify: `prompts/v4/report_scorer/*`
- Test: `tests/reporting/` (verdict/scoring)

- [ ] **Step 1: `evidence_adapter.py` — delete knockout-close properties**

Delete the `is_knockout_close` and `knockout_signal` properties (~82-87). Their source (`SessionEvidence.knockout` / `CompletionReason.knockout_close`) is gone.

- [ ] **Step 2: verdict resolver + `holistic.py` — drop the params/branches**

`grep -rn "is_knockout_close\|knockout_signal" app/modules/reporting`. In `resolve_verdict` and `score_holistic` delete the `is_knockout_close` / `knockout_signal` parameters and any branch keyed on them (e.g. the knockout-close → reject path and the knockout ceiling cap tied to the *completion*). **KEEP** must-have logic keyed on `signal.knockout`: must-have identification, the must-have-met ceiling, and narrative tagging.

- [ ] **Step 3: `service.py` — drop the call args + response key**

In `build_report` remove `is_knockout_close=...` / `knockout_signal=...` from the `signal_ceiling(...)`, `score_holistic(...)`, `resolve_verdict(...)` calls (~192-209). Delete the `"knockout_close": (... if view.is_knockout_close else None)` response key (~246). **KEEP** `must_have` / `must_have_signals` / the `knockout=s.knockout` tags.

- [ ] **Step 4: `prompts/v4/report_scorer/*` — strip knockout_close instructions**

`grep -rn "knockout" prompts/v4/report_scorer/`. Delete any instruction that assumes a `knockout_close` completion or auto-reject path. Leave must-have wording.

- [ ] **Step 5: Fix reporting tests**

`grep -rn "is_knockout_close\|knockout_close\|knockout_signal" tests/reporting`. Delete knockout-close verdict/ceiling test cases; refactor the survivors to the must-have-only path. Add (or keep) a test asserting a candidate with a must-have at coverage `none`/`contradicts` resolves to **borderline/reject via normal scoring** (no special auto-gate).

- [ ] **Step 6: Run reporting suite + a fixture report**

Run:
```bash
docker compose exec nexus python -m pytest tests/reporting -m "not prompt_quality" -q
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/modules/reporting prompts/v4/report_scorer tests/reporting
git commit -m "refactor(reporting): remove knockout_close gate; keep signal.knockout must-have awareness

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B8: Delete `engine_knockout_policy` (tenant_settings)

**Files:**
- Modify: `app/modules/tenant_settings/models.py`
- Modify: `app/modules/tenant_settings/schemas.py`
- Test: `tests/` (any tenant_settings test referencing the field)

- [ ] **Step 1: Delete the column mapping + schema field + readers**

`grep -rn "engine_knockout_policy\|KnockoutPolicy" app/ tests/`. In `tenant_settings/models.py` delete the `engine_knockout_policy` mapped column. In `tenant_settings/schemas.py` delete the `KnockoutPolicy` Literal type and the `engine_knockout_policy` field. Delete any reader (the spec notes it's currently unread by the engine — confirm none remain).

- [ ] **Step 2: Fix tests**

Remove `engine_knockout_policy` from tenant-settings test assertions/fixtures.

- [ ] **Step 3: Run the suite**

Run: `docker compose exec nexus python -m pytest tests -k "tenant_settings or interview" -m "not prompt_quality" -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/modules/tenant_settings tests
git commit -m "refactor(tenant_settings): delete unused engine_knockout_policy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B9: DB migration `0059_drop_knockout`

**Files:**
- Create: `migrations/versions/0059_drop_knockout.py`

- [ ] **Step 1: Read the source DDL to mirror in downgrade**

Run: `docker compose exec nexus sed -n '1,200p' migrations/versions/0027_tenant_settings.py` and note the exact `sessions.knockout_failures` column definition, the `engine_knockout_policy` column definition, and its CHECK constraint name. Also note `0030_default_close_polite.py` for the default value. Confirm current head:
```bash
docker compose exec nexus alembic heads
```
Expected head: `0058_bank_coverage_feasibility`.

- [ ] **Step 2: Write the migration**

```python
"""drop verified-knockout DB columns

Revision ID: 0059_drop_knockout
Revises: 0058_bank_coverage_feasibility
Create Date: 2026-06-14

Drops tenant_settings.engine_knockout_policy (+ its CHECK) and sessions.knockout_failures.
The engine verified-knockout feature was deleted 2026-06-14 (spec
2026-06-14-interview-engine-clock-knockout-deletion-design.md). Migrations 0027/0030
remain as historical record.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0059_drop_knockout"
down_revision = "0058_bank_coverage_feasibility"
branch_labels = None
depends_on = None

# NOTE: replace the CHECK name + column defaults below with the EXACT values read
# from 0027/0030 in Step 1 if they differ.
_KNOCKOUT_POLICY_CK = "ck_tenant_settings_engine_knockout_policy"


def upgrade() -> None:
    op.drop_constraint(_KNOCKOUT_POLICY_CK, "tenant_settings", type_="check")
    op.drop_column("tenant_settings", "engine_knockout_policy")
    op.drop_column("sessions", "knockout_failures")


def downgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "knockout_failures",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "tenant_settings",
        sa.Column(
            "engine_knockout_policy",
            sa.Text(),
            server_default=sa.text("'close_polite'"),  # per 0030; use 0027's 'record_only' if you revert pre-0030
            nullable=False,
        ),
    )
    op.create_check_constraint(
        _KNOCKOUT_POLICY_CK,
        "tenant_settings",
        "engine_knockout_policy IN ('record_only', 'close_polite')",
    )
```

- [ ] **Step 3: Apply, verify, round-trip**

Run:
```bash
docker compose exec nexus alembic upgrade head
docker compose exec nexus alembic downgrade -1
docker compose exec nexus alembic upgrade head
```
Expected: all succeed; `alembic heads` shows `0059_drop_knockout`.

- [ ] **Step 4: Boot check (RLS completeness still passes)**

Run: `docker compose up -d --force-recreate nexus && docker compose logs --tail=50 nexus`
Expected: no `_assert_rls_completeness` CRITICAL; app starts.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0059_drop_knockout.py
git commit -m "migration(0059): drop engine_knockout_policy + sessions.knockout_failures

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B10: Phase-1B guard test + final verification

**Files:**
- Create: `tests/interview_engine_v3/test_no_knockout_symbols.py`

- [ ] **Step 1: Write the guard test**

```python
"""No-stale-code guard: the verified-knockout feature is gone.

Only knockout *behavior* symbols are forbidden. The JD signal DATA attribute
`knockout` (SignalSpec.knockout, SignalEvidence.knockout, SignalMetadata.knockout,
jd schema, reporting must-have use) is intentionally KEPT and not checked here.
"""
from __future__ import annotations

import pathlib

_ROOTS = [
    "app/modules/interview_engine",
    "app/modules/interview_runtime",
    "app/modules/reporting",
    "app/modules/tenant_settings",
]
_FORBIDDEN = [
    "KnockoutOutcome", "knockout_close", "KnockoutFailure", "knockout_failures",
    "gate_knockout", "KnockoutTracker", "KnockoutStep",
    "knockout_pending", "knockout_reflected", "knockout_confirmed",
    "confirmed_knockout_signals", "engine_knockout_policy", "KnockoutPolicy",
]


def test_no_knockout_behavior_symbols_remain():
    repo = pathlib.Path(__file__).resolve().parents[2]  # backend/nexus
    offenders: list[str] = []
    for root in _ROOTS:
        for py in (repo / root).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for sym in _FORBIDDEN:
                if sym in text:
                    offenders.append(f"{py.relative_to(repo)}: {sym}")
    assert not offenders, "stale knockout-behavior symbols:\n" + "\n".join(offenders)


def test_signal_knockout_data_attribute_is_kept():
    # The DATA attribute survives — sanity-check one canonical home.
    from app.modules.interview_engine.contracts import SignalSpec
    assert "knockout" in SignalSpec.model_fields
```

- [ ] **Step 2: Run it**

Run: `docker compose exec nexus python -m pytest tests/interview_engine_v3/test_no_knockout_symbols.py -q`
Expected: PASS. Fix any named offender and re-run.

- [ ] **Step 3: Full suite + prompt grep**

Run:
```bash
docker compose exec nexus python -m pytest tests -m "not prompt_quality" -q
grep -rn "knockout" prompts/v4/engine/ prompts/v4/report_scorer/ || echo "no knockout in prompts (good)"
```
Expected: suite PASS; no knockout in prompts.

- [ ] **Step 4: Restart worker + engine (no hot-reload), manual talk-test**

```bash
docker compose up -d --force-recreate nexus-worker nexus-engine
```
Talk-test a short screen: confirm no knockout language, a clean full-coverage close, and `confirm`/`end_requested` still work. Then run the report scorer on the resulting session (or a saved `SessionEvidence` fixture) and confirm a verdict resolves with no knockout gate.

- [ ] **Step 5: Commit**

```bash
git add tests/interview_engine_v3/test_no_knockout_symbols.py
git commit -m "test(interview_engine): guard against knockout-behavior symbol regressions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Done criteria

- Both guard tests pass; full suite green (`pytest tests -m "not prompt_quality"`).
- `grep -rn "knockout" prompts/v4` returns nothing.
- `alembic heads` == `0059_drop_knockout`; up/down/up round-trips clean; app boots (RLS check passes).
- A manual talk-test screen runs end-to-end with positional advance, working `probe`/`confirm`/`end_requested`/close, and no clock or knockout behavior.
- The reporting path produces a verdict with must-have awareness intact and no knockout gate.
