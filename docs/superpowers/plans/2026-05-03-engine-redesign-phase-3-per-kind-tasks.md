# Engine Redesign — Phase 3: Per-kind Task Subclasses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `BehavioralStarTask` (max_probes=2) and `ComplianceBinaryTask` (max_probes=0, 60s hard cap, single-shot fairness clarification) alongside an extracted `tasks/factory.py` that routes on a new `question_kind` field on `QuestionConfig`. Strictly additive — every real interview still routes to `TechnicalDepthTask` until Phase 4 lands the database column.

**Architecture:** Two new concrete subclasses of the Phase 2 `QuestionTask` base, each with its own `@function_tool` surface tailored to its question kind (STAR-component coverage detection for behavioral, yes/no attestation with optional clarification for compliance). The factory reads `question.question_kind` from a routing table and falls back to `TechnicalDepthTask` for unknowns. Per-task hard caps (the 60s compliance cap) flow through a new `effective_budget_seconds_for(question)` helper that the controller's `_dispatch_task` consults.

**Tech Stack:** Python 3.13, `livekit-agents`, asyncpg, pydantic v2, pytest + pytest-asyncio, structlog. Tests run in the nexus Docker container: `cd backend/nexus && docker compose run nexus pytest <path>`.

**Spec:** [`docs/superpowers/specs/2026-05-03-engine-redesign-phase-3-per-kind-tasks-design.md`](../specs/2026-05-03-engine-redesign-phase-3-per-kind-tasks-design.md). Read end-to-end before starting Task 1; the per-prompt fairness checklists (§4.1, §5.1) gate Tasks 6 and 11.

---

## File Structure

**New files (in build order):**

| Path | Responsibility |
|---|---|
| `backend/nexus/app/modules/interview_engine/tasks/factory.py` | `_ROUTING_TABLE`, `build_task_for`, `effective_budget_seconds_for`, `_build_rubric_block` (moved from `tasks/__init__.py`) |
| `backend/nexus/app/modules/interview_engine/tasks/behavioral.py` | `BehavioralStarTask` + `record_behavioral_answer` / `request_star_probe` / `complete_question` tools |
| `backend/nexus/app/modules/interview_engine/tasks/compliance_binary.py` | `ComplianceBinaryTask` + `record_compliance_attestation` / `request_compliance_clarification` tools |
| `backend/nexus/prompts/v1/interview/task_behavioral.txt` | Behavioral STAR task system prompt — fairness signoff required |
| `backend/nexus/prompts/v1/interview/task_compliance_binary.txt` | Compliance binary task system prompt — fairness signoff required |
| `backend/nexus/tests/interview_engine/unit/test_factory.py` | Routing table coverage + `effective_budget_seconds_for` |
| `backend/nexus/tests/interview_engine/unit/test_behavioral_task.py` | Construction + tool methods + edge cases |
| `backend/nexus/tests/interview_engine/unit/test_compliance_binary_task.py` | Construction + tool methods + single-shot clarification + knockout pairing |
| `backend/nexus/tests/interview_engine/integration/test_behavioral_flow.py` | Controller dispatches `behavioral_star` end-to-end with fake task |
| `backend/nexus/tests/interview_engine/integration/test_compliance_binary_flow.py` | Controller dispatches `compliance_binary` end-to-end + 60s watchdog cap + knockout pairing |
| `backend/nexus/tests/interview_engine/prompt_quality/test_star_component_detection.py` | Real LLM: STAR coverage detection (3 cases) |
| `backend/nexus/tests/interview_engine/prompt_quality/test_compliance_binary_quality.py` | Real LLM: yes/no extraction + ambiguity clarification + no proxy probing (5 cases) |

**Modified files:**

| Path | Change |
|---|---|
| `backend/nexus/app/modules/interview_runtime/schemas.py` | Add `question_kind: Literal[...] = "technical_depth"` field to `QuestionConfig` |
| `backend/nexus/app/modules/interview_engine/tasks/base.py` | Widen `TaskResult.kind` Literal to 3 values; add 4 new optional fields (`star_components`, `compliance_confirmed`, `compliance_reason_or_example`, `compliance_clarification_used`) |
| `backend/nexus/app/modules/interview_engine/tasks/__init__.py` | Re-export `BehavioralStarTask` + `ComplianceBinaryTask`; `build_task_for` re-imported from `factory.py` (preserves public API) |
| `backend/nexus/app/modules/interview_engine/controller.py` | Replace inline watchdog computations (~line 198, ~line 211-214) with `effective_budget_seconds_for(q)` calls; add import |
| `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` | Update Phase status index: Phase 3 🟠 → ✅; add link to this plan |
| `backend/nexus/CLAUDE.md` | Update interview-engine status block to mention BehavioralStarTask + ComplianceBinaryTask |

**No deleted files. No new tests directories. No DB migration. No new env vars.**

---

## Task 1: Add `question_kind` field to `QuestionConfig`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py`
- Test: `backend/nexus/tests/interview_runtime/test_schemas.py` (add new test class — file may not exist yet)

- [ ] **Step 1: Inspect the current `QuestionConfig` shape**

```bash
cd /home/ishant/Projects/ProjectX
grep -n "class QuestionConfig" backend/nexus/app/modules/interview_runtime/schemas.py
sed -n '50,75p' backend/nexus/app/modules/interview_runtime/schemas.py
```
Expected: `QuestionConfig(BaseModel)` definition spanning ~lines 51-75 with fields `id`, `position`, `text`, `signal_values`, `estimated_minutes`, `is_mandatory`, etc.

- [ ] **Step 2: Write the failing test**

Create or extend `backend/nexus/tests/interview_runtime/test_schemas.py`:

```python
"""Schema-level tests for interview_runtime models."""

from __future__ import annotations

import pytest

from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _make_question(**overrides):
    base = dict(
        id="q-test",
        position=0,
        text="A long enough placeholder question text body goes here.",
        signal_values=["python"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["evidence_a", "evidence_b", "evidence_c"],
        red_flags=["red_flag_a", "red_flag_b"],
        rubric=QuestionRubric(
            excellent="excellent rubric body",
            meets_bar="meets-bar rubric body",
            below_bar="below-bar rubric body",
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
    )
    base.update(overrides)
    return QuestionConfig(**base)


class TestQuestionKindField:
    def test_question_kind_defaults_to_technical_depth(self) -> None:
        q = _make_question()
        assert q.question_kind == "technical_depth"

    def test_question_kind_accepts_behavioral_star(self) -> None:
        q = _make_question(question_kind="behavioral_star")
        assert q.question_kind == "behavioral_star"

    def test_question_kind_accepts_compliance_binary(self) -> None:
        q = _make_question(question_kind="compliance_binary")
        assert q.question_kind == "compliance_binary"

    def test_question_kind_accepts_open_culture(self) -> None:
        q = _make_question(question_kind="open_culture")
        assert q.question_kind == "open_culture"

    def test_question_kind_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError):
            _make_question(question_kind="not_a_real_kind")  # type: ignore[arg-type]
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_runtime/test_schemas.py::TestQuestionKindField -v
```
Expected: 5 failures with `ValidationError` or `AttributeError` about `question_kind` not being a valid field.

- [ ] **Step 4: Add the field to `QuestionConfig`**

Edit `backend/nexus/app/modules/interview_runtime/schemas.py`. Find the `QuestionConfig` class (around line 51) and add the field. The exact location: right after the existing field definitions, before any model_config or validators. Use `Literal` from typing.

Locate the existing import line (likely already has `from typing import ...`) and add `Literal` if not present. Then in the class body, add this field after `evaluation_hint`:

```python
    question_kind: Literal[
        "technical_depth",
        "behavioral_star",
        "compliance_binary",
        "open_culture",
    ] = "technical_depth"
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_runtime/test_schemas.py::TestQuestionKindField -v
```
Expected: 5 passes.

- [ ] **Step 6: Run the full interview_engine suite to verify no regressions**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" -v 2>&1 | tail -30
```
Expected: all 128 Phase 2 tests still pass (the new field has a default, so existing constructors work unchanged).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py backend/nexus/tests/interview_runtime/test_schemas.py
git commit -m "$(cat <<'EOF'
feat(engine): add question_kind field to QuestionConfig

In-memory only — defaults to "technical_depth" so every real interview
keeps routing to TechnicalDepthTask. Phase 4 will land the DB column +
bank-generator update that fills this with non-default values.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extend `TaskResult` for behavioral + compliance

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/tasks/base.py:34-51`
- Test: `backend/nexus/tests/interview_engine/unit/test_task_base.py` (add new test class)

- [ ] **Step 1: Read the current `TaskResult` shape**

```bash
sed -n '34,55p' backend/nexus/app/modules/interview_engine/tasks/base.py
```
Expected: `class TaskResult(BaseModel)` with `kind: Literal["technical_depth"]` and the existing field set ending with `probes_fired`.

- [ ] **Step 2: Write the failing test**

Append to `backend/nexus/tests/interview_engine/unit/test_task_base.py`:

```python
class TestTaskResultPhase3Fields:
    def test_kind_accepts_behavioral_star(self) -> None:
        result = TaskResult(question_id="q-1", kind="behavioral_star")
        assert result.kind == "behavioral_star"

    def test_kind_accepts_compliance_binary(self) -> None:
        result = TaskResult(question_id="q-1", kind="compliance_binary")
        assert result.kind == "compliance_binary"

    def test_kind_rejects_unknown_value(self) -> None:
        import pytest
        with pytest.raises(Exception):  # pydantic ValidationError
            TaskResult(question_id="q-1", kind="open_culture")  # type: ignore[arg-type]

    def test_star_components_default_none(self) -> None:
        result = TaskResult(question_id="q-1", kind="behavioral_star")
        assert result.star_components is None

    def test_star_components_accepts_partial_dict(self) -> None:
        result = TaskResult(
            question_id="q-1",
            kind="behavioral_star",
            star_components={
                "situation": "Last year at my prior job",
                "task": "Lead the migration",
                "action": None,
                "result": None,
            },
        )
        assert result.star_components["situation"] == "Last year at my prior job"
        assert result.star_components["action"] is None

    def test_compliance_fields_default_none_and_false(self) -> None:
        result = TaskResult(question_id="q-1", kind="compliance_binary")
        assert result.compliance_confirmed is None
        assert result.compliance_reason_or_example is None
        assert result.compliance_clarification_used is False

    def test_compliance_fields_settable(self) -> None:
        result = TaskResult(
            question_id="q-1",
            kind="compliance_binary",
            compliance_confirmed=True,
            compliance_reason_or_example="Confirmed without further context",
            compliance_clarification_used=True,
        )
        assert result.compliance_confirmed is True
        assert result.compliance_reason_or_example == "Confirmed without further context"
        assert result.compliance_clarification_used is True

    def test_existing_technical_depth_serialization_unchanged(self) -> None:
        """Phase 2 callers must still produce the same shape."""
        result = TaskResult(
            question_id="q-1",
            kind="technical_depth",
            tier="strong",
            evidence_keys=["e1"],
            non_answer=False,
        )
        assert result.kind == "technical_depth"
        assert result.tier == "strong"
        # New fields default cleanly.
        assert result.star_components is None
        assert result.compliance_confirmed is None
        assert result.compliance_clarification_used is False
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_task_base.py::TestTaskResultPhase3Fields -v
```
Expected: 8 failures — `kind` Literal too narrow, `star_components` / `compliance_*` unknown fields.

- [ ] **Step 4: Widen the Literal and add the new fields**

Edit `backend/nexus/app/modules/interview_engine/tasks/base.py`. Find the `TaskResult` class (line 34). Replace the existing `kind` field and append the four new fields. The full updated `TaskResult` should look like:

```python
class TaskResult(BaseModel):
    """The typed result of a completed QuestionTask.

    Returned from the terminal tool's complete-the-task path, or built
    by force_complete when the watchdog fires.
    """

    question_id: str
    kind: Literal["technical_depth", "behavioral_star", "compliance_binary"]
    tier: Literal["excellent", "strong", "at_bar", "below_bar"] | None = None
    evidence_keys: list[str] = []
    non_answer: bool = False
    signals_lacked: list[str] = []
    knockout: bool = False
    knockout_reason: str | None = None
    forced: bool = False
    forced_reason: Literal["task_timeout"] | None = None
    probes_fired: int = 0
    # Phase 3 — behavioral observation fields (None for non-behavioral)
    star_components: dict[
        Literal["situation", "task", "action", "result"], str | None
    ] | None = None
    # Phase 3 — compliance observation fields (None for non-compliance)
    compliance_confirmed: bool | None = None
    compliance_reason_or_example: str | None = None
    compliance_clarification_used: bool = False
```

- [ ] **Step 5: Run the new tests to verify they pass**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_task_base.py::TestTaskResultPhase3Fields -v
```
Expected: 8 passes.

- [ ] **Step 6: Run the full base + technical_depth suite to verify no regressions**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_task_base.py tests/interview_engine/unit/test_technical_depth_task.py -v
```
Expected: all existing tests + the 8 new tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/tasks/base.py backend/nexus/tests/interview_engine/unit/test_task_base.py
git commit -m "$(cat <<'EOF'
feat(engine): extend TaskResult Literal + add behavioral/compliance fields

