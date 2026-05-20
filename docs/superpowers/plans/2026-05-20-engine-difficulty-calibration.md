# Interview Engine — Difficulty Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent's strictness scale with question difficulty. Today the engine never sees difficulty — a "Jr." stage got pushed for senior-level depth (session `26c2efc3`). After this, `easy` stages accept engagement and push gently; `hard` stages demand depth and push harder. Symmetric calibration.

**Architecture:** Promote difficulty to per-question (`stage_questions.difficulty` column + `QuestionConfig.difficulty`), defaulting to the stage difficulty when unset. Thread the active question's difficulty into both the Judge input and the Speaker input. Parameterize the State Engine's advance quality-gate and push-back cap by difficulty per the symmetric table. Add per-difficulty calibration sections to the Judge and Speaker prompts.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy async, Alembic, pytest. One Alembic migration (`0042`). Touches `interview_engine`, `interview_runtime`, and `question_bank`.

**Prerequisite:** This plan assumes the **Conversational Repair** plan (`2026-05-20-engine-conversational-repair.md`) has landed. The push-back-cap parameterization here builds on the cap logic that plan leaves at a fixed `2`. If executed before that plan, the cap edits in Task B6 still apply (the cap constant exists today) but verify line context.

---

## The symmetric calibration table (the contract)