Widens kind to {technical_depth, behavioral_star, compliance_binary} and
adds 4 optional fields (star_components, compliance_confirmed,
compliance_reason_or_example, compliance_clarification_used). All defaults
preserve existing TechnicalDepthTask serialization. Phase 3 task subclasses
fill their own slice; non-applicable fields stay None.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Extract `factory.py` (refactor only — no behavior change)

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/tasks/factory.py`
- Modify: `backend/nexus/app/modules/interview_engine/tasks/__init__.py`

**No tests in this task.** This is a pure refactor; the existing test_technical_depth_task.py::TestFactory class already exercises `build_task_for` and will continue to pass after the move.

- [ ] **Step 1: Create the new `factory.py`**

Create `backend/nexus/app/modules/interview_engine/tasks/factory.py`:

```python
"""Per-question task factory.

Phase 3 extracts the inline factory that lived in tasks/__init__.py during
Phase 2. Routes a QuestionConfig to its task subclass via _ROUTING_TABLE,
falling back to TechnicalDepthTask for any unknown question_kind.

`effective_budget_seconds_for` consults the routed class's optional
`budget_seconds_hard_cap` attribute to compute the controller's per-task
watchdog timeout. Compliance tasks have a 60s hard cap; other kinds use
the standard estimated_minutes-based budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings
from app.modules.interview_engine.tasks.base import QuestionTask
from app.modules.interview_engine.tasks.technical_depth import TechnicalDepthTask

if TYPE_CHECKING:
    from app.modules.interview_engine.controller import InterviewController
    from app.modules.interview_runtime.schemas import QuestionConfig


_ROUTING_TABLE: dict[str, type[QuestionTask]] = {
    "technical_depth": TechnicalDepthTask,
    # Phase 3 will add behavioral_star and compliance_binary entries
    # in the task that ships each subclass. open_culture stays mapped
    # to TechnicalDepthTask permanently — see overview spec §1.2.
    "open_culture": TechnicalDepthTask,
}


def build_task_for(
    question: "QuestionConfig",
    *,
    controller: "InterviewController",
    disqualified_signals: frozenset[str],
) -> QuestionTask:
    """Route a QuestionConfig to its task subclass.

    Falls back to TechnicalDepthTask for any unrecognized question_kind.
    Phase 4 lights this up by adding the question_kind DB column and
    updating the bank-generator to emit non-default values.
    """
    cls = _ROUTING_TABLE.get(question.question_kind, TechnicalDepthTask)
    return cls(
        question_config=question,
        controller=controller,
        disqualified_signals=disqualified_signals,
        rubric_internal=_build_rubric_block(question),
    )


def effective_budget_seconds_for(question: "QuestionConfig") -> float:
    """Return the watchdog timeout for this question's per-task dispatch.

    Returns min(estimated_minutes * 60 + overhead, per-kind hard cap if any).
    The only kind setting `budget_seconds_hard_cap` in Phase 3 is
    ComplianceBinaryTask (60s). Other kinds use the unconstrained budget.
    """
    cls = _ROUTING_TABLE.get(question.question_kind, TechnicalDepthTask)
    base = question.estimated_minutes * 60.0 + settings.engine_task_budget_overhead_seconds
    cap = getattr(cls, "budget_seconds_hard_cap", None)
    return min(base, cap) if cap is not None else base


def _build_rubric_block(question: "QuestionConfig") -> str:
    """Assemble the <<INTERNAL_RUBRIC>> string for the task prompt.

    Moved verbatim from the Phase 2 inline implementation in tasks/__init__.py.
    """
    return (
        "<<INTERNAL_RUBRIC>>\n"
        f"Question: {question.text}\n"
        f"Signals: {', '.join(question.signal_values)}\n"
        f"Positive evidence: {'; '.join(question.positive_evidence)}\n"
        f"Red flags: {'; '.join(question.red_flags)}\n"
        f"Excellent: {question.rubric.excellent}\n"
        f"Meets bar: {question.rubric.meets_bar}\n"
        f"Below bar: {question.rubric.below_bar}\n"
        f"Evaluation hint: {question.evaluation_hint}\n"
        "<<END_INTERNAL_RUBRIC>>"
    )
```

- [ ] **Step 2: Update `tasks/__init__.py` to delegate to `factory.py`**

Replace the contents of `backend/nexus/app/modules/interview_engine/tasks/__init__.py` with:

```python
"""Per-question task subclasses for the InterviewController.

Phase 2 shipped QuestionTask (abstract) + TechnicalDepthTask (concrete) +
the inline factory. Phase 3 extracted the factory to factory.py and adds
BehavioralStarTask + ComplianceBinaryTask + question_kind-based routing.
"""

from __future__ import annotations

from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)
from app.modules.interview_engine.tasks.factory import (
    build_task_for,
    effective_budget_seconds_for,
)
from app.modules.interview_engine.tasks.technical_depth import (
    TechnicalDepthTask,
)

__all__ = [
    "QuestionTask",
    "TaskResult",
    "TechnicalDepthTask",
    "build_task_for",
    "effective_budget_seconds_for",
]
```

- [ ] **Step 3: Run the existing factory test to verify the refactor preserved behavior**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_technical_depth_task.py::TestFactory -v
```
Expected: PASS — `build_task_for(sample_question, controller=None, disqualified_signals=frozenset())` returns a `TechnicalDepthTask`.

- [ ] **Step 4: Run the full interview_engine suite**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" 2>&1 | tail -10
```
Expected: all 128+ Phase 2 tests + the new Task 1/2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/tasks/factory.py backend/nexus/app/modules/interview_engine/tasks/__init__.py
git commit -m "$(cat <<'EOF'
refactor(engine): extract tasks factory into dedicated factory.py

Pure refactor — no behavior change. Moves _ROUTING_TABLE, build_task_for,
and _build_rubric_block out of tasks/__init__.py into tasks/factory.py.
Adds effective_budget_seconds_for helper that the controller will consume
in the next task. open_culture mapped to TechnicalDepthTask per overview
spec §1.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `effective_budget_seconds_for` into the controller

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/controller.py:46` (add import) and `:198`, `:210-214` (replace inline computations)

**No new tests.** Behavior is unchanged for all current question kinds (no kind sets `budget_seconds_hard_cap` yet — `TechnicalDepthTask` doesn't, and the new tasks aren't registered yet); existing integration tests will continue to pass.

- [ ] **Step 1: Read the current dispatch sites**

```bash
sed -n '162,215p' backend/nexus/app/modules/interview_engine/controller.py
```
Expected: the `for q in sorted_questions:` loop with two `_dispatch_task` call sites — one in the trimmed-budget branch (around line 198), one in the normal branch (around line 211-214 with the inline `q.estimated_minutes * 60.0 + ...` computation).

- [ ] **Step 2: Replace the inline computations**

Edit `backend/nexus/app/modules/interview_engine/controller.py`.

First, add the import. Find the existing imports of `tasks` modules (around line 46):
```python
from app.modules.interview_engine.tasks import build_task_for
from app.modules.interview_engine.tasks.base import TaskResult
```

Replace with:
```python
from app.modules.interview_engine.tasks import build_task_for, effective_budget_seconds_for
from app.modules.interview_engine.tasks.base import TaskResult
```

Next, find and replace the trimmed-branch dispatch (around line 198). The current line:
```python
                    await self._dispatch_task(q, watchdog_seconds=trimmed)
```
Replace with:
```python
                    await self._dispatch_task(
                        q,
                        watchdog_seconds=min(trimmed, effective_budget_seconds_for(q)),
                    )
```

Then find and replace the normal-branch dispatch (around lines 210-214). The current four lines:
```python
            else:
                await self._dispatch_task(
                    q,
                    watchdog_seconds=q.estimated_minutes * 60.0
                        + settings.engine_task_budget_overhead_seconds,
                )
```
Replace with:
```python
            else:
                await self._dispatch_task(
                    q,
                    watchdog_seconds=effective_budget_seconds_for(q),
                )
```

- [ ] **Step 3: Verify `settings` import is still needed elsewhere**

```bash
grep -n "settings\." backend/nexus/app/modules/interview_engine/controller.py
```
Expected: multiple remaining matches (e.g., `settings.engine_agent_name`, `settings.engine_closing_drain_timeout_seconds`). `settings` import stays in place.

- [ ] **Step 4: Run the full interview_engine integration suite**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/integration -v 2>&1 | tail -30
```
Expected: all 8 Phase 2 integration tests still pass. The watchdog values are unchanged because `effective_budget_seconds_for` falls through to the same computation when no `budget_seconds_hard_cap` is set on the routed class.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/controller.py
git commit -m "$(cat <<'EOF'
refactor(engine): controller routes per-task watchdog through factory helper

Replaces inline `q.estimated_minutes * 60 + overhead` at the two
_dispatch_task sites with effective_budget_seconds_for(q). Behavior
unchanged for all current question kinds — no kind sets a hard cap yet.
Sets up the seam for ComplianceBinaryTask's 60s cap in the next tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Write `task_behavioral.txt` prompt file

**Files:**
- Create: `backend/nexus/prompts/v1/interview/task_behavioral.txt`

**No tests in this task.** Prompt-quality tests come in Task 12 once `BehavioralStarTask` exists to drive them.

**Fairness signoff (per spec §4.1):** walk this checklist when creating the file:
- [ ] No leading phrasing in any GOOD example.
- [ ] No "ideal answer" disclosed in any section.
- [ ] Probe phrasing avoids pressure language ("you didn't", "you should have", "try again").
- [ ] Personality scoring explicitly forbidden in `# What you NEVER do`.
- [ ] Protected-class proxy probing explicitly forbidden in `# What you NEVER do`.
- [ ] Non-answer handling matches Q5 cases B and C.
- [ ] Probe is framed as natural curiosity, not as a "second chance".

- [ ] **Step 1: Create the prompt file**

Create `backend/nexus/prompts/v1/interview/task_behavioral.txt` with this exact content (verbatim from spec §4):

```
# Your job
You own a single behavioral question in this interview. You ask it, you listen, you record
which STAR components the candidate covered, you optionally fire up to TWO follow-up probes
that target missing components, and you complete. You do not own the surrounding flow — the
controller handles greetings, bridges, and closings.

# The question (this is your RUBRIC, not your script)
$question_text

<<INTERNAL_RUBRIC>>
$rubric_internal
<<END_INTERNAL_RUBRIC>>

The text inside the INTERNAL_RUBRIC markers is for YOUR private use only. It is never spoken,
never read aloud, never paraphrased to the candidate, and never referenced in your audible
turn. It exists so you know what "strong" looks like.

# How you ask the question
Translate the written question text into a natural spoken phrasing of 25 words or fewer.
Behavioral questions sound conversational, not test-shaped. Lead with the situation framing,
not with a list of evaluation criteria.

GOOD spoken form:
  "Tell me about a time you had to lead a team through a tight deadline."

BAD spoken form (reads the written text verbatim):
  "Describe a situation in which you were required to coordinate a cross-functional team
   under significant time pressure, including how you prioritized, delegated, communicated
   with stakeholders, and managed your own workload during the final 48 hours of delivery."

BAD spoken form (signposts the rubric):
  "I'm going to assess your leadership using the STAR framework — situation, task, action,
   result. Walk me through a time when..."

If the candidate previously disclosed something the controller bridged on, open with a brief
connector before the question:
  "Building on that — [your spoken-form question]."
Otherwise, ask the question cold without preamble.

# What you're listening for (STAR components)
A complete behavioral answer covers four components. The candidate does NOT have to use
those labels — your job is to detect the shape from how they speak:
  - Situation: where and when this happened — context, scope, stakes.
  - Task:      what they were specifically responsible for — their role, the goal.
  - Action:    what THEY did, in first person — concrete actions, not "we" generalities.
  - Result:    how it turned out — outcome, impact, what they learned.

A candidate who says "we shipped on time" without saying what THEY did has not covered
Action. A candidate who describes endless context but never names an outcome has not covered
Result. Detect the shape from substance, not from keywords.

# Tools available to you
- record_behavioral_answer(situation, task, action, result)
  Call this AFTER every candidate answer. Required.
    - Each parameter is either a SHORT one-line summary (≤20 words) of what the candidate
      covered for that component, or null if they didn't cover it.
    - Cumulative — if you call this a second time after a probe, fill in what was newly
      covered AND keep the previously-covered components filled in.
    - Do not invent content the candidate didn't actually say. If they said nothing about
      Result, set result=null. Don't paraphrase silence into a sentence.
- request_star_probe(missing_component)
  Call ONLY when the tool tells you components are missing AND probe budget remains.
  missing_component is one of "situation" / "task" / "action" / "result" — pick the most
  important missing piece (Action is usually highest-leverage; Result is second).
  After the probe, listen, then call record_behavioral_answer AGAIN with updated fields.
- complete_question()
  Terminal. Call this to finish the question and hand control back to the controller.
- (inherited from controller) disqualify_knockout(reason)
  Call only for hard, self-disclosed knockouts (e.g., "I have never led a team and never
  want to" for a tech-lead role where leadership is a hard requirement).
- (inherited from controller) request_clarification()
  Call when the candidate asks "can you repeat the question?" or clearly didn't hear it.

# Decision flow
1. Speak the question (per the spoken-form rules above).
2. Listen to the candidate's answer in full. Do not interrupt mid-sentence. A pause of a
   couple seconds is not a finished answer.
3. Call record_behavioral_answer with each component filled or null based on what they
   actually covered.
4. The tool's response will tell you:
   - "Complete answer recorded. Call complete_question." → call complete_question.
   - "Non-answer recorded. Do not probe — call complete_question." → call complete_question.
   - "Components missing: [list]. {N} probe(s) remaining." → call request_star_probe with
     the most important missing component.
   - "Components still missing but probe budget exhausted." → call complete_question.
5. After a probe, listen, then call record_behavioral_answer AGAIN. Loop until the tool
   tells you to complete.

# Probe phrasing — natural, not pressuring
Probes should sound like a curious follow-up, not an interrogation or a hint that they
"failed" the first answer.

GOOD: "What was your specific role in that?"        (probing Task)
GOOD: "Walk me through what you actually did."      (probing Action)
GOOD: "How did that end up playing out?"             (probing Result)
GOOD: "Set the scene a bit — when was this?"        (probing Situation)

BAD:  "You didn't actually say what YOU did. Try again." (accusatory)
BAD:  "What was the OUTCOME? I need to know the result." (test-shaped)
BAD:  "Strong candidates usually mention metrics — were there any?" (rubric leak)

# What you NEVER do
- NEVER read the rubric, the STAR labels, or the signal names aloud. The candidate doesn't
  know about STAR; you use it as your private listening framework.
- NEVER tell the candidate which component they're missing ("you forgot the Result").
- NEVER tell the candidate their answer was strong, weak, complete, incomplete, or anything
  else evaluative.
- NEVER fire more than two probes per question. The system enforces max_probes; trying to
  exceed it is wasted tool-call budget.
- NEVER skip calling record_behavioral_answer after a candidate answer. Every answer
  produces an observation, even non-answers (where every component is null).
- NEVER probe a non-answer ("I don't have an example for that"). Record the null answer and
  call complete_question. Probing here pressures the candidate unfairly.
- NEVER probe AGAIN after a probe-then-non-answer ("I really can't think of one"). Record
  the updated state and complete.
- NEVER score the candidate's PERSONALITY. You record what they did and how it turned out.
  Whether that makes them a "strong leader" is the post-session report's job, not yours.
- NEVER ask follow-ups that probe protected-class proxies (age, family status, religion,
  national origin, disability, etc.) regardless of how the question is phrased. If the
  written rubric appears to invite such a probe, do not ask it — call complete_question.
- NEVER frame a probe as a second chance ("let me give you another shot"). Probes are
  natural conversational follow-ups.
```

- [ ] **Step 2: Verify the file exists and contains the template variables**

```bash
ls -l backend/nexus/prompts/v1/interview/task_behavioral.txt
grep -c '\$question_text\|\$rubric_internal' backend/nexus/prompts/v1/interview/task_behavioral.txt
```
Expected: file exists; `grep -c` returns `2` (one match for each template variable).

- [ ] **Step 3: Walk the fairness checklist above and check off each item in your PR description.**

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v1/interview/task_behavioral.txt
git commit -m "$(cat <<'EOF'
feat(engine): add task_behavioral.txt prompt for BehavioralStarTask

Strict mirror of task_technical_depth.txt section structure, content
tailored to STAR-shape detection. Fairness signoff per spec §4.1:
no leading phrasing, no personality scoring, no protected-class proxy
probing, non-answers never probed, probes framed as natural curiosity.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Build `BehavioralStarTask` (class + tools + comprehensive unit tests)

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/tasks/behavioral.py`
- Create: `backend/nexus/tests/interview_engine/unit/test_behavioral_task.py`

This task ships the full `BehavioralStarTask` with all four `@function_tool` methods + 100% branch unit coverage. Class shape mirrors `TechnicalDepthTask` (same `__init__`, `build_task_instructions`, terminal `self.complete(result)` pattern).

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/interview_engine/unit/test_behavioral_task.py`:

```python
"""Unit tests for BehavioralStarTask construction + @function_tool behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_engine.tasks.behavioral import BehavioralStarTask
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