| Lever | easy | medium | hard |
|---|---|---|---|
| **Advance quality gate** (min observation quality to advance cleanly) | OFF — engaged answer advances even if all `thin` | ≥1 `concrete` (today's behavior) | ≥1 `strong`, OR ≥2 `concrete` |
| **push_back cap** (max push_backs before forced advance) | 1 | 2 (today) | 3 |
| **Speaker tone** | warm, more scaffolding in framing | neutral | crisp, expects rigor |

`difficulty` resolution: `QuestionConfig.difficulty` (per-question) → falls back to `StageConfig.difficulty` (stage) when the per-question column is NULL. Both are `Literal["easy","medium","hard"]`.

---

## Background context for the implementing engineer

- `app/modules/interview_runtime/schemas.py` — `StageDifficulty = Literal["easy","medium","hard"]` already exists; `StageConfig.difficulty` already exists. `QuestionConfig` does NOT have difficulty yet. `build_session_config` (in `interview_runtime/service.py`) maps DB rows → `QuestionConfig`.
- `app/modules/question_bank/models.py` — `StageQuestion` ORM (table `stage_questions`). Adding a column here + a migration.
- `app/modules/question_bank/service.py` — `write_generated_questions()` inserts `StageQuestion` rows from `GeneratedQuestion`. The bank generator (`actors.py`) already knows `stage.difficulty` (it stamps `bank.stage_config_snapshot = {"difficulty": stage.difficulty}`).
- `app/modules/interview_engine/state/engine.py` — `process_judge_output`. The advance quality gate is `quality_downgrade = (... not active_has_quality_at_least_concrete() and active_push_back_count() < 2)`. The push_back cap is the literal `current_count >= 2` in the push_back branch.
- `app/modules/interview_engine/state/queue.py` — `active_has_quality_at_least_concrete()` exists. We add `active_has_quality_at_least_strong()` and `active_concrete_or_strong_count()`.
- `app/modules/interview_engine/judge/input_builder.py` — `JudgeInputPayload` + `build_judge_input`.
- `app/modules/interview_engine/models/speaker.py` — `SpeakerInput`.
- `app/modules/interview_engine/speaker/input_builder.py` — `build_speaker_input`.
- `prompts/v2/engine/judge.system.txt`, `prompts/v2/engine/speaker/_preamble.txt`.

**Migration commands:**
- Apply: `docker compose run --rm nexus alembic upgrade head`
- Latest revision today is `0041`; this plan adds `0042`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `migrations/versions/0042_question_difficulty.py` | Schema | Add `stage_questions.difficulty TEXT NULL` + CHECK |
| `app/modules/question_bank/models.py` | ORM | Add `difficulty` column + CheckConstraint |
| `app/modules/question_bank/service.py` | Persist questions | `write_generated_questions` stamps stage difficulty per row |
| `app/modules/question_bank/actors.py` | Bank generator | Pass `stage.difficulty` into `write_generated_questions` |
| `app/modules/interview_runtime/schemas.py` | Wire contract | Add `QuestionConfig.difficulty` |
| `app/modules/interview_runtime/service.py` | Build SessionConfig | `difficulty = q.difficulty or stage.difficulty` |
| `app/modules/interview_engine/judge/input_builder.py` | Judge input | Add `active_question_difficulty` |
| `app/modules/interview_engine/models/speaker.py` | Speaker input | Add `difficulty` |
| `app/modules/interview_engine/speaker/input_builder.py` | Project Speaker input | Thread active question difficulty |
| `app/modules/interview_engine/state/queue.py` | Quality queries | Add strong/concrete-count helpers |
| `app/modules/interview_engine/state/engine.py` | Routing | Parameterize quality-gate + push_back cap by difficulty |
| `prompts/v2/engine/judge.system.txt` | Judge brain | Difficulty calibration section |
| `prompts/v2/engine/speaker/_preamble.txt` | Speaker voice | Difficulty tone modulation |

---

## Task B1: Migration `0042` — add `stage_questions.difficulty`

**Files:**
- Create: `migrations/versions/0042_question_difficulty.py`

- [ ] **Step 1: Write the migration**

Create `migrations/versions/0042_question_difficulty.py`:

```python
"""question_difficulty

Revision ID: 0042
Revises: 0041
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add stage_questions.difficulty (TEXT, nullable).

    Per-question difficulty for finer control than the stage-level setting.
    NULL means "inherit the stage difficulty" — build_session_config falls
    back to StageConfig.difficulty when this is NULL. Legacy banks are NOT
    backfilled (regeneration stamps the stage difficulty). The CHECK allows
    NULL or one of the three difficulty literals.
    """
    op.add_column(
        "stage_questions",
        sa.Column("difficulty", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "stage_questions_difficulty_check",
        "stage_questions",
        "difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "stage_questions_difficulty_check", "stage_questions", type_="check",
    )
    op.drop_column("stage_questions", "difficulty")
```

- [ ] **Step 2: Apply the migration**

Run: `docker compose run --rm nexus alembic upgrade head`
Expected: `Running upgrade 0041 -> 0042, question_difficulty`. No error.

- [ ] **Step 3: Verify the column exists**

Run: `docker compose run --rm nexus python -c "import asyncio; from app.database import get_bypass_session; import sqlalchemy as sa;
async def m():
    async with get_bypass_session() as db:
        r = await db.execute(sa.text(\"select column_name from information_schema.columns where table_name='stage_questions' and column_name='difficulty'\"))
        print(r.scalar_one_or_none())
asyncio.run(m())"`
Expected: prints `difficulty`.

- [ ] **Step 4: Confirm the down-revision rolls back cleanly (DR rule: rollback script required)**

Run: `docker compose run --rm nexus alembic downgrade 0041` then `docker compose run --rm nexus alembic upgrade head`.
Expected: both succeed. (Leave the DB at head.)

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0042_question_difficulty.py
git commit -m "feat(question-bank): migration 0042 add stage_questions.difficulty"
```

---

## Task B2: `StageQuestion` ORM column + persistence

**Files:**
- Modify: `app/modules/question_bank/models.py`
- Modify: `app/modules/question_bank/service.py`
- Modify: `app/modules/question_bank/actors.py`
- Test: `tests/question_bank/test_write_generated_questions_difficulty.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/question_bank/test_write_generated_questions_difficulty.py` (use the project's existing DB-test fixtures; mirror an existing `tests/question_bank/` test's session fixture — adapt the fixture name to whatever the repo uses, e.g. `bypass_db`):

```python
import pytest
from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric
from app.modules.question_bank.service import write_generated_questions, get_bank_questions


@pytest.mark.asyncio
async def test_write_generated_questions_stamps_stage_difficulty(bank_fixture, bypass_db):
    """Each generated row inherits the stage difficulty passed in."""
    q = GeneratedQuestion(
        position=0, text="A question about the active topic, please walk me through it.",
        signal_values=["sig_a"], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="Look for one concrete example.", question_kind="technical_depth",
    )
    await write_generated_questions(
        bypass_db, bank=bank_fixture, questions=[q],
        source="ai_generated", stage_difficulty="hard",
    )
    rows = await get_bank_questions(bypass_db, bank_fixture.id)
    assert all(r.difficulty == "hard" for r in rows if r.source == "ai_generated")
```

(If `tests/question_bank/conftest.py` does not already provide `bank_fixture` / `bypass_db`, reuse the exact fixture names from a sibling test such as `tests/question_bank/test_service.py`; do not invent new fixtures.)

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_write_generated_questions_difficulty.py -v`
Expected: FAIL — `write_generated_questions() got an unexpected keyword argument 'stage_difficulty'`.

- [ ] **Step 3: Add the ORM column**

In `app/modules/question_bank/models.py`, in `StageQuestion`:

(a) Add to `__table_args__` (next to the existing `question_kind` CheckConstraint):

```python
        CheckConstraint(
            "difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')",
            name="stage_questions_difficulty_check",
        ),
```

(b) Add the column (after `question_kind`):

```python
    difficulty: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: Stamp difficulty in `write_generated_questions`**

In `app/modules/question_bank/service.py`, add a parameter to `write_generated_questions`:

```python
async def write_generated_questions(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    questions: list[GeneratedQuestion],
    source: str = "ai_generated",
    stage_difficulty: str | None = None,
) -> None:
```

and in the `StageQuestion(...)` construction inside the loop, add (next to `question_kind=incoming.question_kind,`):

```python
                difficulty=stage_difficulty,
```

- [ ] **Step 5: Pass `stage.difficulty` from the actor**

In `app/modules/question_bank/actors.py`, find the `await write_generated_questions(db, bank=bank, questions=validated, source="ai_generated",)` call and add the kwarg:

```python
        await write_generated_questions(
            db, bank=bank, questions=validated, source="ai_generated",
            stage_difficulty=stage.difficulty,
        )
```

(If there are other `write_generated_questions` call sites — e.g. the per-kind retry path or a regen path — pass `stage_difficulty=stage.difficulty` there too. Grep: `grep -rn "write_generated_questions(" app/modules/question_bank/`.)

- [ ] **Step 6: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_write_generated_questions_difficulty.py -v`
Expected: PASS.

- [ ] **Step 7: Run the question_bank regression**

Run: `docker compose run --rm nexus pytest tests/question_bank/ -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/modules/question_bank/models.py app/modules/question_bank/service.py app/modules/question_bank/actors.py tests/question_bank/test_write_generated_questions_difficulty.py
git commit -m "feat(question-bank): persist per-question difficulty (stamps stage difficulty)"
```

---

## Task B3: `QuestionConfig.difficulty` + `build_session_config` fallback

**Files:**
- Modify: `app/modules/interview_runtime/schemas.py`
- Modify: `app/modules/interview_runtime/service.py`
- Test: `tests/interview_runtime/test_build_session_config_difficulty.py` (create) OR append to the existing build_session_config test if present.

- [ ] **Step 1: Write the failing test**

Create `tests/interview_runtime/test_build_session_config_difficulty.py`. Use the repo's existing `build_session_config` test harness as the template (find it: `grep -rln "build_session_config" tests/`). The assertion is the new behavior:

```python
import pytest
from app.modules.interview_runtime.schemas import QuestionConfig


def test_question_config_difficulty_defaults_to_stage_when_none():
    """QuestionConfig.difficulty falls back to the stage difficulty when the
    per-question value is None (the fallback is applied in build_session_config;
    here we assert the model accepts an explicit value)."""
    q = QuestionConfig(
        id="q1", position=0, text="A question about the topic, walk me through it.",
        signal_values=["s1"], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric={"excellent": "x"*20, "meets_bar": "y"*20, "below_bar": "z"*20},
        evaluation_hint="Look for specifics here.", question_kind="technical_depth",
        difficulty="hard",
    )
    assert q.difficulty == "hard"
```

Also add an integration assertion to the existing build_session_config test (wherever it lives): after building config from a stage with `difficulty="easy"` and questions whose DB `difficulty` column is `NULL`, assert `config.stage.questions[0].difficulty == "easy"` (fallback applied).

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_build_session_config_difficulty.py -v`
Expected: FAIL — `QuestionConfig` has no field `difficulty`.

- [ ] **Step 3: Add the field to `QuestionConfig`**

In `app/modules/interview_runtime/schemas.py`, add to `QuestionConfig` (after `question_kind`):

```python
    difficulty: StageDifficulty = Field(
        default="medium",
        description=(
            "Per-question difficulty. Falls back to the stage difficulty in "
            "build_session_config when the DB column is NULL. Drives the "
            "engine's advance quality-gate, push-back cap, and Speaker tone. "
            "Default 'medium' keeps back-compat for any caller that omits it."
        ),
    )
```

- [ ] **Step 4: Apply the fallback in `build_session_config`**

In `app/modules/interview_runtime/service.py`, in the `QuestionConfig(...)` construction inside the `questions=[...]` comprehension, add (after `question_kind=q.question_kind,`):

```python
                    difficulty=(q.difficulty or stage.difficulty),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_build_session_config_difficulty.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_runtime/schemas.py app/modules/interview_runtime/service.py tests/interview_runtime/test_build_session_config_difficulty.py
git commit -m "feat(interview-runtime): QuestionConfig.difficulty with stage fallback"
```

---

## Task B4: Thread difficulty into the Judge input

**Files:**
- Modify: `app/modules/interview_engine/judge/input_builder.py`
- Modify: `app/modules/interview_engine/orchestrator.py`
- Test: `tests/interview_engine/judge/test_input_builder.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/judge/test_input_builder.py`:

```python
def test_judge_input_carries_active_question_difficulty():
    from app.modules.interview_engine.judge.input_builder import build_judge_input
    from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
    from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
    from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
    from app.modules.interview_runtime.schemas import QuestionConfig

    q = QuestionConfig(
        id="q1", position=0, text="A question about the topic, walk me through it.",
        signal_values=["s1"], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric={"excellent": "x"*20, "meets_bar": "y"*20, "below_bar": "z"*20},
        evaluation_hint="Look for specifics.", question_kind="technical_depth",
        difficulty="hard",
    )
    payload = build_judge_input(
        active_question=q,
        ledger_snapshot=SignalLedgerSnapshot(),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[], candidate_utterance="...", time_remaining_seconds=600,
        next_pending_question=None,
    )
    assert payload.active_question_difficulty == "hard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_input_builder.py::test_judge_input_carries_active_question_difficulty -v`
Expected: FAIL — `JudgeInputPayload` has no `active_question_difficulty`.

- [ ] **Step 3: Add the field + builder wiring**

In `app/modules/interview_engine/judge/input_builder.py`:

(a) Add the field to `JudgeInputPayload` (in the STABLE per-question section, after `active_question_evaluation_hint`):

```python
    active_question_difficulty: Literal["easy", "medium", "hard"] | None = Field(
        default=None,
        description=(
            "Difficulty of the active question. Calibrates grading strictness: "
            "on 'easy', accept an engaged answer even if thin; on 'hard', "
            "demand concrete depth (tradeoffs/numbers) before advancing. The "
            "State Engine enforces the advance gate and push-back cap "
            "deterministically; this is grading guidance for the Judge."
        ),
    )
```

(b) In `build_judge_input`, set it in the returned payload (after `active_question_evaluation_hint=...`):

```python
        active_question_difficulty=(
            active_question.difficulty if active_question else None
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_input_builder.py::test_judge_input_carries_active_question_difficulty -v`
Expected: PASS.

- [ ] **Step 5: Confirm the orchestrator passes it (already does via `build_judge_input(active_question=active_q_cfg, ...)`)**

The orchestrator's `_run_turn_body` calls `build_judge_input(active_question=active_q_cfg, ...)`. Since the field reads off `active_question.difficulty`, no orchestrator change is needed. Verify by grepping: `grep -n "build_judge_input(" app/modules/interview_engine/orchestrator.py` and confirm `active_question=active_q_cfg` is passed (it is). No edit required.

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_engine/judge/input_builder.py tests/interview_engine/judge/test_input_builder.py
git commit -m "feat(interview-engine): thread active_question_difficulty into Judge input"
```

---

## Task B5: Thread difficulty into the Speaker input

**Files:**
- Modify: `app/modules/interview_engine/models/speaker.py`
- Modify: `app/modules/interview_engine/speaker/input_builder.py`
- Test: `tests/interview_engine/speaker/test_input_builder.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/speaker/test_input_builder.py`:

```python
def test_speaker_input_carries_difficulty():
    from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric
    q = QuestionConfig(
        id="q1", position=0, text="A question about the topic, walk me through it.",
        signal_values=["s1"], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="Look for specifics.", question_kind="technical_depth",
        difficulty="easy",
    )
    si = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=_advance_judge_output(),
        active_question=q,
        queue=_queue_with_active(),
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[], persona_name="Arjun",
        last_candidate_utterance="...",
    )
    assert si.difficulty == "easy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py::test_speaker_input_carries_difficulty -v`