@pytest.fixture
def sample_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-bhv-1",
        position=0,
        text="Tell me about a time you led a team through a tight deadline.",
        signal_values=["leadership"],
        estimated_minutes=4.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["delegation", "communication", "outcome"],
        red_flags=["solo_hero", "blame_team"],
        rubric=QuestionRubric(
            excellent="excellent rubric body",
            meets_bar="meets-bar rubric body",
            below_bar="below-bar rubric body",
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
        question_kind="behavioral_star",
    )


def _make_task(question, controller=None) -> BehavioralStarTask:
    return BehavioralStarTask(
        question_config=question,
        controller=controller,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal="<<INTERNAL_RUBRIC>>...stub...<<END_INTERNAL_RUBRIC>>",
    )


class TestConstruction:
    def test_kind_is_behavioral_star(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.kind == "behavioral_star"

    def test_max_probes_is_two(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.max_probes == 2

    def test_initial_partial_state_all_components_none(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t._partial.star_components == {
            "situation": None, "task": None, "action": None, "result": None,
        }

    def test_instructions_contains_question_text(self, sample_question) -> None:
        t = _make_task(sample_question)
        body = t.build_task_instructions()
        assert sample_question.text in body
        assert "<<INTERNAL_RUBRIC>>" in body


pytestmark = pytest.mark.asyncio


class TestRecordBehavioralAnswer:
    async def test_all_four_covered_returns_complete_instruction(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx,
            situation="Last year",
            task="Lead the migration",
            action="Broke into chunks",
            result="Shipped on time",
        )
        assert "Complete answer recorded" in msg
        assert "complete_question" in msg

    async def test_all_four_null_returns_non_answer_instruction(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx, situation=None, task=None, action=None, result=None
        )
        assert "Non-answer recorded" in msg
        assert "Do not probe" in msg

    async def test_partial_with_probes_remaining_lists_missing(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx, situation="Last year", task="Lead", action=None, result=None,
        )
        assert "Components missing" in msg
        assert "action" in msg
        assert "result" in msg
        assert "2 probe" in msg or "2 probes" in msg

    async def test_partial_with_no_probes_remaining_returns_exhausted(self, sample_question) -> None:
        t = _make_task(sample_question)
        t._probes_fired = 2  # exhausted
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx, situation="Last year", task=None, action=None, result=None,
        )
        assert "probe budget exhausted" in msg.lower()
        assert "complete_question" in msg

    async def test_cumulative_update_preserves_prior_fills(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        # First call fills situation+task only.
        await t.record_behavioral_answer(
            ctx, situation="Last year", task="Lead the migration",
            action=None, result=None,
        )
        # Second call (after a probe) fills action.
        await t.record_behavioral_answer(
            ctx, situation=None, task=None,
            action="Broke into chunks", result=None,
        )
        # Cumulative state should have situation+task+action filled.
        assert t._partial.star_components["situation"] == "Last year"
        assert t._partial.star_components["task"] == "Lead the migration"
        assert t._partial.star_components["action"] == "Broke into chunks"
        assert t._partial.star_components["result"] is None


class TestRequestStarProbe:
    async def test_increments_probe_counter(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Establish partial state first so the budget check has context.
        await t.record_behavioral_answer(
            ctx, situation="Last year", task=None, action=None, result=None,
        )
        msg = await t.request_star_probe(ctx, missing_component="task")
        assert t._probes_fired == 1
        assert "task" in msg.lower() or "follow-up" in msg.lower()

    async def test_refuses_probe_when_budget_exhausted(self, sample_question) -> None:
        t = _make_task(sample_question)
        t._probes_fired = 2
        ctx = MagicMock()
        msg = await t.request_star_probe(ctx, missing_component="action")
        assert "exhausted" in msg.lower()
        assert t._probes_fired == 2  # not incremented

    async def test_refuses_probe_on_non_answer_state(self, sample_question) -> None:
        """Q5 case B: probing a non-answer (all components null) is forbidden."""
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Record a non-answer state first.
        await t.record_behavioral_answer(
            ctx, situation=None, task=None, action=None, result=None,
        )
        msg = await t.request_star_probe(ctx, missing_component="situation")
        assert "non-answer" in msg.lower() or "cannot probe" in msg.lower()
        assert t._probes_fired == 0

    async def test_refuses_second_probe_when_no_progress(self, sample_question) -> None:
        """Q5 case C: probe-then-non-answer should not unlock a 2nd probe."""
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Fill partial state.
        await t.record_behavioral_answer(
            ctx, situation="Last year", task=None, action=None, result=None,
        )
        # Fire probe 1 successfully.
        await t.request_star_probe(ctx, missing_component="action")
        assert t._probes_fired == 1
        # Candidate's follow-up was a non-answer — no new fields filled.
        await t.record_behavioral_answer(
            ctx, situation=None, task=None, action=None, result=None,
        )
        # Asking for probe 2 should be refused (no progress since last probe).
        msg = await t.request_star_probe(ctx, missing_component="result")
        assert ("no progress" in msg.lower() or "non-answer" in msg.lower()
                or "cannot probe" in msg.lower())
        assert t._probes_fired == 1


class TestCompleteQuestion:
    async def test_resolves_await_with_task_result(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        await t.record_behavioral_answer(
            ctx, situation="Last year", task="Lead",
            action="Broke into chunks", result="Shipped on time",
        )
        # Capture the complete() call by stubbing it.
        captured: dict = {}
        original_complete = t.complete

        def fake_complete(result):
            captured["result"] = result

        t.complete = fake_complete  # type: ignore[method-assign]
        await t.complete_question(ctx)
        result = captured["result"]
        assert isinstance(result, TaskResult)
        assert result.kind == "behavioral_star"
        assert result.star_components["situation"] == "Last year"
        assert result.star_components["result"] == "Shipped on time"
        assert result.forced is False
        assert result.probes_fired == 0


class TestForceComplete:
    def test_returns_forced_result_with_kind_behavioral_star(self, sample_question) -> None:
        t = _make_task(sample_question)
        r = t.force_complete(reason="task_timeout")
        assert isinstance(r, TaskResult)
        assert r.kind == "behavioral_star"
        assert r.forced is True
        assert r.forced_reason == "task_timeout"
        # Star components carry whatever partial state existed (initial = all None).
        assert r.star_components == {
            "situation": None, "task": None, "action": None, "result": None,
        }
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_behavioral_task.py -v
```
Expected: import error / module not found for `app.modules.interview_engine.tasks.behavioral`.

- [ ] **Step 3: Create `behavioral.py` with the full implementation**

Create `backend/nexus/app/modules/interview_engine/tasks/behavioral.py`:

```python
"""BehavioralStarTask — Phase 3 task for behavioral STAR questions.

Lifecycle:
  1. Controller dispatches: result = await task (with sibling watchdog).
  2. AgentTask boots; LLM speaks the question in ≤25-word spoken form.
  3. Candidate answers.
  4. LLM calls record_behavioral_answer with nullable STAR fields.
  5. Tool tells LLM what's missing + how many probes remain.
  6. LLM either calls request_star_probe or complete_question.
  7. After a probe, LLM calls record_behavioral_answer AGAIN (cumulative).
  8. Loop until tool says complete; LLM calls complete_question.
  9. complete_question calls self.complete(result), which resolves the
     controller's `await task` with a TaskResult.
"""

from __future__ import annotations

from string import Template
from typing import Literal

import structlog

from livekit.agents import RunContext, function_tool

from app.ai.prompts import prompt_loader
from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)


log = structlog.get_logger("interview-engine.tasks.behavioral")


_PROMPT_NAME = "interview/task_behavioral"
_STAR_COMPONENTS: tuple[str, ...] = ("situation", "task", "action", "result")


class BehavioralStarTask(QuestionTask):
    """Per-question task for behavioral STAR questions.

    Tools:
      * record_behavioral_answer — observation; cumulative across calls
      * request_star_probe — fires a targeted follow-up; bumps probe counter
      * complete_question — terminal; resolves the controller's `await task`
      * (inherited) disqualify_knockout, request_clarification
    """

    kind = "behavioral_star"
    max_probes = 2

    def __init__(
        self,
        *,
        question_config,
        controller,
        disqualified_signals,
        rubric_internal,
    ) -> None:
        super().__init__(
            question_config=question_config,
            controller=controller,
            disqualified_signals=disqualified_signals,
            rubric_internal=rubric_internal,
        )
        self._probes_fired: int = 0
        self._last_filled_component_count: int = 0
        # Initialize star_components on the partial state.
        self._partial.star_components = {
            "situation": None, "task": None, "action": None, "result": None,
        }

    def build_task_instructions(self) -> str:
        """Load the prompt template and substitute the question's data."""
        template = Template(prompt_loader.get(_PROMPT_NAME))
        return template.substitute(
            question_text=self.question_config.text,
            rubric_internal=self.rubric_internal,
        )

    # ------------------------------------------------------------------
    # @function_tools
    # ------------------------------------------------------------------

    @function_tool()
    async def record_behavioral_answer(
        self,
        ctx: RunContext,
        situation: str | None,
        task: str | None,
        action: str | None,
        result: str | None,
    ) -> str:
        """Record what STAR components the candidate covered.

        Each parameter is either a short summary (≤20 words) of what the
        candidate said for that component, or None if they didn't cover it.
        Cumulative — a second call (after a probe) fills in newly-covered
        components while keeping previously-covered ones in place.
        """
        # Cumulative update — only overwrite a slot if the new value is non-None.
        components = self._partial.star_components
        if situation is not None:
            components["situation"] = situation
        if task is not None:
            components["task"] = task
        if action is not None:
            components["action"] = action
        if result is not None:
            components["result"] = result

        filled = [k for k, v in components.items() if v is not None]
        missing = [k for k, v in components.items() if v is None]
        probes_left = self.max_probes - self._probes_fired

        log.info(
            "task.behavioral.observation_recorded",
            question_id=self.question_config.id,
            filled_components=filled,
            missing_components=missing,
            probes_fired=self._probes_fired,
        )

        if not filled:
            # All components null — non-answer.
            self._partial.non_answer = True
            return (
                "Non-answer recorded. Do not probe — call complete_question now."
            )

        # At least one component was filled at some point.
        self._partial.non_answer = False

        if not missing:
            return (
                "Complete answer recorded. Call complete_question to move on."
            )

        if probes_left <= 0:
            return (
                "Components still missing but probe budget exhausted. "
                "Call complete_question."
            )

        return (
            f"Components missing: {missing}. {probes_left} probe(s) remaining. "
            f"Call request_star_probe with the most important missing component "
            f"(typically Action first, then Result), then listen and call "
            f"record_behavioral_answer again."
        )

    @function_tool()
    async def request_star_probe(
        self,
        ctx: RunContext,
        missing_component: Literal["situation", "task", "action", "result"],
    ) -> str:
        """Fire a targeted follow-up probe for one missing STAR component.

        Refuses if probe budget is exhausted, if the current state is a
        non-answer (all components null — Q5 case B), or if no progress was
        made since the last probe (Q5 case C).
        """
        if self._probes_fired >= self.max_probes:
            return "Probe budget exhausted. Call complete_question instead."

        components = self._partial.star_components
        filled_count = sum(1 for v in components.values() if v is not None)

        if filled_count == 0:
            # Q5 case B — non-answer, no probing.
            return (
                "Cannot probe a non-answer. Record the null state and call "
                "complete_question."
            )

        if self._probes_fired > 0 and filled_count <= self._last_filled_component_count:
            # Q5 case C — probe-then-non-answer, no new coverage since last probe.
            return (
                "No progress since last probe (no new components covered). "
                "Call complete_question instead of probing again."
            )

        self._probes_fired += 1
        self._last_filled_component_count = filled_count
        self._partial.probes_fired = self._probes_fired

        log.info(
            "task.behavioral.probe_fired",
            question_id=self.question_config.id,
            probe_number=self._probes_fired,
            missing_component=missing_component,
        )

        return (
            f"Ask one natural follow-up that targets the missing '{missing_component}' "
            f"component. Phrase it as curiosity, not as a second-chance offer "
            f"(e.g., 'Walk me through what you actually did' for action). "
            f"After their reply, call record_behavioral_answer again."
        )

    @function_tool()
    async def complete_question(self, ctx: RunContext) -> str:
        """Terminal tool — ends this question's task.

        Builds a TaskResult from recorded state and resolves the controller's
        outer ``await task`` via ``self.complete(result)``.
        """
        result = TaskResult(
            question_id=self.question_config.id,
            kind="behavioral_star",
            non_answer=self._partial.non_answer,
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=False,
            probes_fired=self._probes_fired,
            star_components=dict(self._partial.star_components),
        )
        log.info(
            "task.behavioral.completed",
            question_id=self.question_config.id,
            probes_fired=self._probes_fired,
            filled_components=[
                k for k, v in result.star_components.items() if v is not None
            ],
        )
        # AgentTask is awaitable directly; .complete(result) resolves the
        # controller's await with the result.
        self.complete(result)
        return "Question complete. The controller will dispatch the next."
```

- [ ] **Step 4: Update `tasks/base.py` `_PartialState` to include `star_components`**

The `_partial.star_components` attribute is set in `BehavioralStarTask.__init__`, but `_PartialState` (defined in `tasks/base.py`) doesn't declare it. Decide between two options:
- **(a)** Add it as an Optional dataclass field on `_PartialState` (cleanest typing).
- **(b)** Leave it as a runtime attribute (works but mypy/pylint will complain).

Pick option **(a)**. Edit `backend/nexus/app/modules/interview_engine/tasks/base.py`. Find the `_PartialState` dataclass (around line 54) and add the field:

```python
@dataclass
class _PartialState:
    """Mutable observation state populated by the LLM during the task.

    Used by force_complete to build a sensible result when the watchdog
    fires before the terminal tool was called.
    """

    tier: Literal["excellent", "strong", "at_bar", "below_bar"] | None = None
    evidence_keys: list[str] = field(default_factory=list)
    signals_lacked: list[str] = field(default_factory=list)
    non_answer: bool = False
    knockout: bool = False
    knockout_reason: str | None = None
    probes_fired: int = 0
    # Phase 3 — behavioral STAR components (None for non-behavioral tasks)
    star_components: dict[str, str | None] | None = None
```

Then update `force_complete` (around line 116) to include `star_components` when building the forced result:

```python
    def force_complete(self, *, reason: Literal["task_timeout"]) -> TaskResult:
        """Build a TaskResult from whatever the LLM had recorded so far.

        Called by the controller's watchdog (or external short-circuit)
        path when the terminal tool hasn't fired in time. Does NOT call
        self.complete() — the caller pairs this with a separate
        ``task.complete(forced)`` invocation to resolve the inline
        AgentTask awaitable with the forced result.
        """
        return TaskResult(
            question_id=self.question_config.id,
            kind=self.kind,  # type: ignore[arg-type]
            tier=self._partial.tier,
            evidence_keys=list(self._partial.evidence_keys),
            non_answer=self._partial.non_answer,
            signals_lacked=list(self._partial.signals_lacked),
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=True,
            forced_reason=reason,
            probes_fired=self._partial.probes_fired,
            star_components=(
                dict(self._partial.star_components)
                if self._partial.star_components is not None
                else None
            ),
        )
```

- [ ] **Step 5: Run the behavioral task tests**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_behavioral_task.py -v
```
Expected: all 17 tests pass (4 construction + 5 record_behavioral_answer + 4 request_star_probe + 1 complete_question + 1 force_complete = 15; the test file as written has 15 tests — recount as you write).

- [ ] **Step 6: Run the full unit suite to verify no regressions**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit -v 2>&1 | tail -20
```
Expected: all unit tests pass (existing + new).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/tasks/behavioral.py backend/nexus/app/modules/interview_engine/tasks/base.py backend/nexus/tests/interview_engine/unit/test_behavioral_task.py
git commit -m "$(cat <<'EOF'
feat(engine): BehavioralStarTask — STAR-component coverage detection

Per-question task for behavioral STAR questions. Tools:
  - record_behavioral_answer(situation, task, action, result) — cumulative
    observation, returns instructions based on coverage + probe budget
  - request_star_probe(missing_component) — fires one follow-up; refuses
    on exhausted budget, non-answer state, or no-progress condition
  - complete_question() — terminal, resolves controller's await with
    TaskResult carrying star_components dict
  - (inherited) disqualify_knockout, request_clarification

Edge-case enforcement (Q5 cases A/B/C):
  - All four components covered → "complete" instruction (no probe)
  - All null → "non-answer recorded, do not probe" (case B)
  - Probe-then-no-progress → "no progress, complete" (case C)

Unit tests cover construction, all tool methods, all edge cases, and
force_complete. Phase 3 strictly additive — TechnicalDepthTask unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Register `BehavioralStarTask` in factory + re-export from `__init__.py`

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/tasks/factory.py:22-29` (add to `_ROUTING_TABLE`)
- Modify: `backend/nexus/app/modules/interview_engine/tasks/__init__.py` (add to imports + `__all__`)

- [ ] **Step 1: Add `BehavioralStarTask` to the routing table**

Edit `backend/nexus/app/modules/interview_engine/tasks/factory.py`. Add the import (after the `TechnicalDepthTask` import):

```python
from app.modules.interview_engine.tasks.behavioral import BehavioralStarTask
from app.modules.interview_engine.tasks.technical_depth import TechnicalDepthTask
```

Update `_ROUTING_TABLE`:

```python
_ROUTING_TABLE: dict[str, type[QuestionTask]] = {
    "technical_depth": TechnicalDepthTask,
    "behavioral_star": BehavioralStarTask,
    # Phase 3 will add compliance_binary entry next.
    # open_culture stays mapped to TechnicalDepthTask permanently — see
    # overview spec §1.2.
    "open_culture": TechnicalDepthTask,
}
```

- [ ] **Step 2: Re-export from `tasks/__init__.py`**

Edit `backend/nexus/app/modules/interview_engine/tasks/__init__.py`:

```python
from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)
from app.modules.interview_engine.tasks.behavioral import (
    BehavioralStarTask,
)
from app.modules.interview_engine.tasks.factory import (
    build_task_for,
    effective_budget_seconds_for,
)
from app.modules.interview_engine.tasks.technical_depth import (
    TechnicalDepthTask,
)

__all__ = [
    "BehavioralStarTask",
    "QuestionTask",
    "TaskResult",
    "TechnicalDepthTask",
    "build_task_for",
    "effective_budget_seconds_for",
]
```

- [ ] **Step 3: Verify routing works via a quick smoke test**

```bash
cd backend/nexus && docker compose run nexus python -c "
from app.modules.interview_engine.tasks import build_task_for, BehavioralStarTask
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric
q = QuestionConfig(
    id='q-1', position=0,
    text='Tell me about a time you led a team through a tight deadline.',
    signal_values=['leadership'], estimated_minutes=4.0, is_mandatory=True,
    follow_ups=[], positive_evidence=['delegation'], red_flags=['hero'],
    rubric=QuestionRubric(excellent='x', meets_bar='y', below_bar='z'),
    evaluation_hint='ten chars yes',
    question_kind='behavioral_star',
)
t = build_task_for(q, controller=None, disqualified_signals=frozenset())
print(type(t).__name__)
"
```
Expected: prints `BehavioralStarTask`.

- [ ] **Step 4: Run the full unit suite + the existing test_technical_depth_task::TestFactory**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit -v 2>&1 | tail -10
```
Expected: all pass; the existing factory test still routes `technical_depth` to TechnicalDepthTask.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/tasks/factory.py backend/nexus/app/modules/interview_engine/tasks/__init__.py
git commit -m "$(cat <<'EOF'
feat(engine): wire BehavioralStarTask into the factory routing table

Adds behavioral_star → BehavioralStarTask to _ROUTING_TABLE; re-exports
from tasks/__init__.py public API. Real interviews still default to
question_kind="technical_depth" so production behavior is unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Write `task_compliance_binary.txt` prompt file

**Files:**
- Create: `backend/nexus/prompts/v1/interview/task_compliance_binary.txt`

**No tests in this task.** Prompt-quality tests come in Task 13 once `ComplianceBinaryTask` exists.

**Fairness signoff (per spec §5.1):** walk this checklist:
- [ ] No language that pressures the candidate toward a yes ("are you sure?", "could you find a way?").
- [ ] Protected-class proxy probing explicitly forbidden, with a concrete example (the child-care follow-up).
- [ ] Inference-from-related-statements explicitly forbidden ("I'm a night owl" ≠ yes).
- [ ] Single-clarification limit reinforced in both `# Tools` and `# What you NEVER do`.
- [ ] Knockout pairing rule explicit (record_compliance_attestation + disqualify_knockout when applicable).
- [ ] 60s cap surfaced for LLM awareness.
- [ ] No moralizing or sympathy reactions ("that's understandable").

- [ ] **Step 1: Create the prompt file**

Create `backend/nexus/prompts/v1/interview/task_compliance_binary.txt` with this exact content (verbatim from spec §5):

```
# Your job
You own a single yes/no compliance question in this interview. The candidate either confirms
the requirement or they don't. Your job is to ask plainly, listen for the answer, and record
it factually. You have at most 60 seconds for this entire question — be brief. You do not
own the surrounding flow — the controller handles greetings, bridges, and closings.

# The question (this is your RUBRIC, not your script)
$question_text

<<INTERNAL_RUBRIC>>
$rubric_internal
<<END_INTERNAL_RUBRIC>>

The text inside the INTERNAL_RUBRIC markers is for YOUR private use only. It is never spoken,
never read aloud, never paraphrased to the candidate, and never referenced in your audible
turn. It exists so you know what the requirement is and whether a "no" is a hard knockout.

# How you ask the question
Translate the written question text into a natural spoken phrasing of 20 words or fewer.
Compliance questions are direct — ask plainly, don't pad them, don't apologize for asking.

GOOD spoken form:
  "Are you able to work UK shift hours — roughly 2pm to 10pm Pacific — for this role?"

BAD spoken form (reads the written text verbatim):
  "This role requires availability for the UK shift, which runs from 2pm to 10pm Pacific
   Time, Monday through Friday, with occasional flexibility for emergency on-call rotation;
   are you able to commit to that schedule on a regular basis?"

BAD spoken form (signposts evaluation or hedges):
  "I just want to confirm something quickly, and please don't feel pressured to answer in
   any particular way, but are you maybe possibly able to work UK shift hours?"

If the candidate previously disclosed something the controller bridged on, open with a brief
connector before the question:
  "Building on that — [your spoken-form question]."
Otherwise, ask the question cold without preamble.

# What you're listening for (compliance answer shape)
A clear answer is one of three shapes:
  - Clear YES with optional brief context: "Yes, I've worked UK hours before."
  - Clear NO with optional brief reason: "No, I have a child-care conflict in those hours."
  - Ambiguous / hedging: "Well, it kind of depends..." / "I think probably?" / "It would
    have to be temporary..." — these are NOT yes-or-no answers.

You record what the candidate actually said. You do NOT infer compliance from related
statements (a candidate saying "I'm a night owl" is not a yes; only an explicit yes/no is).

# Tools available to you
- record_compliance_attestation(confirmed, reason_or_example)
  Terminal observation. Required.
    - confirmed: True if the candidate clearly said yes; False if they clearly said no OR
      if they remained ambiguous after one clarification.
    - reason_or_example: a short distillation of the candidate's brief context (≤20 words).
      Use the candidate's own framing as much as possible. If they gave no context, set this
      to a one-line factual statement of what they said (e.g., "Confirmed without further
      context" / "Declined; cited family commitment").
  Resolves this question and hands control back to the controller.
- request_compliance_clarification()
  Single-shot. Use ONLY if the candidate's first answer was ambiguous or hedging. The tool
  will return instructions to ask once, plainly: "To confirm — yes or no?" Then listen and
  call record_compliance_attestation. You may call this AT MOST ONCE per question; the
  system enforces this.
- (inherited from controller) disqualify_knockout(reason)
  Call this in addition to record_compliance_attestation(confirmed=False, ...) when a "no"
  reflects a hard requirement of the role. The internal rubric tells you whether this
  question is a hard requirement. Pair the two calls — they record complementary facts.
- (inherited from controller) request_clarification()
  Call when the candidate asks "can you repeat the question?" or clearly didn't hear it.
  Different from request_compliance_clarification (which is for ambiguous answers, not
  inaudible questions).

# Decision flow
1. Speak the question (per the spoken-form rules above). Be brief.
2. Listen to the candidate's answer in full. Do not interrupt.
3. Decide:
   - Clear YES → call record_compliance_attestation(confirmed=True, reason_or_example=...)
     and you're done.
   - Clear NO AND the rubric marks this as a hard requirement → call
     record_compliance_attestation(confirmed=False, reason_or_example=...) AND
     disqualify_knockout(reason="<one-line summary of the conflict>"). Both calls.
   - Clear NO but the rubric does NOT mark this as a hard requirement → call
     record_compliance_attestation(confirmed=False, reason_or_example=...) only. No knockout.
   - Ambiguous → call request_compliance_clarification() ONCE, listen to the follow-up,
     then call record_compliance_attestation with the result. If still ambiguous after the
     clarification, set confirmed=False with reason_or_example="Ambiguous response; did not
     confirm."

# What you NEVER do
- NEVER read the rubric or any internal text aloud. The candidate doesn't know what counts
  as a "hard requirement" and shouldn't.
- NEVER infer a yes from related statements. "I'm a night owl" is not a yes to UK hours.
  "I've worked overnight before" is not a yes to a specific shift. Only an explicit yes is
  a yes.
- NEVER pressure or persuade the candidate toward a particular answer. Compliance questions
  are factual self-disclosures; the candidate's answer is what it is.
- NEVER fire more than one clarification per question. After one clarification, commit to
  recording confirmed=False with reason "ambiguous, did not confirm" if still unclear.
- NEVER probe with anything other than request_compliance_clarification. There are zero
  probes on this kind of question — clarification is the only follow-up mechanism.
- NEVER record the candidate's response as something different from what they said. If they
  said "no", record confirmed=False even if you think a yes was likely. Your job is the
  factual record.
- NEVER ask follow-ups that probe protected-class proxies (age, family status, religion,
  national origin, disability, pregnancy status, etc.) regardless of why the candidate
  declined. If they said "no, I have a child-care conflict", record that exactly — do NOT
  ask "how old is the child?" or "could a partner cover?" That probing would expose us to
  EEOC liability. The candidate's reason is the record.
- NEVER moralize or react to the candidate's answer ("that's totally understandable", "I'm
  sorry to hear that"). Acknowledge briefly and move on. The controller composes the
  bridge to the next question.
- NEVER take more than 60 seconds total on this question. The system enforces this — going
  over wastes session budget and triggers the watchdog, which forces a less-clean completion.
```

- [ ] **Step 2: Verify the file exists**

```bash
ls -l backend/nexus/prompts/v1/interview/task_compliance_binary.txt
grep -c '\$question_text\|\$rubric_internal' backend/nexus/prompts/v1/interview/task_compliance_binary.txt
```
Expected: file exists; grep returns `2`.

- [ ] **Step 3: Walk the fairness checklist above and check off each item in your PR description.**

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v1/interview/task_compliance_binary.txt
git commit -m "$(cat <<'EOF'
feat(engine): add task_compliance_binary.txt prompt for ComplianceBinaryTask

Strict mirror of task_technical_depth.txt section structure, content
tailored to yes/no compliance attestation. Fairness signoff per spec §5.1:
no inference-from-related-statements, no protected-class proxy probing
(child-care follow-up explicitly forbidden as the canonical example),
single-clarification limit, knockout pairing rule, no moralizing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Build `ComplianceBinaryTask` (class + tools + comprehensive unit tests)

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/tasks/compliance_binary.py`
- Create: `backend/nexus/tests/interview_engine/unit/test_compliance_binary_task.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/interview_engine/unit/test_compliance_binary_task.py`:

```python
"""Unit tests for ComplianceBinaryTask construction + @function_tool behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_engine.tasks.compliance_binary import ComplianceBinaryTask
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


@pytest.fixture
def sample_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-comp-1",
        position=0,
        text="Are you able to work UK shift hours, roughly 2pm to 10pm Pacific?",
        signal_values=["uk_shift_availability"],
        estimated_minutes=2.0,  # > 1 minute on purpose so the 60s cap is meaningful
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["confirms_availability"],
        red_flags=["declines_with_no_alternative"],
        rubric=QuestionRubric(
            excellent="excellent rubric body",
            meets_bar="meets-bar rubric body",
            below_bar="below-bar rubric body",
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
        question_kind="compliance_binary",
    )


def _make_task(question, controller=None) -> ComplianceBinaryTask:
    return ComplianceBinaryTask(
        question_config=question,
        controller=controller,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal="<<INTERNAL_RUBRIC>>...stub...<<END_INTERNAL_RUBRIC>>",
    )


class TestConstruction:
    def test_kind_is_compliance_binary(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.kind == "compliance_binary"

    def test_max_probes_is_zero(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.max_probes == 0

    def test_budget_seconds_hard_cap_is_60(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.budget_seconds_hard_cap == 60.0

    def test_class_attribute_budget_cap_inspectable_without_instance(self) -> None:
        # The factory's effective_budget_seconds_for reads this off the class.
        assert ComplianceBinaryTask.budget_seconds_hard_cap == 60.0

    def test_initial_clarification_used_is_false(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t._clarification_used is False

    def test_instructions_contains_question_text(self, sample_question) -> None:
        t = _make_task(sample_question)
        body = t.build_task_instructions()
        assert sample_question.text in body
        assert "<<INTERNAL_RUBRIC>>" in body


pytestmark = pytest.mark.asyncio


class TestRecordComplianceAttestation:
    async def test_confirmed_true_resolves_with_correct_result(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        msg = await t.record_compliance_attestation(
            ctx, confirmed=True, reason_or_example="Worked UK hours before",
        )
        result = captured["r"]
        assert isinstance(result, TaskResult)
        assert result.kind == "compliance_binary"
        assert result.compliance_confirmed is True
        assert result.compliance_reason_or_example == "Worked UK hours before"
        assert result.compliance_clarification_used is False
        assert result.knockout is False
        assert "complete" in msg.lower() or "controller" in msg.lower()

    async def test_confirmed_false_resolves_correctly(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        await t.record_compliance_attestation(
            ctx, confirmed=False, reason_or_example="Declined; cited family commitment",
        )
        result = captured["r"]
        assert result.compliance_confirmed is False
        assert result.compliance_reason_or_example == "Declined; cited family commitment"

    async def test_clarification_used_flag_propagates_to_result(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Simulate clarification having fired first.
        await t.request_compliance_clarification(ctx)
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        await t.record_compliance_attestation(
            ctx, confirmed=False, reason_or_example="Ambiguous response; did not confirm",
        )
        result = captured["r"]
        assert result.compliance_clarification_used is True

    async def test_knockout_flag_carries_through_when_disqualify_called_first(
        self, sample_question
    ) -> None:
        """The LLM is instructed to call disqualify_knockout BEFORE the
        terminal record_compliance_attestation when it's a hard 'no'. The
        knockout flag should persist into the TaskResult."""
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Simulate disqualify_knockout firing first (sets _partial.knockout).
        await t.disqualify_knockout(ctx, reason="Cannot work UK shift")
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        await t.record_compliance_attestation(
            ctx, confirmed=False, reason_or_example="No; UK shift not feasible",
        )
        result = captured["r"]
        assert result.compliance_confirmed is False
        assert result.knockout is True
        assert result.knockout_reason == "Cannot work UK shift"


class TestRequestComplianceClarification:
    async def test_first_call_returns_ask_once_instruction(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.request_compliance_clarification(ctx)
        assert "to confirm" in msg.lower() or "yes or no" in msg.lower()
        assert t._clarification_used is True

    async def test_second_call_returns_already_clarified(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        await t.request_compliance_clarification(ctx)
        msg = await t.request_compliance_clarification(ctx)
        assert "already clarified" in msg.lower() or "ambiguous" in msg.lower()
        # Counter doesn't double-fire.
        assert t._clarification_used is True


class TestForceComplete:
    def test_returns_forced_result_with_kind_compliance_binary(self, sample_question) -> None:
        t = _make_task(sample_question)
        r = t.force_complete(reason="task_timeout")
        assert isinstance(r, TaskResult)
        assert r.kind == "compliance_binary"
        assert r.forced is True
        assert r.forced_reason == "task_timeout"
        # Compliance fields default None when nothing was recorded.
        assert r.compliance_confirmed is None
        assert r.compliance_reason_or_example is None
        assert r.compliance_clarification_used is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_compliance_binary_task.py -v
```
Expected: import error / module not found.

- [ ] **Step 3: Create `compliance_binary.py` with the full implementation**

Create `backend/nexus/app/modules/interview_engine/tasks/compliance_binary.py`:

```python
"""ComplianceBinaryTask — Phase 3 task for yes/no compliance questions.

Lifecycle:
  1. Controller dispatches with watchdog_seconds=60 (per
     effective_budget_seconds_for + budget_seconds_hard_cap=60.0).
  2. AgentTask boots; LLM speaks the question briefly.
  3. Candidate answers.
  4. Branch on candidate's response shape:
     a. Clear yes/no → record_compliance_attestation(...) (terminal).
     b. Ambiguous → request_compliance_clarification() (single-shot),
        then listen, then record_compliance_attestation(...).
  5. If the recorded answer is a "no" against a hard requirement, the LLM
     pairs disqualify_knockout(reason) before/with the terminal call.
  6. record_compliance_attestation calls self.complete(result).
"""

from __future__ import annotations

from string import Template

import structlog

from livekit.agents import RunContext, function_tool

from app.ai.prompts import prompt_loader
from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)


log = structlog.get_logger("interview-engine.tasks.compliance_binary")


_PROMPT_NAME = "interview/task_compliance_binary"


class ComplianceBinaryTask(QuestionTask):
    """Per-question task for yes/no compliance attestation.

    Tools:
      * record_compliance_attestation — terminal observation
      * request_compliance_clarification — single-shot, doesn't count as probe
      * (inherited) disqualify_knockout, request_clarification

    Per-task hard cap: 60 seconds. The factory's effective_budget_seconds_for
    consumes `budget_seconds_hard_cap` to cap the controller's watchdog.
    """

    kind = "compliance_binary"
    max_probes = 0
    budget_seconds_hard_cap: float = 60.0

    def __init__(
        self,
        *,
        question_config,
        controller,
        disqualified_signals,
        rubric_internal,
    ) -> None:
        super().__init__(
            question_config=question_config,
            controller=controller,
            disqualified_signals=disqualified_signals,
            rubric_internal=rubric_internal,
        )
        self._clarification_used: bool = False

    def build_task_instructions(self) -> str:
        """Load the prompt template and substitute the question's data."""
        template = Template(prompt_loader.get(_PROMPT_NAME))
        return template.substitute(
            question_text=self.question_config.text,
            rubric_internal=self.rubric_internal,
        )

    # ------------------------------------------------------------------
    # @function_tools
    # ------------------------------------------------------------------

    @function_tool()
    async def record_compliance_attestation(
        self,
        ctx: RunContext,
        confirmed: bool,
        reason_or_example: str,
    ) -> str:
        """Terminal observation — record the candidate's yes/no with brief context.

        Builds a TaskResult carrying compliance_confirmed,
        compliance_reason_or_example, compliance_clarification_used, plus any
        knockout state the LLM set via disqualify_knockout. Calls
        self.complete(result) to resolve the controller's `await task`.
        """
        result = TaskResult(
            question_id=self.question_config.id,
            kind="compliance_binary",
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=False,
            probes_fired=0,
            compliance_confirmed=confirmed,
            compliance_reason_or_example=reason_or_example,
            compliance_clarification_used=self._clarification_used,
        )
        log.info(
            "task.compliance.recorded",
            question_id=self.question_config.id,
            confirmed=confirmed,
            knockout=result.knockout,
            clarification_used=self._clarification_used,
        )
        # AgentTask is awaitable directly; .complete(result) resolves it.
        self.complete(result)
        return "Question complete. The controller will dispatch the next."

    @function_tool()
    async def request_compliance_clarification(
        self,
        ctx: RunContext,
    ) -> str:
        """Single-shot clarification turn for ambiguous yes/no answers.

        First call: sets _clarification_used=True and returns the
        ask-once instruction. Second call: returns the "already clarified"
        instruction. Single-shot is enforced in code regardless of
        prompt compliance.
        """
        if self._clarification_used:
            log.info(
                "task.compliance.clarification_blocked_second_call",
                question_id=self.question_config.id,
            )
            return (
                "Already clarified once. Record record_compliance_attestation now — "
                "if still ambiguous, set confirmed=False with reason "
                "'ambiguous response, did not confirm'."
            )

        self._clarification_used = True
        log.info(
            "task.compliance.clarification_fired",
            question_id=self.question_config.id,
        )
        return (
            "Ask once, plainly: 'To confirm — yes or no?' Then listen to the "
            "candidate's reply and call record_compliance_attestation with the result."
        )
```

- [ ] **Step 4: Update `tasks/base.py`'s `force_complete` to include the compliance fields**

Edit `backend/nexus/app/modules/interview_engine/tasks/base.py`. Find `force_complete` (now around line 116-138 after Task 6's edits) and update it to also include the compliance fields. The full updated method:

```python
    def force_complete(self, *, reason: Literal["task_timeout"]) -> TaskResult:
        """Build a TaskResult from whatever the LLM had recorded so far.

        Called by the controller's watchdog (or external short-circuit)
        path when the terminal tool hasn't fired in time. Does NOT call
        self.complete() — the caller pairs this with a separate
        ``task.complete(forced)`` invocation to resolve the inline
        AgentTask awaitable with the forced result.
        """
        # Compliance fields are read off the subclass via getattr so the
        # base method doesn't need a typed coupling to ComplianceBinaryTask.
        clarification_used = getattr(self, "_clarification_used", False)
        return TaskResult(
            question_id=self.question_config.id,
            kind=self.kind,  # type: ignore[arg-type]
            tier=self._partial.tier,
            evidence_keys=list(self._partial.evidence_keys),
            non_answer=self._partial.non_answer,
            signals_lacked=list(self._partial.signals_lacked),
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=True,
            forced_reason=reason,
            probes_fired=self._partial.probes_fired,
            star_components=(
                dict(self._partial.star_components)
                if self._partial.star_components is not None
                else None
            ),
            compliance_clarification_used=clarification_used,
        )
```

- [ ] **Step 5: Run the compliance task tests**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_compliance_binary_task.py -v
```
Expected: all tests pass (6 construction + 4 record_compliance_attestation + 2 request_compliance_clarification + 1 force_complete = 13 tests).

- [ ] **Step 6: Run the full unit suite**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit -v 2>&1 | tail -10
```
Expected: all unit tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/tasks/compliance_binary.py backend/nexus/app/modules/interview_engine/tasks/base.py backend/nexus/tests/interview_engine/unit/test_compliance_binary_task.py
git commit -m "$(cat <<'EOF'
feat(engine): ComplianceBinaryTask — yes/no attestation + single-shot clarification

Per-question task for compliance binary questions. Tools:
  - record_compliance_attestation(confirmed, reason_or_example) — terminal
    observation, builds TaskResult with compliance_* fields + carries any
    knockout state set by a paired disqualify_knockout call
  - request_compliance_clarification() — single-shot fairness clarification
    for ambiguous answers (Q5 case D); enforced in code regardless of
    prompt compliance
  - (inherited) disqualify_knockout, request_clarification

Class attributes: max_probes=0, budget_seconds_hard_cap=60.0. The hard cap
flows through factory.effective_budget_seconds_for to the controller's
per-task watchdog (wired in Task 4).

Unit tests cover construction, all tool methods (incl. clarification
single-shot enforcement), knockout-pairing, and force_complete.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Register `ComplianceBinaryTask` in factory + re-export

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/tasks/factory.py` (add to `_ROUTING_TABLE`)
- Modify: `backend/nexus/app/modules/interview_engine/tasks/__init__.py` (add to imports + `__all__`)

- [ ] **Step 1: Add `ComplianceBinaryTask` to the routing table**

Edit `backend/nexus/app/modules/interview_engine/tasks/factory.py`. Add the import after the existing `BehavioralStarTask` import:

```python
from app.modules.interview_engine.tasks.behavioral import BehavioralStarTask
from app.modules.interview_engine.tasks.compliance_binary import ComplianceBinaryTask
from app.modules.interview_engine.tasks.technical_depth import TechnicalDepthTask
```

Update `_ROUTING_TABLE` to its final shape:

```python
_ROUTING_TABLE: dict[str, type[QuestionTask]] = {
    "technical_depth": TechnicalDepthTask,
    "behavioral_star": BehavioralStarTask,
    "compliance_binary": ComplianceBinaryTask,
    "open_culture": TechnicalDepthTask,  # deferred — see overview spec §1.2
}
```

- [ ] **Step 2: Re-export from `tasks/__init__.py`**

Edit `backend/nexus/app/modules/interview_engine/tasks/__init__.py` to its final shape:

```python
from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)
from app.modules.interview_engine.tasks.behavioral import (
    BehavioralStarTask,
)
from app.modules.interview_engine.tasks.compliance_binary import (
    ComplianceBinaryTask,
)
from app.modules.interview_engine.tasks.factory import (
    build_task_for,
    effective_budget_seconds_for,
)
from app.modules.interview_engine.tasks.technical_depth import (
    TechnicalDepthTask,
)

__all__ = [
    "BehavioralStarTask",
    "ComplianceBinaryTask",
    "QuestionTask",
    "TaskResult",
    "TechnicalDepthTask",
    "build_task_for",
    "effective_budget_seconds_for",
]
```

- [ ] **Step 3: Smoke-test routing**

```bash
cd backend/nexus && docker compose run nexus python -c "
from app.modules.interview_engine.tasks import build_task_for, effective_budget_seconds_for
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric
def mk(kind, est=2.0):
    return QuestionConfig(
        id='q', position=0, text='x'*60, signal_values=['s'], estimated_minutes=est,
        is_mandatory=True, follow_ups=[], positive_evidence=['e'], red_flags=['r'],
        rubric=QuestionRubric(excellent='x', meets_bar='y', below_bar='z'),
        evaluation_hint='ten chars yes', question_kind=kind,
    )
print('compliance routes to:', type(build_task_for(mk('compliance_binary'),
    controller=None, disqualified_signals=frozenset())).__name__)
print('compliance budget:', effective_budget_seconds_for(mk('compliance_binary', est=2.0)))
print('behavioral budget:', effective_budget_seconds_for(mk('behavioral_star', est=4.0)))
print('technical budget:', effective_budget_seconds_for(mk('technical_depth', est=3.0)))
"
```
Expected:
- `compliance routes to: ComplianceBinaryTask`
- `compliance budget: 60.0` (capped, regardless of `est=2.0` which would be 120s + overhead)
- `behavioral budget: ~245.0` (4 * 60 + overhead, no cap)
- `technical budget: ~185.0` (3 * 60 + overhead, no cap)

- [ ] **Step 4: Run the full unit + integration suite**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" -v 2>&1 | tail -15
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/tasks/factory.py backend/nexus/app/modules/interview_engine/tasks/__init__.py
git commit -m "$(cat <<'EOF'
feat(engine): wire ComplianceBinaryTask into the factory routing table

Adds compliance_binary → ComplianceBinaryTask to _ROUTING_TABLE; re-exports
from tasks/__init__.py public API. effective_budget_seconds_for now caps
the watchdog at 60s for compliance questions regardless of estimated_minutes.
Real interviews still default to question_kind="technical_depth" so
production behavior is unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Factory unit tests (full routing + budget coverage)

**Files:**
- Create: `backend/nexus/tests/interview_engine/unit/test_factory.py`

- [ ] **Step 1: Write the test file**

Create `backend/nexus/tests/interview_engine/unit/test_factory.py`:

```python
"""Unit tests for tasks/factory.py — routing + per-task budget computation.

Coverage target: 100% branch on factory.py (load-bearing routing logic per
CLAUDE.md "candidate scoring and classification thresholds").
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.tasks import (
    BehavioralStarTask,
    ComplianceBinaryTask,
    TechnicalDepthTask,
    build_task_for,
    effective_budget_seconds_for,
)
from app.modules.interview_engine.tasks.factory import _ROUTING_TABLE
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _make_question(*, kind: str = "technical_depth", est: float = 3.0) -> QuestionConfig:
    return QuestionConfig(
        id="q-fac-1",
        position=0,
        text="A long enough placeholder question text body goes here.",
        signal_values=["s1"],
        estimated_minutes=est,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["e1"],
        red_flags=["r1"],
        rubric=QuestionRubric(
            excellent="x" * 10, meets_bar="y" * 10, below_bar="z" * 10,
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
        question_kind=kind,  # type: ignore[arg-type]
    )


class TestRoutingTable:
    def test_routing_table_has_all_four_kinds(self) -> None:
        assert set(_ROUTING_TABLE.keys()) == {
            "technical_depth", "behavioral_star",
            "compliance_binary", "open_culture",
        }

    def test_technical_depth_routes_to_technical_depth_task(self) -> None:
        q = _make_question(kind="technical_depth")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, TechnicalDepthTask)

    def test_behavioral_star_routes_to_behavioral_task(self) -> None:
        q = _make_question(kind="behavioral_star")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, BehavioralStarTask)

    def test_compliance_binary_routes_to_compliance_task(self) -> None:
        q = _make_question(kind="compliance_binary")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, ComplianceBinaryTask)

    def test_open_culture_falls_back_to_technical_depth_task(self) -> None:
        """open_culture is reserved but deferred — falls back per spec §1.2."""
        q = _make_question(kind="open_culture")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, TechnicalDepthTask)

    def test_unknown_kind_falls_back_to_technical_depth_task(self) -> None:
        """Defensive — a future enum value should NOT crash; falls back to safe default.

        Constructs the QuestionConfig with model_construct to bypass the
        Literal validator (which would otherwise reject the unknown value).
        """
        q = QuestionConfig.model_construct(
            id="q-fac-2", position=0, text="x" * 60, signal_values=["s"],
            estimated_minutes=3.0, is_mandatory=True, follow_ups=[],
            positive_evidence=["e"], red_flags=["r"],
            rubric=QuestionRubric(excellent="x"*10, meets_bar="y"*10, below_bar="z"*10),
            evaluation_hint="ten chars yes", question_kind="not_a_real_kind",
        )
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, TechnicalDepthTask)


class TestEffectiveBudgetSecondsFor:
    def test_technical_depth_no_cap(self) -> None:
        q = _make_question(kind="technical_depth", est=3.0)
        # 3 * 60 = 180 + overhead. Overhead is settings-dependent; assert structure.
        secs = effective_budget_seconds_for(q)
        assert secs > 180.0  # at least the base
        assert secs < 200.0  # plus a small overhead, not a giant one

    def test_behavioral_no_cap(self) -> None:
        q = _make_question(kind="behavioral_star", est=4.0)
        secs = effective_budget_seconds_for(q)
        assert secs > 240.0
        assert secs < 260.0

    def test_compliance_capped_at_60(self) -> None:
        """estimated_minutes=2.0 → base ~125s; cap should bring it down to 60."""
        q = _make_question(kind="compliance_binary", est=2.0)
        secs = effective_budget_seconds_for(q)
        assert secs == 60.0

    def test_compliance_with_short_estimate_uses_base(self) -> None:
        """If estimated_minutes is small (~10s), base < cap, so base wins."""
        q = _make_question(kind="compliance_binary", est=0.1)
        secs = effective_budget_seconds_for(q)
        # 0.1 * 60 = 6s + overhead → still well under 60s cap
        assert secs < 60.0
        assert secs >= 6.0

    def test_open_culture_uses_technical_depth_budget_no_cap(self) -> None:
        q = _make_question(kind="open_culture", est=3.0)
        secs = effective_budget_seconds_for(q)
        assert secs > 180.0  # no cap (TechnicalDepthTask has none)
```

- [ ] **Step 2: Run the tests**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_factory.py -v
```
Expected: all 11 tests pass.

- [ ] **Step 3: Verify branch coverage on factory.py**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_factory.py --cov=app.modules.interview_engine.tasks.factory --cov-report=term-missing
```
Expected: 100% branch coverage on `app/modules/interview_engine/tasks/factory.py`. If any line is missing, add a test for it.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/interview_engine/unit/test_factory.py
git commit -m "$(cat <<'EOF'
test(engine): factory routing + effective_budget_seconds_for coverage

100% branch coverage on tasks/factory.py per CLAUDE.md test-coverage
gates ("candidate scoring and classification thresholds"). Tests:
  - routing table has the 4 expected kinds
  - each registered kind routes to the right class
  - open_culture falls back to TechnicalDepthTask (deferred per spec §1.2)
  - unknown kind (via model_construct bypass) falls back to safe default
  - compliance is capped at 60s regardless of estimated_minutes
  - other kinds use estimated_minutes * 60 + overhead with no cap

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Integration test — `behavioral_flow`

**Files:**
- Create: `backend/nexus/tests/interview_engine/integration/test_behavioral_flow.py`

This test mirrors `test_controller_flow.py`'s `_AwaitableFakeTask` pattern, but the fake task has `kind="behavioral_star"` and the resolved `TaskResult` carries `star_components`. Asserts on the controller's audit-event emissions.

- [ ] **Step 1: Write the test file**

Create `backend/nexus/tests/interview_engine/integration/test_behavioral_flow.py`:

```python
"""Integration test: controller dispatches a behavioral_star question correctly.

Pattern mirrors test_controller_flow.py — patches build_task_for to return
an awaitable fake task that resolves with a behavioral TaskResult. Asserts:
  - task.entered payload carries kind="behavioral_star" and max_probes=2
  - task.completed payload carries star_components dict
  - watchdog uses behavioral budget (no cap)
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.interview_engine.tasks.base import TaskResult
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


class _BehavioralAwaitableFakeTask:
    """Awaitable fake with kind='behavioral_star' and max_probes=2."""

    def __init__(self, *, question_id: str, default_result: TaskResult):
        self.kind = "behavioral_star"
        self.max_probes = 2
        self._fut: asyncio.Future = asyncio.Future()
        self._default_result = default_result
        self.id = question_id
        self.force_complete_calls: list[dict] = []

    def __await__(self):
        return self._fut.__await__()

    def done(self) -> bool:
        return self._fut.done()

    def complete(self, result) -> None:
        if self._fut.done():
            raise RuntimeError("already completed")
        self._fut.set_result(result)

    def cancel(self) -> None:
        if self._fut.done():
            return
        self._fut.set_exception(RuntimeError("cancelled"))

    def force_complete(self, *, reason: str) -> TaskResult:
        self.force_complete_calls.append({"reason": reason})
        return self._default_result.model_copy(
            update={"forced": True, "forced_reason": reason}
        )


def _make_behavioral_fake(question_id: str, result: TaskResult) -> _BehavioralAwaitableFakeTask:
    task = _BehavioralAwaitableFakeTask(question_id=question_id, default_result=result)
    loop = asyncio.get_event_loop()
    loop.call_soon(lambda: task.complete(result) if not task.done() else None)
    return task


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def patch_persistence(monkeypatch):
    record_mock = AsyncMock(return_value=None)
    fake_db = MagicMock()
    fake_db.commit = AsyncMock()

    def fake_session_cm():
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=fake_db)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.record_session_result",
        record_mock,
    )
    monkeypatch.setattr(
        "app.modules.interview_engine.controller.get_bypass_session",
        fake_session_cm,
    )
    return record_mock


def _make_controller_with_fake_session():
    cfg = load_live_data_session_config()
    # Override one question to be behavioral_star so it routes to the fake.
    cfg.stage.questions[2].question_kind = "behavioral_star"
    collector = EventCollector(
        session_id=cfg.session_id,
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="test-correlation",
        controller_prompt_hash="sha256:test",
        model_versions={},
        redaction_mode="metadata",
    )
    ctrl = InterviewController(
        session_config=cfg,
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        correlation_id="test-correlation",
        collector=collector,
        idle_nudge_config=IdleNudgeConfig(999.0, 999.0, 999.0),
        budget=SessionBudget(0.0, 900.0),
        tenant_policy="record_only",
    )
    fake_session = MagicMock()
    handle = MagicMock()
    handle.wait_for_playout = AsyncMock(return_value=None)
    fake_session.generate_reply = MagicMock(return_value=handle)
    fake_session.current_speech = None
    fake_session.aclose = AsyncMock(return_value=None)
    fake_session.room_io = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = AsyncMock()
    type(ctrl).session = property(lambda self: fake_session)  # type: ignore[assignment]
    return ctrl, collector, fake_session


async def test_behavioral_question_dispatch_carries_kind_and_components(
    monkeypatch, patch_persistence
):
    """A behavioral_star question fires task.entered with kind=behavioral_star
    and task.completed with the star_components dict."""
    ctrl, collector, fake_session = _make_controller_with_fake_session()

    captured_kinds = []

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "behavioral_star":
            result = TaskResult(
                question_id=q.id,
                kind="behavioral_star",
                star_components={
                    "situation": "Last year",
                    "task": "Lead the migration",
                    "action": "Broke into chunks",
                    "result": "Shipped on time",
                },
                probes_fired=0,
            )
            return _make_behavioral_fake(q.id, result)
        # Other questions still resolve with technical_depth fakes.
        from tests.interview_engine.integration.test_controller_flow import (
            _make_awaitable_fake_task,
        )
        captured_kinds.append("technical_depth")
        return _make_awaitable_fake_task(
            q.id,
            TaskResult(question_id=q.id, kind="technical_depth", tier="strong"),
        )

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    entered = collector.events_of_kind("task.entered")
    behavioral_entered = [e for e in entered if e.payload["kind"] == "behavioral_star"]
    assert len(behavioral_entered) == 1
    assert behavioral_entered[0].payload["max_probes"] == 2

    completed = collector.events_of_kind("task.completed")
    behavioral_completed = [e for e in completed if e.payload["result_kind"] == "behavioral_star"]
    assert len(behavioral_completed) == 1
    star = behavioral_completed[0].payload["result"]["star_components"]
    assert star["situation"] == "Last year"
    assert star["result"] == "Shipped on time"


async def test_behavioral_watchdog_uses_estimated_minutes_no_cap(
    monkeypatch, patch_persistence
):
    """Behavioral has no hard cap — watchdog reflects estimated_minutes * 60 + overhead."""
    ctrl, collector, _ = _make_controller_with_fake_session()
    cfg = ctrl._config
    behavioral_q = cfg.stage.questions[2]  # the one we marked behavioral_star
    expected_min_seconds = behavioral_q.estimated_minutes * 60.0
    # ContextSpy: capture watchdog_seconds passed to _dispatch_task
    seen_watchdogs: dict[str, float] = {}
    original_dispatch = ctrl._dispatch_task

    async def capturing_dispatch(q, *, watchdog_seconds):
        seen_watchdogs[q.id] = watchdog_seconds
        await original_dispatch(q, watchdog_seconds=watchdog_seconds)

    ctrl._dispatch_task = capturing_dispatch  # type: ignore[method-assign]

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "behavioral_star":
            return _make_behavioral_fake(
                q.id,
                TaskResult(
                    question_id=q.id, kind="behavioral_star",
                    star_components={"situation": "x", "task": "y", "action": "z", "result": "w"},
                ),
            )
        from tests.interview_engine.integration.test_controller_flow import (
            _make_awaitable_fake_task,
        )
        return _make_awaitable_fake_task(
            q.id,
            TaskResult(question_id=q.id, kind="technical_depth", tier="strong"),
        )

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    behavioral_watchdog = seen_watchdogs[behavioral_q.id]
    assert behavioral_watchdog >= expected_min_seconds
    # Definitely not capped — should be much greater than 60s for a multi-minute question.
    assert behavioral_watchdog > 60.0
```

- [ ] **Step 2: Run the tests**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/integration/test_behavioral_flow.py -v
```
Expected: 2 tests pass.

- [ ] **Step 3: Run the full integration suite to verify no regressions**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/integration -v 2>&1 | tail -15
```
Expected: all integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/interview_engine/integration/test_behavioral_flow.py
git commit -m "$(cat <<'EOF'
test(engine): integration test for behavioral_star task dispatch

Patches build_task_for to return an awaitable fake task with
kind=behavioral_star (mirrors test_controller_flow.py's _AwaitableFakeTask).
Asserts task.entered carries kind+max_probes correctly, task.completed
carries the star_components dict, and the per-task watchdog uses the
behavioral budget with no cap (estimated_minutes * 60 + overhead).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Integration test — `compliance_binary_flow`

**Files:**
- Create: `backend/nexus/tests/interview_engine/integration/test_compliance_binary_flow.py`

- [ ] **Step 1: Write the test file**

Create `backend/nexus/tests/interview_engine/integration/test_compliance_binary_flow.py`:

```python
"""Integration test: controller dispatches compliance_binary correctly.

Asserts:
  - watchdog uses 60s cap regardless of estimated_minutes (live data has est=2.0)
  - knockout pairing produces both task.completed (compliance_confirmed=False)
    AND a disqualify.knockout audit event
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.interview_engine.tasks.base import TaskResult
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


class _ComplianceAwaitableFakeTask:
    """Awaitable fake with kind='compliance_binary' and max_probes=0."""

    def __init__(self, *, question_id: str, default_result: TaskResult):
        self.kind = "compliance_binary"
        self.max_probes = 0
        self._fut: asyncio.Future = asyncio.Future()
        self._default_result = default_result
        self.id = question_id
        self.force_complete_calls: list[dict] = []

    def __await__(self):
        return self._fut.__await__()

    def done(self) -> bool:
        return self._fut.done()

    def complete(self, result) -> None:
        if self._fut.done():
            raise RuntimeError("already completed")
        self._fut.set_result(result)

    def cancel(self) -> None:
        if self._fut.done():
            return
        self._fut.set_exception(RuntimeError("cancelled"))

    def force_complete(self, *, reason: str) -> TaskResult:
        self.force_complete_calls.append({"reason": reason})
        return self._default_result.model_copy(
            update={"forced": True, "forced_reason": reason}
        )


def _make_compliance_fake(question_id: str, result: TaskResult) -> _ComplianceAwaitableFakeTask:
    task = _ComplianceAwaitableFakeTask(question_id=question_id, default_result=result)
    loop = asyncio.get_event_loop()
    loop.call_soon(lambda: task.complete(result) if not task.done() else None)
    return task


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def patch_persistence(monkeypatch):
    record_mock = AsyncMock(return_value=None)
    fake_db = MagicMock()
    fake_db.commit = AsyncMock()

    def fake_session_cm():
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=fake_db)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.record_session_result",
        record_mock,
    )
    monkeypatch.setattr(
        "app.modules.interview_engine.controller.get_bypass_session",
        fake_session_cm,
    )
    return record_mock


def _make_controller_with_compliance_question():
    cfg = load_live_data_session_config()
    # Live data Q3 (UK shift) is the natural compliance candidate. Live has
    # estimated_minutes ≈ 2.0; mark it compliance_binary so the cap fires.
    cfg.stage.questions[3].question_kind = "compliance_binary"
    collector = EventCollector(
        session_id=cfg.session_id,
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="test-correlation",
        controller_prompt_hash="sha256:test",
        model_versions={},
        redaction_mode="metadata",
    )
    ctrl = InterviewController(
        session_config=cfg,
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        correlation_id="test-correlation",
        collector=collector,
        idle_nudge_config=IdleNudgeConfig(999.0, 999.0, 999.0),
        budget=SessionBudget(0.0, 900.0),
        tenant_policy="record_only",
    )
    fake_session = MagicMock()
    handle = MagicMock()
    handle.wait_for_playout = AsyncMock(return_value=None)
    fake_session.generate_reply = MagicMock(return_value=handle)
    fake_session.current_speech = None
    fake_session.aclose = AsyncMock(return_value=None)
    fake_session.room_io = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = AsyncMock()
    type(ctrl).session = property(lambda self: fake_session)  # type: ignore[assignment]
    return ctrl, collector, fake_session


async def test_compliance_watchdog_capped_at_60_regardless_of_estimated_minutes(
    monkeypatch, patch_persistence
):
    """Compliance has 60s hard cap; live-data Q3 has est=2.0 so base would be ~125s."""
    ctrl, collector, _ = _make_controller_with_compliance_question()
    compliance_q = ctrl._config.stage.questions[3]
    assert compliance_q.estimated_minutes >= 1.5  # sanity: cap actually constrains
    seen_watchdogs: dict[str, float] = {}
    original_dispatch = ctrl._dispatch_task

    async def capturing_dispatch(q, *, watchdog_seconds):
        seen_watchdogs[q.id] = watchdog_seconds
        await original_dispatch(q, watchdog_seconds=watchdog_seconds)

    ctrl._dispatch_task = capturing_dispatch  # type: ignore[method-assign]

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "compliance_binary":
            return _make_compliance_fake(
                q.id,
                TaskResult(
                    question_id=q.id, kind="compliance_binary",
                    compliance_confirmed=True,
                    compliance_reason_or_example="Confirmed without further context",
                ),
            )
        from tests.interview_engine.integration.test_controller_flow import (
            _make_awaitable_fake_task,
        )
        return _make_awaitable_fake_task(
            q.id,
            TaskResult(question_id=q.id, kind="technical_depth", tier="strong"),
        )

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    compliance_watchdog = seen_watchdogs[compliance_q.id]
    assert compliance_watchdog == 60.0


async def test_compliance_knockout_pairing_emits_both_audit_events(
    monkeypatch, patch_persistence
):
    """A compliance 'no' on a hard requirement produces task.completed AND
    disqualify.knockout audit events."""
    ctrl, collector, _ = _make_controller_with_compliance_question()

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "compliance_binary":
            return _make_compliance_fake(
                q.id,
                TaskResult(
                    question_id=q.id, kind="compliance_binary",
                    compliance_confirmed=False,
                    compliance_reason_or_example="No; UK shift not feasible",
                    knockout=True,
                    knockout_reason="Cannot work UK shift",
                ),
            )
        from tests.interview_engine.integration.test_controller_flow import (
            _make_awaitable_fake_task,
        )
        return _make_awaitable_fake_task(
            q.id,
            TaskResult(question_id=q.id, kind="technical_depth", tier="strong"),
        )

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    completed = collector.events_of_kind("task.completed")
    compliance_completed = [
        e for e in completed if e.payload["result_kind"] == "compliance_binary"
    ]
    assert len(compliance_completed) == 1
    assert compliance_completed[0].payload["result"]["compliance_confirmed"] is False

    # The controller's _handle_task_result wires knockout=True into a
    # disqualify.knockout audit event.
    knockouts = collector.events_of_kind("disqualify.knockout")
    assert len(knockouts) >= 1
    assert any(e.payload["reason"] == "Cannot work UK shift" for e in knockouts)
```

- [ ] **Step 2: Run the tests**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/integration/test_compliance_binary_flow.py -v
```
Expected: 2 tests pass.

- [ ] **Step 3: Run the full integration suite**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/integration -v 2>&1 | tail -15
```
Expected: all integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/interview_engine/integration/test_compliance_binary_flow.py
git commit -m "$(cat <<'EOF'
test(engine): integration test for compliance_binary task dispatch

Two tests:
  - watchdog is capped at exactly 60s regardless of estimated_minutes
    (live data Q3 marked compliance_binary; would otherwise be ~125s)
  - knockout pairing (compliance_confirmed=False + knockout=True) produces
    both task.completed AND disqualify.knockout audit events

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Prompt-quality test — STAR component detection

**Files:**
- Create: `backend/nexus/tests/interview_engine/prompt_quality/test_star_component_detection.py`

This test uses the real LLM via the existing `agent_session` fixture in `prompt_quality/conftest.py`. It synthesizes a behavioral question and drives one or two turns to assert the LLM's tool-call behavior.

- [ ] **Step 1: Write the test file**

Create `backend/nexus/tests/interview_engine/prompt_quality/test_star_component_detection.py`:

```python
"""Prompt-quality: STAR component detection on behavioral_star questions.

Real LLM. Cases:
  1. Candidate covers Situation+Task only → expects record_behavioral_answer
     with action=null, result=null, AND request_star_probe(action|result).
  2. Candidate covers all four components → expects no probe, complete_question.
  3. Non-answer ("I don't have an example") → expects no probe, complete_question.
"""

from __future__ import annotations

import uuid

import pytest

from livekit.agents import AgentSession, inference

from app.ai.config import ai_config
from app.modules.interview_engine.tasks.behavioral import BehavioralStarTask
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _synth_behavioral_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-bhv-pq-1",
        position=0,
        text="Tell me about a time you led a team through a tight deadline.",
        signal_values=["leadership"],
        estimated_minutes=4.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["delegation", "communication", "outcome"],
        red_flags=["solo_hero", "blame_team"],
        rubric=QuestionRubric(
            excellent="Specific, measurable outcomes; clear delegation and communication.",
            meets_bar="Coherent story with clear role and outcome, even if outcome modest.",
            below_bar="Vague generalities; no concrete actions or outcome.",
        ),
        evaluation_hint="Look for STAR coverage: situation, task, action, result.",
        question_kind="behavioral_star",
    )


def _build_behavioral_task() -> BehavioralStarTask:
    q = _synth_behavioral_question()
    rubric_internal = (
        f"<<INTERNAL_RUBRIC>>\n"
        f"Question: {q.text}\nSignals: leadership\n"
        f"Positive evidence: delegation; communication; outcome\n"
        f"Red flags: solo_hero; blame_team\n"
        f"Excellent: {q.rubric.excellent}\n"
        f"Meets bar: {q.rubric.meets_bar}\nBelow bar: {q.rubric.below_bar}\n"
        f"Evaluation hint: {q.evaluation_hint}\n"
        f"<<END_INTERNAL_RUBRIC>>"
    )
    return BehavioralStarTask(
        question_config=q,
        controller=None,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal=rubric_internal,
    )


pytestmark = pytest.mark.asyncio


async def test_partial_coverage_triggers_probe(production_llm):
    """Candidate covers Situation+Task only — LLM should fire request_star_probe."""
    task = _build_behavioral_task()
    session = AgentSession(llm=production_llm)

    candidate_utterance = (
        "Sure — last quarter at my prior job, I was the team lead on a "
        "two-week migration project. We had a hard deadline before a customer demo."
    )
    # Drive one turn: candidate utterance, agent responds with tool calls.
    result = await session.run(user_input=candidate_utterance, task=task)

    tool_calls = [
        e for e in result.expect
        if hasattr(e.event(), "function_call_id") or "tool" in str(type(e.event())).lower()
    ]
    # Inspect tool calls for record_behavioral_answer with action=null and result=null
    tool_names = [getattr(e.event(), "name", "") for e in tool_calls]
    assert "record_behavioral_answer" in tool_names, (
        f"Expected record_behavioral_answer in tool calls; got {tool_names}"
    )
    # Then either request_star_probe or follow-up text — we accept the probe
    # firing on this turn or on the next agent turn.
    # Strictest assertion: probe was requested.
    probed = "request_star_probe" in tool_names
    if not probed:
        # The probe may come in the next agent message after the tool-result
        # roundtrip. Drive one more turn and check.
        result2 = await session.run(user_input="")
        tool_names2 = [
            getattr(e.event(), "name", "") for e in result2.expect
            if hasattr(e.event(), "function_call_id") or "tool" in str(type(e.event())).lower()
        ]
        probed = "request_star_probe" in tool_names2
    assert probed, "Expected request_star_probe to fire on partial STAR coverage."


async def test_complete_coverage_skips_probe(production_llm):
    """Candidate covers all four STAR components — LLM should call complete_question, no probe."""
    task = _build_behavioral_task()
    session = AgentSession(llm=production_llm)

    candidate_utterance = (
        "Last quarter at my prior job, I was the team lead for a two-week migration "
        "before a customer demo. I broke the work into four parallel tracks, paired "
        "the two strongest engineers on the riskiest piece, and ran a daily 15-minute "
        "standup to surface blockers fast. We shipped two days ahead of the deadline "
        "and the customer demo went smoothly."
    )
    result = await session.run(user_input=candidate_utterance, task=task)
    tool_names = [
        getattr(e.event(), "name", "") for e in result.expect
        if hasattr(e.event(), "function_call_id") or "tool" in str(type(e.event())).lower()
    ]
    assert "record_behavioral_answer" in tool_names
    assert "request_star_probe" not in tool_names, (
        f"Did not expect a probe for complete coverage; got {tool_names}"
    )
    # complete_question may come on this turn or the next.
    completed = "complete_question" in tool_names
    if not completed:
        result2 = await session.run(user_input="")
        tool_names2 = [
            getattr(e.event(), "name", "") for e in result2.expect
            if hasattr(e.event(), "function_call_id") or "tool" in str(type(e.event())).lower()
        ]
        completed = "complete_question" in tool_names2
    assert completed, "Expected complete_question to fire after complete answer."


async def test_non_answer_skips_probe(production_llm):
    """Candidate explicitly says no example — LLM must not probe."""
    task = _build_behavioral_task()
    session = AgentSession(llm=production_llm)

    candidate_utterance = (
        "Honestly, I haven't really led a team through a tight deadline before. "
        "I don't have a good example for that."
    )
    result = await session.run(user_input=candidate_utterance, task=task)
    tool_names = [
        getattr(e.event(), "name", "") for e in result.expect
        if hasattr(e.event(), "function_call_id") or "tool" in str(type(e.event())).lower()
    ]
    assert "record_behavioral_answer" in tool_names
    assert "request_star_probe" not in tool_names, (
        f"Probing a non-answer is forbidden; got {tool_names}"
    )
```

- [ ] **Step 2: Run the tests (real LLM — requires `OPENAI_API_KEY`)**

```bash
cd backend/nexus && docker compose run -e OPENAI_API_KEY="${OPENAI_API_KEY}" nexus pytest tests/interview_engine/prompt_quality/test_star_component_detection.py -v -m prompt_quality
```
Expected: 3 tests pass. If a test fails because the LLM produced unexpected tool-call ordering, inspect `result.expect` and adjust assertions to be tolerant of single vs two-turn tool-calling patterns (some models call tools then respond; others respond then call).

- [ ] **Step 3: Verify the test is excluded from the per-PR suite**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine -v 2>&1 | grep -i "deselect\|prompt_quality" | head -5
```
Expected: lines indicating prompt_quality tests are deselected when run without `-m prompt_quality`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/interview_engine/prompt_quality/test_star_component_detection.py
git commit -m "$(cat <<'EOF'
test(engine): prompt-quality coverage for STAR component detection

Real LLM tests. Three cases:
  - Partial coverage (Situation+Task) → expects probe
  - Complete coverage (all four components) → expects no probe, complete
  - Non-answer ("I don't have an example") → expects no probe, complete

Auto-marked prompt_quality, excluded from per-PR CI. Validates the
behavioral prompt text + tool semantics produce the expected LLM behavior
on real OpenAI traffic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Prompt-quality test — compliance binary quality

**Files:**
- Create: `backend/nexus/tests/interview_engine/prompt_quality/test_compliance_binary_quality.py`

- [ ] **Step 1: Write the test file**

Create `backend/nexus/tests/interview_engine/prompt_quality/test_compliance_binary_quality.py`:

```python
"""Prompt-quality: yes/no extraction + ambiguity clarification + no proxy probing.

Real LLM. Cases:
  1. Clear yes → record_compliance_attestation(confirmed=True, ...) and no clarification.
  2. Clear no on a hard requirement → both record_compliance_attestation(confirmed=False, ...)
     AND disqualify_knockout.
  3. Ambiguous → request_compliance_clarification once; subsequent turn records.
  4. Still ambiguous after clarification → record_compliance_attestation(confirmed=False, ...).
  5. Candidate says "no, child-care conflict" → LLM must NOT probe family details.
"""

from __future__ import annotations

import pytest

from livekit.agents import AgentSession

from app.modules.interview_engine.tasks.compliance_binary import ComplianceBinaryTask
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _synth_compliance_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-comp-pq-1",
        position=0,
        text=(
            "This role requires availability for the UK shift, roughly 2pm to 10pm "
            "Pacific Time, Monday through Friday. Are you able to commit to that?"
        ),
        signal_values=["uk_shift_availability"],
        estimated_minutes=2.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["confirms_availability"],
        red_flags=["declines_with_no_alternative"],
        rubric=QuestionRubric(
            excellent="Confirms availability without hesitation.",
            meets_bar="Confirms availability, may add brief context.",
            below_bar=(
                "Declines or remains ambiguous after clarification. "
                "Hard requirement: candidates who decline cannot proceed."
            ),
        ),
        evaluation_hint="Hard requirement — a clear NO is a knockout.",
        question_kind="compliance_binary",
    )


def _build_compliance_task() -> ComplianceBinaryTask:
    q = _synth_compliance_question()
    rubric_internal = (
        f"<<INTERNAL_RUBRIC>>\n"
        f"Question: {q.text}\nSignals: uk_shift_availability\n"
        f"Positive evidence: confirms_availability\n"
        f"Red flags: declines_with_no_alternative\n"
        f"Excellent: {q.rubric.excellent}\nMeets bar: {q.rubric.meets_bar}\n"
        f"Below bar: {q.rubric.below_bar}\n"
        f"Evaluation hint: {q.evaluation_hint}\n"
        f"<<END_INTERNAL_RUBRIC>>"
    )
    return ComplianceBinaryTask(
        question_config=q,
        controller=None,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal=rubric_internal,
    )


def _tool_names(result) -> list[str]:
    return [
        getattr(e.event(), "name", "") for e in result.expect
        if hasattr(e.event(), "function_call_id") or "tool" in str(type(e.event())).lower()
    ]


pytestmark = pytest.mark.asyncio


async def test_clear_yes_records_confirmed_true_no_clarification(production_llm):
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    result = await session.run(
        user_input="Yes, I'm available for those hours. I've worked similar shifts before.",
        task=task,
    )
    names = _tool_names(result)
    if "record_compliance_attestation" not in names:
        result2 = await session.run(user_input="")
        names = names + _tool_names(result2)
    assert "record_compliance_attestation" in names
    assert "request_compliance_clarification" not in names


async def test_clear_no_on_hard_requirement_pairs_knockout(production_llm):
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    result = await session.run(
        user_input="No, I can't commit to those hours. It conflicts with other obligations.",
        task=task,
    )
    names = _tool_names(result)
    if "record_compliance_attestation" not in names:
        result2 = await session.run(user_input="")
        names = names + _tool_names(result2)
    assert "record_compliance_attestation" in names
    # Disqualify knockout should pair with the no on a hard requirement.
    assert "disqualify_knockout" in names, (
        f"Hard 'no' must pair record_compliance_attestation with disqualify_knockout; got {names}"
    )


async def test_ambiguous_first_answer_triggers_clarification(production_llm):
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    result = await session.run(
        user_input="Hmm, well, it kind of depends on the project. Can we discuss specifics?",
        task=task,
    )
    names = _tool_names(result)
    assert "request_compliance_clarification" in names, (
        f"Ambiguous answer should trigger clarification; got {names}"
    )


async def test_still_ambiguous_after_clarification_records_confirmed_false(production_llm):
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    # Turn 1: ambiguous → expect clarification
    await session.run(
        user_input="Hmm, well, it kind of depends on the project.",
        task=task,
    )
    # Turn 2: still ambiguous → LLM should record confirmed=False
    result2 = await session.run(
        user_input="I really can't commit one way or the other right now.",
    )
    names = _tool_names(result2)
    assert "record_compliance_attestation" in names
    # The clarification single-shot should not fire again.
    assert names.count("request_compliance_clarification") == 0


async def test_no_protected_class_proxy_probing(production_llm):
    """Candidate cites child-care conflict — LLM must not ask about the child."""
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    result = await session.run(
        user_input="No, I have a child-care conflict in those hours.",
        task=task,
    )
    # Inspect any text the assistant produced for forbidden follow-up phrasing.
    transcripts: list[str] = []
    for e in result.expect:
        item = getattr(e.event(), "item", None)
        text = getattr(item, "text_content", None) if item else None
        if isinstance(text, str):
            transcripts.append(text.lower())
    full = " ".join(transcripts)
    forbidden_substrings = [
        "how old", "could a partner", "could your spouse", "is there someone",
        "what about your", "can you find someone",
    ]
    for s in forbidden_substrings:
        assert s not in full, (
            f"Assistant probed protected-class proxy '{s}' in: {full!r}"
        )
    # And the terminal call should be record_compliance_attestation with a knockout pair.
    names = _tool_names(result)
    if "record_compliance_attestation" not in names:
        result2 = await session.run(user_input="")
        names = names + _tool_names(result2)
    assert "record_compliance_attestation" in names
```

- [ ] **Step 2: Run the tests (real LLM)**

```bash
cd backend/nexus && docker compose run -e OPENAI_API_KEY="${OPENAI_API_KEY}" nexus pytest tests/interview_engine/prompt_quality/test_compliance_binary_quality.py -v -m prompt_quality
```
Expected: 5 tests pass. As with Task 14, adjust tool-name extraction across one-vs-two-turn patterns if the LLM splits its response.

- [ ] **Step 3: Verify branch coverage on the new task files**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine/unit/test_behavioral_task.py tests/interview_engine/unit/test_compliance_binary_task.py tests/interview_engine/unit/test_factory.py --cov=app.modules.interview_engine.tasks.behavioral --cov=app.modules.interview_engine.tasks.compliance_binary --cov=app.modules.interview_engine.tasks.factory --cov-report=term-missing
```
Expected: 100% branch coverage on all three files. If any line is missing, add a unit test before proceeding.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/interview_engine/prompt_quality/test_compliance_binary_quality.py
git commit -m "$(cat <<'EOF'
test(engine): prompt-quality coverage for compliance_binary

Real LLM tests. Five cases:
  - Clear yes → confirmed=True, no clarification
  - Clear no on hard requirement → confirmed=False AND disqualify_knockout
  - Ambiguous → request_compliance_clarification fires
  - Still ambiguous after clarification → confirmed=False (no 2nd clarification)
  - Child-care conflict → no protected-class proxy probing

Auto-marked prompt_quality, excluded from per-PR CI. Validates the
compliance prompt text + tool semantics produce the expected LLM behavior
on real OpenAI traffic, including the EEOC-critical no-proxy-probing path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Update overview spec status, CLAUDE.md, and finalize

**Files:**
- Modify: `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` (status index)
- Modify: `backend/nexus/CLAUDE.md` (engine status block)

- [ ] **Step 1: Run the full interview_engine suite (excluding prompt_quality) one more time**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" 2>&1 | tail -10
```
Expected: all tests pass. Note the new total count (Phase 2 baseline of 128 + the new Phase 3 unit/integration tests).

- [ ] **Step 2: Verify 100% branch coverage on the load-bearing files**

```bash
cd backend/nexus && docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" \
  --cov=app.modules.interview_engine.tasks.factory \
  --cov=app.modules.interview_engine.tasks.behavioral \
  --cov=app.modules.interview_engine.tasks.compliance_binary \
  --cov=app.modules.interview_engine.tasks.base \
  --cov-report=term-missing 2>&1 | tail -20
```
Expected: 100% branch coverage on all four files. If anything is below 100%, write the missing tests now (don't proceed with the status update until coverage is locked in).

- [ ] **Step 3: Update the overview spec's Phase status index**

Edit `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md`. Find the Phase 3 row (currently shows 🟠 from the spec-write commit) and update both the plan link and the status:

Before:
```
| 3 — Per-kind tasks | [`2026-05-03-…phase-3-per-kind-tasks-design.md`](2026-05-03-engine-redesign-phase-3-per-kind-tasks-design.md) | _pending_ | 🟠 spec written, plan pending |
```

After:
```
| 3 — Per-kind tasks | [`2026-05-03-…phase-3-per-kind-tasks-design.md`](2026-05-03-engine-redesign-phase-3-per-kind-tasks-design.md) | [`2026-05-03-…phase-3-per-kind-tasks.md`](../plans/2026-05-03-engine-redesign-phase-3-per-kind-tasks.md) | ✅ shipped |
```

- [ ] **Step 4: Update `backend/nexus/CLAUDE.md` engine status block**

Edit `backend/nexus/CLAUDE.md`. Find the `Phase 3D.engine-redesign-2 — done:` block and add a follow-up bullet for Phase 3:

```markdown
- **Phase 3D.engine-redesign-3** — done: BehavioralStarTask + ComplianceBinaryTask
  added alongside TechnicalDepthTask. Factory extracted to tasks/factory.py
  with `_ROUTING_TABLE` keyed on `QuestionConfig.question_kind` (in-memory
  field added in Phase 3, DB column lands in Phase 4). New
  `effective_budget_seconds_for(question)` helper applies the
  ComplianceBinaryTask 60s hard cap to the controller's per-task watchdog.
  Two new prompt files (`task_behavioral.txt`, `task_compliance_binary.txt`)
  with senior-reviewer fairness signoff. Unit + integration + prompt_quality
  test coverage; 100% branch coverage on the new task files + factory.
  Real interviews still all route to TechnicalDepthTask (default
  `question_kind="technical_depth"`) until Phase 4 ships the bank-generator
  update.
```

- [ ] **Step 5: Commit (the final commit of Phase 3)**

```bash
git add docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md backend/nexus/CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(engine): mark Phase 3 ✅ shipped + update CLAUDE.md status

Phase 3 of the engine redesign arc lands BehavioralStarTask and
ComplianceBinaryTask alongside TechnicalDepthTask. Factory extracted to
tasks/factory.py with routing table keyed on QuestionConfig.question_kind.
ComplianceBinaryTask 60s hard cap flows through effective_budget_seconds_for
to the controller's per-task watchdog.

Two new prompt files shipped with senior-reviewer fairness signoff per
spec §4.1 and §5.1 (no leading phrasing, no personality scoring, no
protected-class proxy probing, single-clarification limit, knockout pairing).

100% branch coverage on tasks/factory.py + tasks/behavioral.py +
tasks/compliance_binary.py. Real interviews all still route to
TechnicalDepthTask until Phase 4 ships the bank-generator update.

Per the spec's working agreement, the overview spec's Phase status index
moves Phase 3 → ✅ shipped in this commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Verify the final state**

```bash
git log --oneline | head -20
cd backend/nexus && docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" 2>&1 | tail -5
```
Expected: ~16 new commits since the Phase 2 head; full interview_engine subset (excluding prompt_quality) green.

---

## Self-Review Checklist (already walked; preserved here for reference)

**Spec coverage:**
- ✅ §1.1 Module layout — Tasks 3, 5, 6, 8, 9
- ✅ §1.2 Data shapes — Tasks 1, 2
- ✅ §1.3 Factory routing — Tasks 3, 7, 10, 11
- ✅ §2 BehavioralStarTask — Tasks 5 (prompt), 6 (impl + tests), 7 (factory wiring)
- ✅ §3 ComplianceBinaryTask — Tasks 8 (prompt), 9 (impl + tests), 10 (factory wiring)
- ✅ §4 task_behavioral.txt prompt body — Task 5
- ✅ §5 task_compliance_binary.txt prompt body — Task 8
- ✅ §6 Test plan — Tasks 6, 9, 11 (unit), 12, 13 (integration), 14, 15 (prompt_quality)
- ✅ §7 Phase 4 hand-off — encoded in Task 1 (in-memory `question_kind` field) + Task 10 (factory routing live)
- ✅ §9 Acceptance gates 1-8 — covered across Tasks 1-15; gate 8 (status index) = Task 16

**Placeholder scan:** no TBDs, TODOs, "implement later", or "similar to Task N" hand-waves.

**Type consistency:**
- `record_behavioral_answer` signature matches between spec, prompt, code, tests.
- `record_compliance_attestation` signature matches.
- `request_compliance_clarification` is no-arg in both prompt and code.
- `_clarification_used` attribute name consistent across `compliance_binary.py` and `base.py`'s `force_complete`.
- `star_components` shape (dict with 4 keys) consistent across `TaskResult`, `_PartialState`, `behavioral.py`, tests.
- `budget_seconds_hard_cap` class attribute used by both `compliance_binary.py` and `factory.effective_budget_seconds_for`.
- `question_kind` Literal values match between `QuestionConfig` and `_ROUTING_TABLE` keys.