Expected: FAIL — `SpeakerInput` has no `difficulty`.

- [ ] **Step 3: Add the field to `SpeakerInput`**

In `app/modules/interview_engine/models/speaker.py`, add (near the other calibration fields):

```python
    difficulty: Literal["easy", "medium", "hard"] | None = Field(
        default=None,
        description=(
            "Active question difficulty. Modulates Speaker tone per the "
            "DIFFICULTY section in _preamble.txt: 'easy' = warmer, more "
            "scaffolding in framing; 'hard' = crisp, expects rigor. None "
            "falls back to neutral tone."
        ),
    )
```

- [ ] **Step 4: Thread it through `build_speaker_input`**

In `app/modules/interview_engine/speaker/input_builder.py`, in the `return SpeakerInput(...)` call, add:

```python
        difficulty=(active_question.difficulty if active_question else None),
```

(`active_question` is already a parameter of `build_speaker_input`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py::test_speaker_input_carries_difficulty -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_engine/models/speaker.py app/modules/interview_engine/speaker/input_builder.py tests/interview_engine/speaker/test_input_builder.py
git commit -m "feat(interview-engine): thread active question difficulty into Speaker input"
```

---

## Task B6: Parameterize the quality gate + push-back cap by difficulty

**Files:**
- Modify: `app/modules/interview_engine/state/queue.py`
- Modify: `app/modules/interview_engine/state/engine.py`
- Test: `tests/interview_engine/state/test_difficulty_calibration.py` (create)

- [ ] **Step 1: Add the queue quality helpers**

In `app/modules/interview_engine/state/queue.py`, add two methods to `QuestionQueue` (next to `active_has_quality_at_least_concrete`):

```python
    def active_has_quality_at_least_strong(self) -> bool:
        """True iff the active question has >=1 'strong' observation."""
        active = self.active_state()
        if active is None:
            return False
        return active.quality_observations.get("strong", 0) > 0

    def active_concrete_or_strong_count(self) -> int:
        """Count of 'concrete' + 'strong' observations on the active question."""
        active = self.active_state()
        if active is None:
            return 0
        return (
            active.quality_observations.get("concrete", 0)
            + active.quality_observations.get("strong", 0)
        )
```

- [ ] **Step 2: Write the failing calibration test**

Create `tests/interview_engine/state/test_difficulty_calibration.py`:

```python
"""Difficulty calibrates the advance quality-gate and the push-back cap.

  easy   : gate OFF (engaged thin answer advances), push_back cap 1
  medium : gate >=1 concrete (today), push_back cap 2
  hard   : gate >=1 strong OR >=2 concrete, push_back cap 3
"""
from app.modules.interview_engine.models.judge import (
    AdvancePayload, CoverageQuality, CoverageTransition, JudgeOutput, NextAction,
    Observation, PushBackPayload, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
    SessionConfig, SignalMetadata, StageConfig,
)


def _q(qid, sig, pos, difficulty):
    return QuestionConfig(
        id=qid, position=pos, text="A question about the topic, walk me through it.",
        signal_values=[sig], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="Look for specifics.", question_kind="technical_depth",
        difficulty=difficulty)


def _cfg(difficulty):
    return SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Eng", role_summary="r",
        seniority_level="mid", company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="S",
                          duration_minutes=15, difficulty=difficulty,
                          questions=[_q("q1", "sig_a", 0, difficulty), _q("q2", "sig_b", 1, difficulty)]),
        signals=["sig_a", "sig_b"],
        signal_metadata=[
            SignalMetadata(value="sig_a", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_b", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
        ])


def _start(eng):
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)


def _advance_with_thin():
    return JudgeOutput(
        reasoning="Candidate engaged but the answer is generic with no specifics yet.",
        observations=[Observation(signal_value="sig_a", anchor_id=0, evidence_quote="I would log it.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.thin)],
        candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata())


def test_easy_gate_off_thin_answer_advances():
    eng = StateEngine(session_config=_cfg("easy"))
    _start(eng)
    d = eng.process_judge_output(turn_id="t1", judge_output=_advance_with_thin(),
                                 candidate_utterance_text="I would log it.", elapsed_ms=1000)
    assert d.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert eng.queue_snapshot().active_index == 1  # advanced despite thin


def test_medium_gate_thin_answer_downgraded_to_push_back():
    eng = StateEngine(session_config=_cfg("medium"))
    _start(eng)
    d = eng.process_judge_output(turn_id="t1", judge_output=_advance_with_thin(),
                                 candidate_utterance_text="I would log it.", elapsed_ms=1000)
    assert d.speaker_input.instruction_kind == InstructionKind.push_back
    assert eng.queue_snapshot().active_index == 0  # held


def test_hard_gate_single_concrete_downgraded_to_push_back():
    eng = StateEngine(session_config=_cfg("hard"))
    _start(eng)
    adv = JudgeOutput(
        reasoning="Candidate named one concrete tool but no tradeoffs or scale yet.",
        observations=[Observation(signal_value="sig_a", anchor_id=0,
                                  evidence_quote="I used Splunk for the logs.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.concrete)],
        candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata())
    d = eng.process_judge_output(turn_id="t1", judge_output=adv,
                                 candidate_utterance_text="I used Splunk.", elapsed_ms=1000)
    # hard needs >=1 strong OR >=2 concrete; one concrete is not enough.
    assert d.speaker_input.instruction_kind == InstructionKind.push_back
    assert eng.queue_snapshot().active_index == 0


def test_push_back_cap_easy_is_one():
    eng = StateEngine(session_config=_cfg("easy"))
    _start(eng)
    pb = JudgeOutput(
        reasoning="Candidate engaged but the answer is thin; pushing for one specific.",
        observations=[Observation(signal_value="sig_a", anchor_id=0, evidence_quote="logs.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.thin)],
        candidate_claims=[], next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata())
    # 1st push_back honored (count 0 -> 1).
    d1 = eng.process_judge_output(turn_id="t1", judge_output=pb,
                                  candidate_utterance_text="logs.", elapsed_ms=1000)
    assert d1.speaker_input.instruction_kind == InstructionKind.push_back
    # 2nd push_back at easy hits the cap (1) -> downgrades to advance.
    d2 = eng.process_judge_output(turn_id="t2", judge_output=pb,
                                  candidate_utterance_text="logs again.", elapsed_ms=2000)
    assert d2.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert d2.speaker_input.is_post_cap_advance is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_difficulty_calibration.py -v`
Expected: FAIL — gates/cap are not yet difficulty-aware (`easy` will downgrade thin to push_back; `hard` will advance on one concrete; cap is fixed at 2).

- [ ] **Step 4: Add difficulty helper methods to `StateEngine`**

In `app/modules/interview_engine/state/engine.py`, add two private methods to `StateEngine` (near `_first_or_continuing_instruction`):

```python
    def _active_difficulty(self) -> str:
        """Difficulty of the active question; 'medium' when unknown."""
        qid = self._queue.active_question_id()
        if qid is None:
            return "medium"
        q_cfg = next((q for q in self._cfg.stage.questions if q.id == qid), None)
        return getattr(q_cfg, "difficulty", None) or "medium"

    def _push_back_cap(self) -> int:
        """Per-difficulty push-back cap: easy=1, medium=2, hard=3."""
        return {"easy": 1, "medium": 2, "hard": 3}.get(self._active_difficulty(), 2)

    def _advance_quality_met(self) -> bool:
        """Whether the active question's observations clear the advance gate
        for its difficulty.

          easy   : always True (engaged answer advances; gate OFF)
          medium : >=1 concrete or strong
          hard   : >=1 strong OR >=2 concrete
        """
        difficulty = self._active_difficulty()
        if difficulty == "easy":
            return True
        if difficulty == "hard":
            return (
                self._queue.active_has_quality_at_least_strong()
                or self._queue.active_concrete_or_strong_count() >= 2
            )
        return self._queue.active_has_quality_at_least_concrete()
```

- [ ] **Step 5: Use the gate in the advance branch**

In `process_judge_output`, in the `if action == NextAction.advance:` branch, the `quality_downgrade` condition currently reads:

```python
            quality_downgrade = (
                self._queue.active_state() is not None
                and not self._queue.active_has_quality_at_least_concrete()
                and self._queue.active_push_back_count() < 2
            )
```

Replace it with the difficulty-aware version:

```python
            quality_downgrade = (
                self._queue.active_state() is not None
                and not self._advance_quality_met()
                and self._queue.active_push_back_count() < self._push_back_cap()
            )
```

- [ ] **Step 6: Use the cap in the push_back branch**

In the `elif action == NextAction.push_back:` branch (the non-inverse-gate `else` path), the cap check currently reads `if current_count >= 2 and self._queue.active_state() is not None:`. Replace the literal `2` with the difficulty cap:

```python
                cap = self._push_back_cap()
                if current_count >= cap and self._queue.active_state() is not None:
```

(Leave the rest of the block — the `push_back_cap_reached` warning + `is_post_cap_advance = True` — unchanged.)

- [ ] **Step 7: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_difficulty_calibration.py -v`
Expected: PASS (all four tests).

- [ ] **Step 8: Run the State Engine regression**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/ -v`
Expected: PASS. Existing tests build configs with `difficulty="medium"` (the default in helpers), so the medium path == today's behavior — they should be unaffected. If any existing test built a config WITHOUT difficulty and relied on the default, confirm `_active_difficulty` returns "medium" for it.

- [ ] **Step 9: Commit**

```bash
git add app/modules/interview_engine/state/queue.py app/modules/interview_engine/state/engine.py tests/interview_engine/state/test_difficulty_calibration.py
git commit -m "feat(interview-engine): difficulty-gated advance quality + push-back cap (symmetric)"
```

---

## Task B7: Judge prompt — difficulty calibration section

**Files:**
- Modify: `prompts/v2/engine/judge.system.txt`

- [ ] **Step 1: Add the input field doc**

In §2 INPUT FIELDS, add a bullet (after `active_question_evaluation_hint`):

```
- `active_question_difficulty` — easy | medium | hard. Calibrates how
  strictly you grade observation quality and how readily you advance:
    easy   — accept an engaged answer; do not push for deep specifics.
    medium — expect at least one concrete tool/technique/example.
    hard   — expect concrete depth PLUS tradeoffs / numbers / edge cases
             before you call coverage sufficient.
  The State Engine enforces the advance gate and push-back cap; this guides
  your quality grading and your push_back vs advance leaning.
```

- [ ] **Step 2: Add a calibration note to §5 QUALITY GRADING**

After the ANTI-VERBOSITY-BIAS rule in §5, add:

```
DIFFICULTY CALIBRATION (use active_question_difficulty):
  On easy questions, grade generously — a clear engaged answer is enough;
  prefer advance over push_back. On hard questions, hold the bar high — a
  single named tool is `concrete` but NOT sufficient on its own; look for
  tradeoffs/scale/failure-modes (`strong`) before treating the signal as
  covered, and lean toward one more probe/push_back when depth is missing.
  Never be harsh on an easy question; never wave through a thin answer on a
  hard one.
```

- [ ] **Step 3: Verify prompt loads**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_prompt_loadable.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add prompts/v2/engine/judge.system.txt
git commit -m "feat(interview-engine): judge prompt difficulty calibration section"
```

---

## Task B8: Speaker prompt — difficulty tone modulation

**Files:**
- Modify: `prompts/v2/engine/speaker/_preamble.txt`

- [ ] **Step 1: Add the DIFFICULTY section**

In `prompts/v2/engine/speaker/_preamble.txt`, after the `# CONVERSATIONAL CONTEXT` section, add:

```
# DIFFICULTY (read `difficulty` in your input)
Your input may carry a `difficulty` for the active question. Modulate TONE
only — never change WHAT you ask (that is fixed by bank_text), and never
relax the anti-leak rules.
  easy   — warmer, a touch more framing. A friendly "no rush, just walk me
           through how you'd approach it" is welcome.
  medium — neutral, professional. The default.
  hard   — crisp and direct. You expect rigor; skip the soft framing and
           ask the question plainly.
This changes register, not content. When `difficulty` is absent, use medium.
```

- [ ] **Step 2: Verify prompt loads**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_prompt_loadable.py -v` (and any speaker-preamble-loadable test). Expected: PASS — no template-variable typos (the section uses no `{...}` placeholders).

- [ ] **Step 3: Commit**

```bash
git add prompts/v2/engine/speaker/_preamble.txt
git commit -m "feat(interview-engine): speaker preamble difficulty tone modulation"
```

---

## Task B9: Full regression + manual end-to-end smoke

- [ ] **Step 1: Full deterministic engine + question_bank + runtime suites**

Run:
```bash
docker compose run --rm nexus pytest tests/interview_engine/ tests/question_bank/ tests/interview_runtime/ -m "not prompt_quality" -v
```
Expected: PASS.

- [ ] **Step 2: Type-check**

Run: `docker compose run --rm nexus mypy app/modules/interview_engine/ app/modules/interview_runtime/ app/modules/question_bank/`
Expected: no new errors.

- [ ] **Step 3: Regenerate a bank + verify per-question difficulty persists**

Generate a question bank for a stage with `difficulty="easy"` via the normal flow, then query `stage_questions.difficulty` — expect every AI-generated row = `easy`.

- [ ] **Step 4: Manual end-to-end at easy and hard**

Run two live sessions (`docker compose up`):
- **easy stage:** give a thin-but-engaged answer; verify the agent advances rather than pushing 2-3 times, and the tone is warmer.
- **hard stage:** give one concrete tool with no tradeoffs; verify the agent pushes for depth (does not wave it through) and the tone is crisp.

- [ ] **Step 5: Final commit (if any fixups)**

```bash
git add -A
git commit -m "fix(interview-engine): difficulty-calibration regression fixups"
```

---

## Self-Review checklist

- **Spec coverage:** per-question difficulty (B1–B3); thread to Judge (B4) + Speaker (B5); gate quality + cap by difficulty, symmetric table (B6); prompt calibration Judge (B7) + Speaker (B8). ✓
- **Type consistency:** `difficulty` is `Literal["easy","medium","hard"]` everywhere; `QuestionConfig.difficulty` defaults `"medium"`; `SpeakerInput.difficulty` / `JudgeInputPayload.active_question_difficulty` are `... | None`. `_push_back_cap()` and `_advance_quality_met()` both read `_active_difficulty()`. The queue helpers `active_has_quality_at_least_strong()` / `active_concrete_or_strong_count()` defined in B6 Step 1 are used in B6 Step 4. ✓
- **No placeholders:** every code/test step has concrete code; the migration has full up/down. ✓
- **Migration safety:** `0042` is additive + nullable; rollback verified in B1 Step 4 (DR rule satisfied). Legacy banks not backfilled — `build_session_config` falls back to stage difficulty (B3 Step 4). ✓
- **Open risk:** existing State-Engine tests that omit `difficulty` rely on the `QuestionConfig` default `"medium"` and `_active_difficulty`'s `"medium"` fallback — confirm in B6 Step 8 that the medium path is byte-identical to today's behavior.
