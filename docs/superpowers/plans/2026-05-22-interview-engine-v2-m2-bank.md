# Interview Engine v2 — Milestone 2: Question Bank Redesign

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking. This is **M2** of the master plan
> (`2026-05-22-interview-engine-v2-master-plan.md`) — read its §3 (cutover), §3a (CMI-2), §5 (M2), §6
> (test/eval strategy) first. M2 is **independent of M1's engine code** and must not break the live
> recruiter bank flow or the (default-off) v2 engine. Guard each subagent's git scope (commit only the
> listed files; never `git checkout`/branch/stash/reset/amend/push; do NOT stage the pre-existing
> untracked `backend/nexus/scripts/export_job_agent_context.py`). After every task, the controller
> verifies `git symbolic-ref HEAD` is still `feat/interview-engine-v2-m2`.

**Goal:** Make question-bank generation produce **spoken, single-focus** questions (`text` ≤ ~200 chars,
depth in `follow_ups`), each carrying a `primary_signal` + per-question `difficulty` + a refined
`question_kind` (`experience_check | behavioral | technical_scenario | compliance_binary`); **stream each
question to the recruiter UI as it completes** (instructor `create_iterable` → persist + `BANK_QUESTION_ADDED`);
**chain** the just-generated behavioral set into the technical call so the two don't overlap; and group the
recruiter UI into Behavioral / Technical sections that fill live. All bank-gen prompts are rewritten from
scratch into `prompts/v2/`.

**Architecture:** The bank generator switches from "two non-streaming `create()` calls → validate the whole
array → one write at the end" to "two **streaming** `create_iterable()` calls → validate + persist +
publish **each question as it completes**, committing per question so the recruiter's GET re-fetch sees it
immediately." Budget is **soft + prompt-guided** under streaming (no LLM retry loop — v2 treats the bank as
a menu, not a script; see Decision D2). The taxonomy switches **cleanly at the write boundary** (DB CHECK +
`GeneratedQuestion`); the shared read projection `QuestionConfig.question_kind` **relaxes to `str`** so the
reference-only v1 suite stays an untouched backstop (Decision D1). v1's engine code and tests are not
touched.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy async, Alembic, Dramatiq, `instructor==1.15.1`
(`create_iterable` streaming, `Mode.TOOLS_STRICT`), `openai>=2.10,<3`, OpenAI via `app/ai/`
(`get_openai_client`, `PromptLoader`, `AIConfig`), Redis pub/sub (`app/pubsub.py`), Next.js 16
(`frontend/app`) + TanStack Query + `@microsoft/fetch-event-source`, Vitest.

---

## Decisions locked for M2 (resolve before coding — recorded so they can't resurface mid-build)

These two were genuine contradictions in the source docs; the user resolved them on 2026-05-22. The
master plan §3a CMI-2 has been updated to match D1.

### D1 — `question_kind`: VALIDATE-ON-WRITE, RELAX-ON-READ (reconciles CMI-2 + "v1 tests stay green")
- **Write boundary switches cleanly to the new taxonomy, no union:**
  - `stage_questions.question_kind` DB CHECK → `experience_check | behavioral | technical_scenario | compliance_binary`.
  - `question_bank.schemas.GeneratedQuestion.question_kind` Literal → the same 4 values.
  - Updating the **bank-side** tests that construct `GeneratedQuestion` with old strings is **in-scope M2
    work** (`tests/test_question_banks_{schemas,service,actors,events}.py`, `tests/question_bank/test_validate_splits.py`).
- **Read projection relaxes:** `interview_runtime.schemas.QuestionConfig.question_kind` → plain **`str`**
  (a read-only mirror of the CHECK-constrained column; **NOT a union** — enforcement lives at the write
  side). Docstring it (Task 4).
- **Why:** verified that NO v1 *engine* test constructs `GeneratedQuestion` (grep → none); v1 engine tests
  + `tests/interview_engine/fixtures/sample_session_config.json` only build/read **`QuestionConfig`** with
  old strings — the `str` relax keeps every one of them green **untouched**, so the reference-only v1
  backstop stays a true regression net (a backstop you had to rewrite no longer catches anything).
- **Caveat — DB CHECK applies to tests too:** tests build their schema from `Base.metadata.create_all`
  (M1 lesson), so the ORM `CheckConstraint` switch means **any test that INSERTs a `stage_questions` row**
  must use new-taxonomy strings. That set is bank-side tests + `tests/interview_runtime/*` (the shared
  contract layer M2 already extends) + `tests/test_question_banks_migration_0026.py` (tests the old
  CHECK — must be updated to the new one). The v1 engine tests do **not** INSERT `stage_questions` (they
  build `QuestionConfig` in-memory / load the JSON fixture), so they need zero edits.
- **M6:** tighten `QuestionConfig.question_kind` to the new Literal when v1 is retired, if desired.

### D2 — Budget under streaming: SOFT + prompt-guided + inline count ceiling (drop the LLM retry loop)
The current generator re-calls the LLM with corrective feedback when the validated array exceeds the stage
time budget (`BudgetExceededError` → `MAX_BUDGET_RETRIES`). That loop is impossible under streaming (you
can't un-stream persisted+emitted questions) **and unnecessary** for v2: v2 is thread-satisfaction-driven —
the brain does mandatory/knockout first, early-exits, probes only when productive, skips
preemptively-covered signals; it never promises to ask every question, so a bank slightly over the time box
is fine. Short spoken questions (depth in `follow_ups`, asked one-at-a-time only when the brain probes) make
`estimated_minutes` a weak time predictor anyway. The replacement (all streaming-compatible — no un-stream,
no retry):
1. **KEEP budget GUIDANCE in the prompt** (primary control): each call's user message states the stage
   budget + target. Sequential calls keep this exact — the behavioral call streams + completes first, so
   the technical call is told the behavioral total and the remaining budget (same as today).
2. **PER QUESTION as it streams:** validate `signal_values ⊆ snapshot` + types allowed + `primary_signal ∈
   signal_values` **inline**; skip (don't persist/emit) a hallucinated-signal question; else persist + emit
   `BANK_QUESTION_ADDED`.
3. **HARD INLINE COUNT CEILING** (`STREAM_QUESTION_CEILING = 12` per call) — runaway safety, not a time cap.
   Enforced mid-stream by simply stopping consumption past the ceiling (no un-stream, no retry). Only fires
   on a pathological runaway.
4. **AFTER the stream:** run the mandatory-knockout correction in place (verified: it only **flips
   `is_mandatory`**, never adds a question) + log a **soft over-budget WARNING** if the total exceeds the
   stage duration (surface it, don't block). The recruiter reviews/edits anyway (AI-decides-human-verifies;
   quality-before-latency). The terminal `BANK_STATUS_CHANGED → reviewing` event already triggers a FE
   re-fetch, so any `is_mandatory` flip on an already-emitted question is reflected without a special event.
- **Remove** the budget-retry loop + its tests (`test_generate_one_bank_retries_on_budget_violation_*`,
  `..._fails_after_repeated_budget_violations`). Keep `_validate_budget_against_stage` + its two direct unit
  tests (still a valid utility; just no longer called from the streaming gen path) — repurpose it to compute
  the soft-warning total, or compute the sum inline.

### D3 — Generation **phase** vs per-question **kind** (keep per-kind regen working)
The `kind` passed to `_generate_questions_for_kind` (`behavioral_star`/`technical_depth`) and the
`generation_status_by_kind` keys + `RegenerateKindBody.kind` are **call/phase** labels, distinct from each
question's `question_kind`. Under the new taxonomy a single call emits several `question_kind` values, so
the phase↔kind coupling in `regenerate_kind_actor`/`wipe_ai_questions_of_kind` (which delete/count by
`StageQuestion.question_kind == kind`) breaks. Resolution:
- Rename the phase labels to **`behavioral` / `technical`** (`generation_status_by_kind` keys +
  `RegenerateKindBody.kind` Literal).
- Define a fixed **phase→kinds partition** (Task 6's `PHASE_QUESTION_KINDS`), enforced by the rewritten
  prompts (each call may emit only its phase's kinds):
  - `behavioral` phase → `{experience_check, behavioral, compliance_binary}`
  - `technical`  phase → `{technical_scenario}`
- `wipe_ai_questions_of_kind` + the regen "other-phase total" query filter by `question_kind IN
  PHASE_QUESTION_KINDS[phase]`. The FE section grouping uses the same partition.

### D4 — Prompt versioning
New bank-gen prompts live in `prompts/v2/`. Add `AIConfig.question_bank_prompt_version` (Settings field
`question_bank_prompt_version`, default `"v2"`); construct a dedicated
`PromptLoader(version=ai_config.question_bank_prompt_version)` for the **spoken-question generation**
prompts only (`question_bank_common`, `question_bank_ai_screening`, `question_bank_ai_screening_behavioral`,
`question_bank_phone_screen`, `question_bank_regenerate_one`); stamp `bank.prompt_version` with it.
`question_bank_keyterms` (STT extraction) and `question_refine_single`/`question_create_single` (recruiter
draft/refine — their output schemas are NOT `GeneratedQuestion`, so they need no `primary_signal`) stay on
the v1 module loader and are out of scope.

### D5 — Streaming-safe `GeneratedQuestion`
instructor's streaming warns that **custom validators on a streamed model misbehave**. `GeneratedQuestion`
must therefore carry **no `@model_validator`/`@field_validator`** (Field-level constraints like
`min_length`/`max_length`/`ge`/`le` are fine). The cross-field rule `primary_signal ∈ signal_values` is
checked in the **post-arrival validation step** (`validate_streamed_question`), not as a Pydantic validator.
This same `validate_streamed_question` is used by the single-question regen path too (Task 11d) — it is the
ONLY place `primary_signal ∈ signal_values` is enforced, since `validate_llm_output_against_snapshot` checks
`signal_values ⊆ snapshot` + types but **not** `primary_signal` (verified).

### D6 — Streaming transaction model: NO long-lived session across the LLM stream
**Verified against live code:** `generate_question_bank_stage` (actors.py) opens ONE `get_bypass_session()`
and holds it across the entire `_run_stage_generation → _generate_one_bank` call, committing once at the
end; the pipeline path's `_run_one_pipeline_stage_in_session` does the same per stage. Under streaming that
outer transaction would sit **idle-in-transaction for the multi-second LLM stream** while per-question short
sessions commit inside it — pool pressure + ambiguous ownership (the outer `db` becomes vestigial). The bank
row is loaded read-only (no `FOR UPDATE`), so it isn't row-locked, but the held transaction + connection are
the problem. **Also (SQLAlchemy):** `AsyncSession` defaults to `expire_on_commit=True`, so any ORM object
loaded in a session is **expired after that session commits** — accessing its attributes later triggers a
lazy refresh that fails on a closed session. So a phase must capture **primitives / built prompt strings**
before its session closes; it must not pass live ORM objects into the stream loop.
**Decision** (mirror the existing load-then-release pattern in `_run_pipeline_generation`: phase-1 load /
phase-2 per-stage own session / phase-3 audit):
- **Phase A (short session):** load bank/stage/instance/job/snapshot; ensure `status='generating'`; wipe
  existing AI questions; commit; **close**. Nothing held after.
- **Phase B (NO held session):** each phase's `_generate_questions_for_kind` opens its OWN short read
  session to build the prompt + capture `snapshot.signals`/`allowed_types`/`stage_difficulty` as primitives,
  closes it, THEN streams (no session held); each validated question persists + publishes in its OWN short
  `get_bypass_session()` (Task 9).
- **Phase C (short session):** reload by id; reconcile (mandatory-correction flips) + per-phase status +
  keyterms + transition to `reviewing`; commit. Publish `BANK_STATUS_CHANGED` post-commit.
`_generate_one_bank` is refactored to take **primitives** (bank_id, tenant_id, started_by) and own its short
sessions; `_run_stage_generation` becomes the thin orchestration caller (or folds into the actor); the
pipeline per-stage path drops its outer session for the same reason. The bank row is never held in an open
transaction across the stream.

### D7 — Wipe on the failure path (partial-persist is new under streaming)
**Verified:** old code is all-or-nothing (one `write_generated_questions` at the end; an exception →
`transition_to_failed` with zero rows written). Under streaming, an error after N committed questions leaves
N orphans on a `failed` bank. Phase A's wipe-at-start self-heals on the next regenerate, but in the meantime
a `failed` bank shows a confusing partial set. **Decision:** the failure path ALSO wipes all AI-sourced
questions (recruiter rows preserved — `wipe_ai_questions` filters `source IN (ai_generated, ai_regenerated)`)
before `transition_to_failed`, so a failed bank shows zero. (`generation_status_by_kind` still records which
phase failed.)

---

## Task 1: Risk spike — confirm `create_iterable` per-question streaming (R2, do FIRST)

**Goal:** Before any code restructure, confirm that `instructor` 1.15.1 streams complete `GeneratedQuestion`
objects one-at-a-time from the reasoning model under `Mode.TOOLS_STRICT`, and nail the exact call path.
This is a **manual/gated** spike — no production code lands here. If per-question is infeasible, fall back
to per-set streaming and note it (then adjust Tasks 9–11).

**Files:**
- Create: `backend/nexus/tests/question_bank/test_streaming_spike.py` (gated `@pytest.mark.prompt_quality`)

- [ ] **Step 1: Write the spike (real-API, opt-in)**

`backend/nexus/tests/question_bank/test_streaming_spike.py`:
```python
"""R2 spike (M2 Task 1): confirm per-question streaming with the reasoning model.

Opt-in (hits the real OpenAI API):
    docker compose exec nexus pytest tests/question_bank/test_streaming_spike.py -m prompt_quality -s

Confirms three things and PRINTS the answers (this is a spike, not a gate):
  1. The exact call path on the instructor AsyncInstructor client.
  2. That multiple complete objects arrive incrementally (not all at the end).
  3. That reasoning_effort + Mode.TOOLS_STRICT are compatible with create_iterable.
"""
from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.ai.client import get_openai_client
from app.ai.config import ai_config

pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


class _SpokenQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=10, max_length=240)
    primary_signal: str
    difficulty: str


async def test_create_iterable_streams_incrementally():
    client = get_openai_client()
    # Confirm the call path exists (current code uses client.chat.completions.create):
    assert hasattr(client.chat.completions, "create_iterable"), "expected chat.completions.create_iterable"

    messages = [
        {"role": "system", "content": "You generate short spoken interview questions."},
        {"role": "user", "content": (
            "Generate exactly 4 short spoken screening questions for a Python backend role. "
            "Each: one focus, <200 chars, with a primary_signal and difficulty (easy/medium/hard)."
        )},
    ]
    kwargs = dict(
        model=ai_config.question_bank_model,
        response_model=_SpokenQ,
        messages=messages,
        max_retries=1,
    )
    if ai_config.question_bank_effort:
        kwargs["reasoning_effort"] = ai_config.question_bank_effort

    arrivals: list[float] = []
    start = time.monotonic()
    async for q in client.chat.completions.create_iterable(**kwargs):
        arrivals.append(time.monotonic() - start)
        print(f"  [{arrivals[-1]:.2f}s] {q.text!r} (signal={q.primary_signal})")

    print(f"\nSPIKE RESULT: {len(arrivals)} questions; arrival offsets={[round(a,2) for a in arrivals]}")
    assert len(arrivals) >= 2, "expected multiple streamed objects"
```

- [ ] **Step 2: Run the spike**

Run: `docker compose up -d nexus && docker compose exec nexus pytest tests/question_bank/test_streaming_spike.py -m prompt_quality -s`
Expected (success): prints ≥2 questions with **increasing** arrival offsets (incremental), confirming
`client.chat.completions.create_iterable(...)` is the call path and reasoning + TOOLS_STRICT work together.

- [ ] **Step 3: Record the finding + decide**

In the PR/commit message and at the top of Task 9, record one of:
- **PER-QUESTION CONFIRMED** (expected) → proceed with `create_iterable` per Tasks 9–11.
- **PER-QUESTION INFEASIBLE** (e.g. objects only materialize at the end, or TOOLS_STRICT+reasoning+stream
  errors) → fall back: use `create_partial(response_model=StageQuestionBankOutput)` and emit
  `BANK_QUESTION_ADDED` each time a NEW complete element appears in the partial `.questions` list (still
  better than today's whole-array-at-end). Note the fallback in Task 9 and keep the rest of the plan.
- If `create_iterable` is under `client.create_iterable` rather than `client.chat.completions.create_iterable`,
  use whichever the spike confirms in Tasks 9–11.

- [ ] **Step 4: Commit the spike (kept as a runnable artifact)**

```bash
git add backend/nexus/tests/question_bank/test_streaming_spike.py
git commit -m "test(question-bank): R2 spike — confirm per-question create_iterable streaming"
```

---

## Task 2: Migration `0045_bank_spoken_fields` + ORM (primary_signal + question_kind CHECK switch)

**Files:**
- Create: `backend/nexus/migrations/versions/0045_bank_spoken_fields.py`
- Modify: `backend/nexus/app/modules/question_bank/models.py` (StageQuestion: add column + swap CheckConstraint)
- Test: `backend/nexus/tests/question_bank/test_migration_0045.py`

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/question_bank/test_migration_0045.py`:
```python
"""0045: stage_questions gains primary_signal; question_kind CHECK switches to the new taxonomy."""

from app.modules.question_bank.models import StageQuestion


def test_primary_signal_column_present_and_nullable():
    col = StageQuestion.__table__.columns["primary_signal"]
    assert col.nullable is True


def test_question_kind_check_is_new_taxonomy():
    checks = {
        c.name: c.sqltext.text
        for c in StageQuestion.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    body = checks["stage_questions_question_kind_check"]
    for v in ("experience_check", "behavioral", "technical_scenario", "compliance_binary"):
        assert v in body
    for old in ("technical_depth", "behavioral_star", "open_culture"):
        assert old not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_migration_0045.py -v`
Expected: FAIL — `primary_signal` not a column / old values still in the CHECK.

- [ ] **Step 3: Update the ORM model**

In `backend/nexus/app/modules/question_bank/models.py`, in `class StageQuestion`:

(a) Replace the `question_kind` CheckConstraint in `__table_args__`:
```python
        CheckConstraint(
            "question_kind IN ('experience_check', 'behavioral', "
            "'technical_scenario', 'compliance_binary')",
            name="stage_questions_question_kind_check",
        ),
```

(b) Add the `primary_signal` mapped column (next to `difficulty`, ~line 141):
```python
    primary_signal: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "The single signal value the lead question opens — makes thread-"
            "satisfaction crisp (thread done when primary_signal is covered). "
            "Must be a member of signal_values. NULL only for legacy/hand rows "
            "predating the spoken-question redesign; the generator always sets it."
        ),
    )
```

(c) Change the `question_kind` column server_default from `'technical_depth'` to a new-taxonomy default:
```python
    question_kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'technical_scenario'")
    )
```

- [ ] **Step 4: Write the migration**

`backend/nexus/migrations/versions/0045_bank_spoken_fields.py`:
```python
"""bank spoken-question fields: primary_signal + question_kind taxonomy switch

Revision ID: 0045_bank_spoken_fields
Revises: 0044_interview_engine_version
Create Date: 2026-05-22

Interview-engine-v2 M2. Adds stage_questions.primary_signal (nullable) and switches
the question_kind CHECK OUTRIGHT to the new spoken taxonomy (no old∪new union; dev
mode, no backward compat — all banks are regenerated). A new-only CHECK re-validates
existing rows, so old-kind rows would block the ALTER: we CLEAR stage_questions first
(regeneration repopulates). No RLS change (columns/constraint on an already-policied
table). Rollback restores the original CHECK + drops primary_signal.
"""

from alembic import op
import sqlalchemy as sa

revision = "0045_bank_spoken_fields"
down_revision = "0044_interview_engine_version"
branch_labels = None
depends_on = None

_CK = "stage_questions_question_kind_check"
_NEW = (
    "question_kind IN ('experience_check', 'behavioral', "
    "'technical_scenario', 'compliance_binary')"
)
_OLD = (
    "question_kind IN ('technical_depth', 'behavioral_star', "
    "'compliance_binary', 'open_culture')"
)


def upgrade() -> None:
    # Dev-mode clean switch: no row may survive the taxonomy change. Banks are
    # regenerated by the recruiter; clearing the children leaves bank rows intact
    # (they go back to draft/reviewing on regenerate).
    op.execute("DELETE FROM stage_questions")
    op.add_column(
        "stage_questions",
        sa.Column("primary_signal", sa.Text(), nullable=True),
    )
    op.drop_constraint(_CK, "stage_questions", type_="check")
    op.create_check_constraint(_CK, "stage_questions", _NEW)


def downgrade() -> None:
    # Symmetric: clear the new-taxonomy rows so the restored old CHECK validates.
    op.execute("DELETE FROM stage_questions")
    op.drop_constraint(_CK, "stage_questions", type_="check")
    op.create_check_constraint(_CK, "stage_questions", _OLD)
    op.drop_column("stage_questions", "primary_signal")
```

- [ ] **Step 5: Apply up/down/up + verify**

Run:
```bash
docker compose run --rm nexus alembic upgrade head
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: all three succeed; `docker compose run --rm nexus alembic current` shows `0045` (revision id is the
short-numeric repo convention; the FILE is named `0045_bank_spoken_fields.py`).

- [ ] **Step 6: Run the ORM test**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_migration_0045.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Update the stale 0026 CHECK test**

`tests/test_question_banks_migration_0026.py` asserts the OLD allowed values. Update its assertions to the
new taxonomy (it now documents the post-0045 CHECK). Run it:
`docker compose run --rm nexus pytest tests/test_question_banks_migration_0026.py -v` → PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/migrations/versions/0045_bank_spoken_fields.py backend/nexus/app/modules/question_bank/models.py backend/nexus/tests/question_bank/test_migration_0045.py backend/nexus/tests/test_question_banks_migration_0026.py
git commit -m "feat(question-bank): migration 0045 — primary_signal + spoken question_kind taxonomy"
```

---

## Task 3: `GeneratedQuestion` + API schemas — primary_signal, new question_kind, spoken text cap

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/schemas.py`
- Test: `backend/nexus/tests/test_question_banks_schemas.py` (extend + update old-string constructions)

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/test_question_banks_schemas.py`:
```python
def test_generated_question_new_kind_and_primary_signal():
    from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric

    q = GeneratedQuestion(
        position=0,
        text="Walk me through a REST connector you built — how did you handle auth?",
        primary_signal="rest_api_integration",
        signal_values=["rest_api_integration", "auth_flows"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=["How did you handle pagination?", "What about retries on 5xx?"],
        positive_evidence=["names a real auth scheme", "describes token refresh", "mentions error handling"],
        red_flags=["vague 'just used the SDK'", "no error handling"],
        rubric=QuestionRubric(
            excellent="Names scheme, refresh, and failure handling concretely.",
            meets_bar="Describes the auth scheme and basic error handling.",
            below_bar="Cannot describe how auth worked at all.",
        ),
        evaluation_hint="Looking for hands-on connector ownership, not SDK hand-waving.",
        question_kind="technical_scenario",
    )
    assert q.primary_signal == "rest_api_integration"
    assert q.question_kind == "technical_scenario"


def test_generated_question_rejects_old_kind():
    import pytest
    from pydantic import ValidationError
    from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric

    with pytest.raises(ValidationError):
        GeneratedQuestion(
            position=0, text="x" * 20, primary_signal="s", signal_values=["s"],
            estimated_minutes=1.0, is_mandatory=False, follow_ups=[],
            positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
            rubric=QuestionRubric(excellent="a" * 20, meets_bar="b" * 20, below_bar="c" * 20),
            evaluation_hint="e" * 10, question_kind="technical_depth",  # old → rejected
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_schemas.py -k "new_kind or rejects_old_kind" -v`
Expected: FAIL — `primary_signal` not a field / `technical_depth` still accepted.

- [ ] **Step 3: Edit `GeneratedQuestion`**

In `backend/nexus/app/modules/question_bank/schemas.py`, in `class GeneratedQuestion`:

(a) Tighten the spoken cap on `text`:
```python
    text: str = Field(
        ..., min_length=10, max_length=240,
        description=(
            "SHORT, single-focus, SPOKEN lead question (~200 chars). One ask — no "
            "'and… and…'. Depth lives in follow_ups, asked one at a time."
        ),
    )
```

(b) Add `primary_signal` right after `text` (no validator — streaming-safe per D5; the
`primary_signal ∈ signal_values` rule is enforced in `validate_streamed_question`, Task 9):
```python
    primary_signal: str = Field(
        ..., min_length=1,
        description=(
            "The SINGLE signal value this lead question opens. Must be one of "
            "signal_values. Makes thread-satisfaction crisp (thread done when "
            "primary_signal is covered); signal_values stays the broader set the "
            "thread can cover."
        ),
    )
```

(c) Switch the `question_kind` Literal to the new taxonomy:
```python
    question_kind: Literal[
        "experience_check",
        "behavioral",
        "technical_scenario",
        "compliance_binary",
    ] = Field(
        ...,
        description=(
            "Refined spoken taxonomy: experience_check (claim verification) · "
            "behavioral (true STAR) · technical_scenario (verbal design/depth) · "
            "compliance_binary (hard yes/no gate). Each generation phase may emit "
            "only its phase's kinds (see actors.PHASE_QUESTION_KINDS)."
        ),
    )
```

(d) Add **per-question `difficulty`** (doc 12 #3 / doc 13 — verified the current generator does NOT set it;
it falls back to the stage difficulty uniformly). Optional so existing `GeneratedQuestion` constructions
don't break; the prompt (Task 8) instructs the LLM to set it; persistence falls back to the stage difficulty
when null (Task 7); the eval (Task 15) asserts the LLM actually sets it:
```python
    difficulty: Literal["easy", "medium", "hard"] | None = Field(
        default=None,
        description=(
            "Per-question difficulty the GENERATOR sets (drives the brain's grading "
            "strictness). None falls back to the stage difficulty at persistence."
        ),
    )
```

- [ ] **Step 4: Surface the new fields on `QuestionResponse` (read side — needed for FE sections)**

In the same file, `QuestionResponse`: add `question_kind: str`, `primary_signal: str | None = None`, and
`difficulty: str | None = None` after `evaluation_hint`:
```python
    question_kind: str
    primary_signal: str | None = None
    difficulty: str | None = None
```
> Scope note: the recruiter `CreateQuestionBody`/`UpdateQuestionBody` are deliberately **not** extended in
> M2. A generated question's `primary_signal`/`question_kind`/`difficulty` are preserved on a PATCH (the
> service updates only `exclude_unset` fields, so unlisted fields are untouched); a hand-written question
> takes the column defaults (`question_kind` server-default `technical_scenario` → Technical section;
> `primary_signal` NULL — tolerated). Recruiter editing of the AI's `primary_signal` is a later add, not an
> M2 acceptance requirement. Tighten `CreateQuestionBody.text`/`UpdateQuestionBody.text` `max_length` to 240
> to match the spoken rule (a one-line cap change, no service logic touched).

- [ ] **Step 5: Update bank-side tests that construct `GeneratedQuestion` with old strings**

These are **bank-side** (NOT v1 engine) and are in-scope per D1. In each, swap `question_kind="technical_depth"`
→ `"technical_scenario"`, `"behavioral_star"` → `"behavioral"`, and add a `primary_signal=<one of
signal_values>` kwarg to every `GeneratedQuestion(...)` construction:
- `tests/test_question_banks_schemas.py`
- `tests/test_question_banks_service.py`
- `tests/test_question_banks_actors.py`
- `tests/test_question_banks_events.py`
- `tests/question_bank/test_validate_splits.py`

> Tip: `grep -n "GeneratedQuestion(" tests/test_question_banks_*.py tests/question_bank/test_validate_splits.py`
> to enumerate sites. A construction missing `primary_signal` now fails (it's required), so the test run
> will flag any you miss.

- [ ] **Step 6: Run the schema + bank-side suites**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_schemas.py tests/question_bank/test_validate_splits.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/question_bank/schemas.py backend/nexus/tests/test_question_banks_schemas.py backend/nexus/tests/test_question_banks_service.py backend/nexus/tests/test_question_banks_actors.py backend/nexus/tests/test_question_banks_events.py backend/nexus/tests/question_bank/test_validate_splits.py
git commit -m "feat(question-bank): primary_signal + spoken taxonomy on GeneratedQuestion + API models"
```

---

## Task 4: Relax `QuestionConfig.question_kind` to `str` + project `primary_signal` (interview_runtime)

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py` (`QuestionConfig`)
- Modify: `backend/nexus/app/modules/interview_runtime/service.py` (`build_session_config` projection)
- Test: `backend/nexus/tests/interview_runtime/test_schemas.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/interview_runtime/test_schemas.py`:
```python
def test_question_config_question_kind_accepts_any_str():
    """Read projection is a relaxed str during v1 coexistence (D1) — old AND new
    strings both validate; enforcement lives at the DB CHECK + GeneratedQuestion."""
    from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric

    base = dict(
        id="q1", position=0, text="x" * 12, signal_values=["s"], estimated_minutes=1.0,
        is_mandatory=False, follow_ups=[], positive_evidence=["a", "b", "c"],
        red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="e", meets_bar="m", below_bar="b"),
        evaluation_hint="hint",
    )
    # legacy v1 string still validates (untouched backstop)
    assert QuestionConfig(**base, question_kind="technical_depth").question_kind == "technical_depth"
    # new taxonomy validates
    assert QuestionConfig(**base, question_kind="technical_scenario").question_kind == "technical_scenario"


def test_question_config_primary_signal_optional():
    from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric

    cfg = QuestionConfig(
        id="q1", position=0, text="x" * 12, signal_values=["s"], estimated_minutes=1.0,
        is_mandatory=False, follow_ups=[], positive_evidence=["a", "b", "c"],
        red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="e", meets_bar="m", below_bar="b"),
        evaluation_hint="hint", question_kind="behavioral", primary_signal="s",
    )
    assert cfg.primary_signal == "s"
    # default None when omitted
    cfg2 = cfg.model_copy(update={"primary_signal": None})
    assert cfg2.primary_signal is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_schemas.py -k "question_kind_accepts_any_str or primary_signal_optional" -v`
Expected: FAIL — `technical_scenario`/`primary_signal` rejected by the current Literal/missing field.

- [ ] **Step 3: Edit `QuestionConfig`**

In `backend/nexus/app/modules/interview_runtime/schemas.py`, in `class QuestionConfig`, replace the
`question_kind: Literal[...]` field with a relaxed `str` and add `primary_signal`:
```python
    question_kind: str = Field(
        default="technical_scenario",
        description=(
            "RELAXED READ PROJECTION (interview-engine-v2 M2, decision D1). The "
            "canonical taxonomy (experience_check | behavioral | technical_scenario "
            "| compliance_binary) is enforced at the WRITE boundary — the "
            "GeneratedQuestion generator model + the stage_questions.question_kind DB "
            "CHECK. This field is intentionally an unconstrained `str` (NOT a union) "
            "during v1 coexistence so the reference-only v1 engine suite + "
            "sample_session_config.json (which read QuestionConfig with the legacy "
            "strings 'behavioral_star'/'technical_depth') stay a TRUE untouched "
            "regression backstop. Tighten to the new Literal at M6 when v1 is retired."
        ),
    )
    primary_signal: str | None = Field(
        default=None,
        description=(
            "The single signal value the lead question opens (the v2 brain's crisp "
            "thread-satisfaction key). Projected from stage_questions.primary_signal; "
            "None for legacy/hand rows. signal_values stays the broader coverable set."
        ),
    )
```
(The `difficulty` field already exists and is unchanged.)

- [ ] **Step 4: Project `primary_signal` in `build_session_config`**

In `backend/nexus/app/modules/interview_runtime/service.py`, in the `QuestionConfig(...)` construction
(~line 232), add after `difficulty=...`:
```python
                    primary_signal=q.primary_signal,
```

- [ ] **Step 5: Update `tests/interview_runtime/*` that INSERT `stage_questions`**

These seed `stage_questions` rows and now hit the new DB CHECK (D1 caveat). In each, swap any seeded
`question_kind` value to the new taxonomy and (optionally) set `primary_signal`. Affected (verify each by
grep; only those that INSERT rows, not those that build `QuestionConfig` in-memory):
- `tests/interview_runtime/test_question_kind_population.py` — this test specifically asserts question_kind
  round-trips through `build_session_config`; rewrite its expected values to the new taxonomy.
- `tests/interview_runtime/test_build_session_config_difficulty.py`
- `tests/interview_runtime/test_build_session_config_keyterms.py`
- `tests/interview_runtime/test_service.py`
- `tests/interview_runtime/test_signal_metadata_plumbing.py`

> `grep -rn "question_kind" tests/interview_runtime/` to enumerate. A row INSERTed with an old kind string
> now raises a CHECK violation, so the run flags any you miss. Do NOT touch `tests/interview_engine/*` or
> `sample_session_config.json` — they build/read `QuestionConfig` in-memory and stay green via the `str`
> relax (that is the whole point of D1).

- [ ] **Step 6: Run the interview_runtime suite**

Run: `docker compose run --rm nexus pytest tests/interview_runtime -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py backend/nexus/app/modules/interview_runtime/service.py backend/nexus/tests/interview_runtime/
git commit -m "feat(engine-v2): relax QuestionConfig.question_kind to str + project primary_signal"
```

---

## Task 5: `AIConfig.question_bank_prompt_version` + versioned bank-gen prompt loader

**Files:**
- Modify: `backend/nexus/app/config.py` (Settings — add field near `question_bank_*`)
- Modify: `backend/nexus/app/ai/config.py` (AIConfig — add property)
- Modify: `backend/nexus/.env.example`
- Test: `backend/nexus/tests/question_bank/test_prompt_version_config.py`

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/question_bank/test_prompt_version_config.py`:
```python
"""Bank-gen prompts live in prompts/v2 (the spoken-question rewrite)."""

from app.ai.config import AIConfig


def test_default_bank_prompt_version_is_v2():
    assert AIConfig().question_bank_prompt_version == "v2"


def test_bank_prompt_version_env_override(monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_PROMPT_VERSION", "v1")
    assert AIConfig().question_bank_prompt_version == "v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_prompt_version_config.py -v`
Expected: FAIL — attribute missing.

- [ ] **Step 3: Add Settings field + AIConfig property**

In `backend/nexus/app/config.py`, near the existing `question_bank_model`/`question_bank_effort` fields, add:
```python
    # Bank-gen prompts: spoken-question rewrite lives in prompts/v2 (engine-v2 M2).
    question_bank_prompt_version: str = "v2"
```
In `backend/nexus/app/ai/config.py`, near the existing `question_bank_*` properties, add:
```python
    @property
    def question_bank_prompt_version(self) -> str:
        return self._settings.question_bank_prompt_version
```
In `backend/nexus/.env.example`, near the other `QUESTION_BANK_*` vars:
```bash
# Bank-gen prompt set version (spoken-question rewrite). v1 = legacy written-exam prompts.
QUESTION_BANK_PROMPT_VERSION=v2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_prompt_version_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/.env.example backend/nexus/tests/question_bank/test_prompt_version_config.py
git commit -m "feat(question-bank): question_bank_prompt_version (default v2) for the spoken prompt set"
```

---

## Task 6: `BANK_QUESTION_ADDED` pub/sub event + `PHASE_QUESTION_KINDS` partition

**Files:**
- Modify: `backend/nexus/app/pubsub.py` (add the event constant)
- Modify: `backend/nexus/app/modules/question_bank/actors.py` (add `PHASE_QUESTION_KINDS` + phase labels)
- Test: `backend/nexus/tests/test_question_banks_events.py` (extend) + `tests/question_bank/test_phase_kinds.py`

- [ ] **Step 1: Write the failing tests**

`backend/nexus/tests/question_bank/test_phase_kinds.py`:
```python
"""Generation phase ↔ question_kind partition (decision D3)."""

from app.modules.question_bank.actors import PHASE_QUESTION_KINDS


def test_partition_is_total_and_disjoint():
    behavioral = PHASE_QUESTION_KINDS["behavioral"]
    technical = PHASE_QUESTION_KINDS["technical"]
    assert behavioral == {"experience_check", "behavioral", "compliance_binary"}
    assert technical == {"technical_scenario"}
    assert behavioral.isdisjoint(technical)


def test_event_constant_present():
    from app import pubsub
    assert pubsub.Events.BANK_QUESTION_ADDED == "bank.question_added"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_phase_kinds.py -v`
Expected: FAIL — constant/partition missing.

- [ ] **Step 3: Add the event constant**

In `backend/nexus/app/pubsub.py`, in `class Events`, add:
```python
    BANK_QUESTION_ADDED = "bank.question_added"
```

- [ ] **Step 4: Add the phase partition (constant only here; used in Tasks 9–11)**

In `backend/nexus/app/modules/question_bank/actors.py`, near the other module constants (after
`BEHAVIORAL_BUDGET_MIN`), add:
```python
# Inline runaway ceiling per streamed generation call (decision D2, point 3) —
# a safety stop, NOT a time-budget cap. Only fires on pathological runaway.
STREAM_QUESTION_CEILING = 12

# Generation phase ↔ allowed question_kind partition (decision D3). Each phase's
# rewritten prompt may emit only its phase's kinds; wipe/count/section-grouping use
# this map so the per-phase regen + UI sections survive the taxonomy switch.
PHASE_QUESTION_KINDS: dict[str, set[str]] = {
    "behavioral": {"experience_check", "behavioral", "compliance_binary"},
    "technical": {"technical_scenario"},
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_phase_kinds.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/pubsub.py backend/nexus/app/modules/question_bank/actors.py backend/nexus/tests/question_bank/test_phase_kinds.py
git commit -m "feat(question-bank): BANK_QUESTION_ADDED event + phase↔kind partition"
```

---

## Task 7: Incremental persistence + per-phase wipe helpers (service.py)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/service.py`
- Test: `backend/nexus/tests/test_question_banks_service.py` (extend)

> These are the streaming-friendly write primitives: persist ONE question (so we can commit + emit per
> question) and wipe AI questions by phase (so regen-by-phase works under the new taxonomy). The existing
> `write_generated_questions` (whole-array) stays for any non-streaming caller and as a reference; the new
> streaming path uses `persist_one_question` instead.

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/test_question_banks_service.py` (reuse the existing bank/snapshot seed
fixtures in this module):
```python
@pytest.mark.asyncio
async def test_persist_one_question_appends_at_next_position(bypass_db, seeded_bank):
    from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric
    from app.modules.question_bank.service import persist_one_question, get_bank_questions

    bank = seeded_bank  # an empty StageQuestionBank in the seed
    q = GeneratedQuestion(
        position=0, text="Tell me about a deploy that went wrong.",
        primary_signal="incident_response", signal_values=["incident_response"],
        estimated_minutes=2.0, is_mandatory=True, follow_ups=["What did you change after?"],
        positive_evidence=["names the failure", "describes the fix", "owns the mistake"],
        red_flags=["blames others", "no concrete detail"],
        rubric=QuestionRubric(excellent="a" * 20, meets_bar="b" * 20, below_bar="c" * 20),
        evaluation_hint="ownership of a real incident", question_kind="behavioral",
    )
    new_id = await persist_one_question(
        bypass_db, bank=bank, question=q, source="ai_generated", position=0,
        stage_difficulty="medium",
    )
    rows = await get_bank_questions(bypass_db, bank.id)
    assert len(rows) == 1
    assert str(rows[0].id) == str(new_id)
    assert rows[0].primary_signal == "incident_response"
    assert rows[0].question_kind == "behavioral"
    assert rows[0].difficulty == "medium"


@pytest.mark.asyncio
async def test_wipe_ai_questions_of_phase(bypass_db, seeded_bank_with_mixed_kinds):
    from app.modules.question_bank.service import wipe_ai_questions_of_phase, get_bank_questions

    bank = seeded_bank_with_mixed_kinds  # has experience_check + behavioral + technical_scenario rows
    deleted = await wipe_ai_questions_of_phase(bypass_db, bank=bank, phase="behavioral")
    assert deleted >= 1
    remaining = {r.question_kind for r in await get_bank_questions(bypass_db, bank.id)}
    assert remaining == {"technical_scenario"}  # only the technical phase survives
```

> If `seeded_bank` / `seeded_bank_with_mixed_kinds` don't already exist, add minimal fixtures to this test
> module that create a `StageQuestionBank` (+ a few `StageQuestion` rows for the mixed-kinds one) under the
> bypass session, mirroring the existing seed helpers in `tests/test_question_banks_service.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_service.py -k "persist_one_question or wipe_ai_questions_of_phase" -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement the helpers**

In `backend/nexus/app/modules/question_bank/service.py` (near `write_generated_questions`):
```python
async def persist_one_question(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    question: GeneratedQuestion,
    source: str,
    position: int,
    stage_difficulty: str | None,
) -> uuid.UUID:
    """Insert ONE generated question and return its id.

    Streaming-path primitive (engine-v2 M2): the actor calls this per question so it
    can commit + publish BANK_QUESTION_ADDED incrementally. Position is assigned by
    the caller in stream order; a final re-pack happens once the stream completes.
    Per-question difficulty falls back to the stage difficulty when the generator
    leaves it null (matches write_generated_questions).
    """
    row = StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=position,
        source=source,
        text=question.text,
        signal_values=list(question.signal_values),
        estimated_minutes=question.estimated_minutes,
        is_mandatory=question.is_mandatory,
        follow_ups=list(question.follow_ups),
        positive_evidence=list(question.positive_evidence),
        red_flags=list(question.red_flags),
        rubric=question.rubric.model_dump(),
        evaluation_hint=question.evaluation_hint,
        question_kind=question.question_kind,
        primary_signal=question.primary_signal,
        difficulty=question.difficulty or stage_difficulty,   # per-question, fallback to stage
    )
    db.add(row)
    await db.flush()
    return row.id


async def wipe_ai_questions(db: AsyncSession, *, bank: StageQuestionBank) -> int:
    """Delete ALL AI-sourced questions for a bank (recruiter rows preserved); re-pack.

    Standalone version of the delete that write_generated_questions does inline. Used by
    the streaming path: Phase A wipe-at-start (clean regenerate) and the D7 failure-path
    wipe (so a failed bank shows zero, not a confusing partial set). Returns count deleted.
    """
    deleted = await db.execute(
        delete(StageQuestion).where(
            StageQuestion.bank_id == bank.id,
            StageQuestion.source.in_(["ai_generated", "ai_regenerated"]),
        )
    )
    await db.flush()
    remaining = await get_bank_questions(db, bank.id)
    for i, q in enumerate(remaining):
        q.position = i
    await db.flush()
    return deleted.rowcount or 0


async def wipe_ai_questions_of_phase(
    db: AsyncSession, *, bank: StageQuestionBank, phase: str,
) -> int:
    """Delete AI-sourced questions whose question_kind belongs to `phase`.

    Phase→kinds partition lives in actors.PHASE_QUESTION_KINDS (D3). Recruiter rows
    preserved. Re-packs remaining positions. Replaces the old wipe_ai_questions_of_kind
    (which keyed on a single question_kind == phase string — invalid once a phase emits
    several kinds).
    """
    from app.modules.question_bank.actors import PHASE_QUESTION_KINDS

    kinds = PHASE_QUESTION_KINDS[phase]
    deleted_result = await db.execute(
        delete(StageQuestion).where(
            StageQuestion.bank_id == bank.id,
            StageQuestion.source.in_(["ai_generated", "ai_regenerated"]),
            StageQuestion.question_kind.in_(kinds),
        )
    )
    deleted_count = deleted_result.rowcount or 0
    await db.flush()
    remaining = await get_bank_questions(db, bank.id)
    for i, q in enumerate(remaining):
        q.position = i
    await db.flush()
    return deleted_count
```
> Ensure `uuid` is imported at the top of `service.py` (it almost certainly is; add `import uuid` if not).
> `write_generated_questions` is **also updated** to persist the new `primary_signal` field — add
> `primary_signal=incoming.primary_signal,` to its `StageQuestion(...)` construction so any remaining caller
> stays correct. Likewise update `replace_question_in_place` (used by the single-question regen) to copy the
> new fields: `question.primary_signal = new_data.primary_signal` and
> `question.difficulty = new_data.difficulty or question.difficulty` (preserve the existing difficulty when
> the regen leaves it null).

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_service.py -k "persist_one_question or wipe_ai_questions_of_phase" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py backend/nexus/tests/test_question_banks_service.py
git commit -m "feat(question-bank): persist_one_question + wipe_ai_questions_of_phase (streaming write primitives)"
```

---

## Task 8: Rewrite the bank-gen prompts from scratch into `prompts/v2/` (spoken, doc 12 + doc 13 Surface A)

**Files:**
- Create: `backend/nexus/prompts/v2/question_bank_common.txt`
- Create: `backend/nexus/prompts/v2/question_bank_ai_screening.txt` (technical, chained)
- Create: `backend/nexus/prompts/v2/question_bank_ai_screening_behavioral.txt`
- Create: `backend/nexus/prompts/v2/question_bank_phone_screen.txt` (technical-only stage)
- Create: `backend/nexus/prompts/v2/question_bank_regenerate_one.txt`
- Test: `backend/nexus/tests/question_bank/test_v2_prompts_load.py`

> **Authoring rules (doc 13 Surface A — all prompts):** high reasoning is ON (the model is GPT-5.5 +
> reasoning); **start fresh** — do NOT port v1's wording. Outcome-framed (define what a great SPOKEN
> screening question is, then constraints), explicit completion criteria, a **self-critique pass** (build a
> 5–7-item excellence rubric, score the draft, revise before finalizing), and **few-shot good (spoken) vs
> bad (written-exam, multi-part)** examples. Prompt-context ordering (memory `feedback_prompt_context_ordering`):
> the user message already puts context (company/role/signals) before the document — keep that; the system
> prompt holds the rules. **Compact** (latency for bank-gen is not critical, but no bloat). The structured
> output is `GeneratedQuestion` (Task 3 schema): the generator MUST set `text`, `primary_signal` (∈
> `signal_values`), `signal_values`, `follow_ups` (the depth, one ask each), `difficulty`, `question_kind`,
> `rubric`, `positive_evidence`, `red_flags`, `evaluation_hint`. `rubric`/`positive_evidence`/`red_flags` are
> **brain-only** — they describe what a good *spoken* answer sounds like; they are never spoken to the
> candidate.

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/question_bank/test_v2_prompts_load.py`:
```python
"""The v2 spoken bank-gen prompt set exists, loads, and states the spoken contract."""

from app.ai.prompts import PromptLoader


def test_v2_bank_prompts_load_and_state_spoken_rules():
    loader = PromptLoader(version="v2")
    common = loader.get("question_bank_common")
    # spoken contract present
    assert "spoken" in common.lower()
    assert "follow_up" in common.lower() or "follow-up" in common.lower()
    assert "primary_signal" in common
    # new taxonomy named, old taxonomy absent
    for kind in ("experience_check", "behavioral", "technical_scenario", "compliance_binary"):
        assert kind in common
    for old in ("technical_depth", "behavioral_star", "open_culture"):
        assert old not in common


def test_v2_stage_and_phase_prompts_load():
    loader = PromptLoader(version="v2")
    for name in (
        "question_bank_ai_screening",
        "question_bank_ai_screening_behavioral",
        "question_bank_phone_screen",
        "question_bank_regenerate_one",
    ):
        body = loader.get(name)
        assert len(body) > 200


def test_v2_phase_prompts_constrain_kinds():
    loader = PromptLoader(version="v2")
    behavioral = loader.get("question_bank_ai_screening_behavioral")
    technical = loader.get("question_bank_ai_screening")
    # behavioral phase forbids technical_scenario; technical phase emits only technical_scenario
    assert "technical_scenario" in technical
    assert "experience_check" in behavioral
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_v2_prompts_load.py -v`
Expected: FAIL — `FileNotFoundError` (prompts/v2 files don't exist).

- [ ] **Step 3: Author `prompts/v2/question_bank_common.txt`**

Write the shared system header. Required sections (full prose, doc 13 Surface A):
1. **Role + outcome:** "You design SPOKEN screening questions for a live voice interview conducted by an AI
   interviewer to an Indian candidate. A great question is short, asks ONE thing, opens ONE primary signal,
   and can be answered aloud in 30–60 seconds. Depth is NOT crammed into the lead — it lives in `follow_ups`,
   asked one at a time."
2. **Spoken constraints (the core fix):** lead `text` ≤ ~200 chars; ONE ask, never "and… and…"; no lists;
   numbers spelled in words; conversational register. **Few-shot good vs bad** (verbatim, doc 13):
   - BAD (written-exam, multi-part): *"Outline the design: auth flow, request signing, pagination,
     error/retry strategy, and how you'd unit/integration test it. Be concrete about modules/classes."*
   - GOOD (spoken): *"If you built a custom REST connector, how would you handle authentication?"*
     with `follow_ups`: ["How would you deal with pagination?", "What's your retry strategy on a 5xx?",
     "How would you test it?"]
   - BAD: *"How many years of experience do you have, and what Workato work have you personally done in
     production, including the connectors you owned and the teams you worked with?"*
   - GOOD (experience_check): *"How many years have you worked hands-on with Workato in production?"* with
     `follow_ups`: ["Which connectors or recipes did you own?", "What team were you on?"]
3. **`primary_signal` rule:** every question names exactly one `primary_signal`, and it MUST be one of the
   question's `signal_values`. `signal_values` is the broader set the thread can credit.
4. **`question_kind` taxonomy + when each applies:**
   - `experience_check` — verify a claim (years, platforms, scope). No STAR.
   - `behavioral` — true STAR (a specific past situation; probe "I" not "we").
   - `technical_scenario` — verbal design/depth, think-aloud (NO coding).
   - `compliance_binary` — a hard yes/no gate (work authorization, shift hours, relocation).
5. **`difficulty` rule:** the generator sets per-question `difficulty` (easy/medium/hard) — it drives the
   brain's grading strictness. Calibrate to the stage difficulty + the signal's weight/knockout.
6. **`follow_ups`:** ordered, ONE ask each; they carry the depth that used to be crammed into `text`. 1–3.
7. **rubric / positive_evidence / red_flags:** describe what a good/acceptable/weak SPOKEN answer sounds
   like (not a written essay). These are evaluator-only and never read to the candidate.
8. **Self-critique pass (doc 13 — highest-leverage):** "Before finalizing each question, score it against
   this excellence rubric and revise: (a) Is it spoken, not written-exam? (b) ONE ask, not multi-part?
   (c) Opens exactly one primary_signal that's in signal_values? (d) Is the depth laddered into follow_ups,
   one ask each? (e) difficulty + question_kind set and consistent? (f) rubric anchors describe a SPOKEN
   answer? Revise any question that fails before emitting it."
9. **Completion criteria:** every mandatory/knockout signal for this stage has a question; budget is
   guidance not a hard cap; no overlap with questions already generated (they are listed in the user
   message — do not repeat them).

- [ ] **Step 4: Author the per-phase prompts**

`prompts/v2/question_bank_ai_screening_behavioral.txt` (behavioral phase):
- Phase scope: "You are generating the BEHAVIORAL phase. Emit ONLY these question_kind values:
  `experience_check`, `behavioral`, `compliance_binary`. Do NOT emit `technical_scenario` (that's the
  separate technical phase)."
- Focus: verify knockout/experience claims + true STAR behavioral; probe "I" not "we"; one binary
  gate per hard compliance requirement.
- Budget guidance: respect the stated behavioral budget; signal density over count.

`prompts/v2/question_bank_ai_screening.txt` (technical phase, chained):
- Phase scope: "You are generating the TECHNICAL phase. Emit ONLY `technical_scenario` questions. Do NOT
  emit `experience_check`/`behavioral`/`compliance_binary` (the behavioral phase covers those)."
- **Chaining:** "The behavioral questions already generated for THIS stage are listed in the user message
  under 'ALREADY-GENERATED BEHAVIORAL QUESTIONS — DO NOT OVERLAP'. Cover DIFFERENT angles; never restate a
  behavioral question as a technical one."
- Focus: verbal design/depth/think-aloud; depth in follow_ups; NO coding.

`prompts/v2/question_bank_phone_screen.txt` (technical-only stage, no behavioral phase):
- Same spoken rules; phone-screen is short; emit primarily `experience_check` + `compliance_binary` +
  light `technical_scenario`. (phone_screen has no behavioral phase prompt mapping — it runs as a single
  call; keep it self-contained.)

`prompts/v2/question_bank_regenerate_one.txt` (single-question regen, `SingleQuestionOutput`):
- Same spoken rules; produce ONE replacement `GeneratedQuestion` (so it MUST set `primary_signal` + a
  new-taxonomy `question_kind`) + a `reasoning` string; do not duplicate the other questions listed.

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_v2_prompts_load.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/prompts/v2/question_bank_common.txt backend/nexus/prompts/v2/question_bank_ai_screening.txt backend/nexus/prompts/v2/question_bank_ai_screening_behavioral.txt backend/nexus/prompts/v2/question_bank_phone_screen.txt backend/nexus/prompts/v2/question_bank_regenerate_one.txt backend/nexus/tests/question_bank/test_v2_prompts_load.py
git commit -m "feat(question-bank): rewrite bank-gen prompts from scratch into prompts/v2 (spoken, doc 12/13)"
```

---

## Task 9: Streaming generation core — `_generate_questions_for_kind` → `create_iterable`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py`
- Modify: `backend/nexus/app/modules/question_bank/service.py` (add `validate_streamed_question`)
- Test: `backend/nexus/tests/test_question_banks_actors.py` (streaming tests, LLM mocked at app/ai boundary)

> Per the Task 1 spike result, use `client.chat.completions.create_iterable(response_model=GeneratedQuestion,
> ...)` (or the confirmed fallback). The LLM is mocked at the `app/ai` boundary in tests — patch the client's
> `chat.completions.create_iterable` to return an async iterator over scripted `GeneratedQuestion`s.

- [ ] **Step 1: Add the per-question validator (service.py) + its test**

In `service.py`, add a streaming-safe single-question validator (no Pydantic validator, per D5). It takes
**primitives** (`snapshot_signals` + `snapshot_id`), NOT an ORM snapshot — under D6 the streaming caller has
already closed its read session and an ORM object would be expired:
```python
def validate_streamed_question(
    question: GeneratedQuestion,
    *,
    snapshot_signals: list[dict],
    snapshot_id,
    allowed_types: list[str],
) -> None:
    """Validate one streamed/regenerated question. Raises on a hallucinated signal / bad primary_signal.

    Streaming-safe sibling of validate_llm_output_against_snapshot's first pass (D5):
      - every signal_value must exist in the snapshot signals and be an allowed type
      - primary_signal must be one of signal_values (the ONLY place this is enforced — D5)
    The streaming caller SKIPS (does not persist/emit) a question that raises; the regen caller
    (Task 11d) lets it propagate. Budget + mandatory correction are NOT done here (post-stream, D2).
    """
    snapshot_by_value = {s["value"]: s for s in snapshot_signals}
    for value in question.signal_values:
        if value not in snapshot_by_value:
            raise SignalValueNotInSnapshotError(signal_value=value, snapshot_id=snapshot_id)
        if snapshot_by_value[value]["type"] not in allowed_types:
            raise SignalTypeNotAllowedError(
                signal_value=value,
                signal_type=snapshot_by_value[value]["type"],
                allowed_types=allowed_types,
            )
    if question.primary_signal not in question.signal_values:
        raise SignalValueNotInSnapshotError(
            signal_value=question.primary_signal, snapshot_id=snapshot_id
        )
```
Add a unit test in `tests/test_question_banks_service.py` covering: valid passes; bad signal raises;
`primary_signal` not in `signal_values` raises. (Callers pass `snapshot_signals=snapshot.signals,
snapshot_id=snapshot.id` while a session is live, or captured primitives after close.) Run it → PASS.

- [ ] **Step 2: Write the failing streaming test**

In `tests/test_question_banks_actors.py`, add (mock `create_iterable`):
```python
@pytest.mark.asyncio
async def test_generate_questions_for_kind_streams_and_persists(bypass_db, seeded_bank_ctx, monkeypatch):
    """Each streamed question is validated, persisted, and emitted one at a time."""
    from app.modules.question_bank import actors as bank_actors
    from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric

    # Scripted stream of 2 valid questions (signals must be in the seed snapshot).
    scripted = [
        GeneratedQuestion(
            position=0, text="How many years of hands-on Python in production?",
            primary_signal="python_experience", signal_values=["python_experience"],
            estimated_minutes=2.0, is_mandatory=True, follow_ups=["Which frameworks?"],
            positive_evidence=["states a number", "names frameworks", "production context"],
            red_flags=["vague", "only side projects"],
            rubric=QuestionRubric(excellent="a" * 20, meets_bar="b" * 20, below_bar="c" * 20),
            evaluation_hint="verify the experience claim", question_kind="experience_check",
        ),
        GeneratedQuestion(
            position=1, text="Tell me about a production incident you owned.",
            primary_signal="incident_response", signal_values=["incident_response"],
            estimated_minutes=2.0, is_mandatory=False, follow_ups=["What did you change after?"],
            positive_evidence=["specific incident", "owns the fix", "concrete detail"],
            red_flags=["blames others", "hypothetical"],
            rubric=QuestionRubric(excellent="a" * 20, meets_bar="b" * 20, below_bar="c" * 20),
            evaluation_hint="STAR ownership", question_kind="behavioral",
        ),
    ]

    async def _fake_iter(**kwargs):
        for q in scripted:
            yield q

    published = []
    async def _fake_publish(channel, event, payload, **kw):
        published.append((event, payload))

    monkeypatch.setattr(bank_actors, "_create_question_iterable", lambda **kw: _fake_iter())
    monkeypatch.setattr(bank_actors.pubsub, "publish", _fake_publish)

    persisted = await bank_actors._generate_questions_for_kind(
        bank_id=seeded_bank_ctx.bank_id, tenant_id=seeded_bank_ctx.tenant_id,
        job_id=seeded_bank_ctx.job_id, stage_id=seeded_bank_ctx.stage_id,
        snapshot_id=seeded_bank_ctx.snapshot_id, phase="behavioral",
        eligible_signals=seeded_bank_ctx.snapshot_signals, budget_minutes=3,
        prompt_name="question_bank_ai_screening_behavioral",
        start_position=0, prior_phase_questions=[],
    )
    assert len(persisted) == 2
    # two BANK_QUESTION_ADDED events, one per question
    added = [p for (e, p) in published if e == "bank.question_added"]
    assert len(added) == 2
```

> Fixture note: `_generate_questions_for_kind` opens its OWN `get_bypass_session()` (D6), so the
> `seeded_bank_ctx` fixture must **commit** the bank/stage/instance/job/snapshot rows (not just flush into
> `bypass_db`) so the helper's fresh sessions see them. Expose plain ids + `snapshot_signals` (a list) on the
> fixture — NOT live ORM objects (the helper takes primitives). The assertions that matter: **two persisted
> questions and two `bank.question_added` events**.

- [ ] **Step 3: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k streams_and_persists -v`
Expected: FAIL — the streaming helper / `_create_question_iterable` seam doesn't exist yet.

- [ ] **Step 4: Rewrite `_generate_questions_for_kind` to stream**

In `actors.py`, replace the body of `_generate_questions_for_kind` with the streaming version. Key shape
(adapt names to the existing module; keep the OTel span + structured logging):
```python
def _create_question_iterable(**kwargs):
    """Thin seam over instructor streaming so tests can monkeypatch it.

    Returns an async iterator of GeneratedQuestion. Uses the call path confirmed by
    the Task 1 spike (client.chat.completions.create_iterable). reasoning_effort is
    only forwarded when set (effort-gating contract).
    """
    client = get_openai_client()
    call_kwargs = dict(
        model=ai_config.question_bank_model,
        response_model=GeneratedQuestion,
        messages=kwargs["messages"],
        max_retries=1,
        metadata=kwargs.get("metadata", {}),
    )
    if ai_config.question_bank_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_effort
    return client.chat.completions.create_iterable(**call_kwargs)


async def _generate_questions_for_kind(
    *,
    bank_id,
    tenant_id,
    job_id,
    stage_id,
    snapshot_id,
    phase: str,                          # "behavioral" | "technical"
    eligible_signals: list[dict],        # the signal subset shown to the LLM for this phase
    budget_minutes: int,
    prompt_name: str,
    start_position: int,
    prior_phase_questions: list[dict],   # behavioral set fed into technical (chaining)
) -> list[GeneratedQuestion]:
    """Stream ONE phase: build prompt in a SHORT read session, then stream + persist + emit each.

    Per D6: NO session is held across the LLM stream. A short read session builds the prompt and
    captures primitives (FULL snapshot signals for validation, allowed_types, stage_difficulty), then
    CLOSES; each validated question persists + publishes in its OWN short session. Per D2: soft budget
    (no retry / no BudgetExceededError); STREAM_QUESTION_CEILING is a runaway guard. Returns the
    persisted questions (for behavioral→technical chaining + count).
    """
    # --- short read session: build prompt + capture primitives, then CLOSE (D6) ---
    async with get_bypass_session() as rdb:
        await rdb.execute(text(f"SET LOCAL app.current_tenant = '{str(tenant_id)}'"))
        bank = (await rdb.execute(select(StageQuestionBank).where(StageQuestionBank.id == bank_id))).scalar_one()
        stage = (await rdb.execute(select(JobPipelineStage).where(JobPipelineStage.id == stage_id))).scalar_one()
        instance = (await rdb.execute(select(JobPipelineInstance).where(JobPipelineInstance.id == stage.instance_id))).scalar_one()
        job = (await rdb.execute(select(JobPosting).where(JobPosting.id == job_id))).scalar_one()
        snapshot = (await rdb.execute(select(JobPostingSignalSnapshot).where(JobPostingSignalSnapshot.id == snapshot_id))).scalar_one()

        ctx = await build_question_context(rdb, job=job, instance=instance, stage=stage)
        bank_loader = PromptLoader(version=ai_config.question_bank_prompt_version)
        system_prompt = bank_loader.load_pair("question_bank_common", prompt_name)

        # Show the LLM only this phase's eligible signals (same swap-build-restore trick as today),
        # but VALIDATE against the FULL snapshot.
        full_signals = list(snapshot.signals)
        snapshot.signals = eligible_signals
        try:
            user_message = _build_user_message(
                job=job, snapshot=snapshot, company_profile=ctx.company_profile, stage=stage,
                pipeline_stages=ctx.pipeline_stages, prior_stages_questions=ctx.prior_stages_questions,
                prior_phase_questions=prior_phase_questions,   # NEW: within-bank chaining block
                budget_minutes=budget_minutes,                 # NEW: soft budget GUIDANCE block
            )
        finally:
            snapshot.signals = full_signals
        snapshot_signals = full_signals
        allowed_types = stage.signal_filter.get("include_types", [])
        stage_difficulty = stage.difficulty
    # rdb is closed here — nothing held across the stream.

    persisted: list[GeneratedQuestion] = []
    position = start_position
    with _tracer.start_as_current_span("openai.chat.completions.create_iterable"):
        set_llm_span_attributes(
            prompt_name=prompt_name, prompt_version=ai_config.question_bank_prompt_version,
            tenant_id=str(tenant_id), bank_id=str(bank_id), stage_id=str(stage_id),
            job_posting_id=str(job_id), model=ai_config.question_bank_model,
            reasoning_effort=ai_config.question_bank_effort, question_kind=phase,
        )
        async for q in _create_question_iterable(
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_message}],
            metadata={"bank_id": str(bank_id), "phase": phase, "tenant_id": str(tenant_id)},
        ):
            if len(persisted) >= STREAM_QUESTION_CEILING:
                logger.warning("question_bank.stream_ceiling_hit", bank_id=str(bank_id), phase=phase)
                break
            try:
                validate_streamed_question(
                    q, snapshot_signals=snapshot_signals, snapshot_id=snapshot_id,
                    allowed_types=allowed_types,
                )
            except (SignalValueNotInSnapshotError, SignalTypeNotAllowedError) as exc:
                logger.warning("question_bank.stream_question_skipped",
                               bank_id=str(bank_id), phase=phase, reason=str(exc)[:200])
                continue
            # commit-per-question in its OWN short session so the recruiter GET re-fetch sees it
            async with get_bypass_session() as qdb:
                await qdb.execute(text(f"SET LOCAL app.current_tenant = '{str(tenant_id)}'"))
                bank_row = (await qdb.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_id))).scalar_one()
                await persist_one_question(qdb, bank=bank_row, question=q, source="ai_generated",
                                           position=position, stage_difficulty=stage_difficulty)
                await qdb.commit()
            await pubsub.publish(
                pubsub.job_channel(job_id), pubsub.Events.BANK_QUESTION_ADDED,
                {"job_id": str(job_id), "bank_id": str(bank_id), "stage_id": str(stage_id),
                 "phase": phase, "source": "actor"},
                correlation_id=f"bank-stream-{bank_id}",
            )
            persisted.append(q)
            position += 1
    return persisted
```
> **Notes for the implementer:** (a) `_build_user_message` gains a `prior_phase_questions` param + a
> `budget_minutes` param — render an "ALREADY-GENERATED BEHAVIORAL QUESTIONS — DO NOT OVERLAP" block when
> non-empty, and a "BUDGET GUIDANCE (soft target, not a hard cap): ~{budget_minutes} min" block. Keep the
> existing context-ordering. (b) the OTel span name changes to reflect streaming. (c) DELETE the old
> `for attempt in range(MAX_BUDGET_RETRIES + 1)` retry loop and the `BudgetExceededError` catch from this
> function (D2). (d) `MAX_BUDGET_RETRIES`/`OPTIONAL_BUDGET_MARGIN_MIN` constants may stay (used by
> `_validate_budget_against_stage`'s remaining direct tests) but are no longer referenced here. (e) **D6:**
> the helper takes ids + opens its OWN short read session to build the prompt, then closes it BEFORE
> streaming — no session is held across the LLM stream; per-question writes use their own short sessions.
> `_build_user_message` is called inside the short read session (with live ORM objects) so the returned
> string is captured before the session closes.

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k streams_and_persists -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py backend/nexus/app/modules/question_bank/service.py backend/nexus/tests/test_question_banks_actors.py
git commit -m "feat(question-bank): stream per-question generation (create_iterable) + persist + emit"
```

---

## Task 10: `_generate_one_bank` streaming orchestration — chaining, post-stream correction, soft budget

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py`
- Test: `backend/nexus/tests/test_question_banks_actors.py` + `tests/test_question_banks_integration.py`

- [ ] **Step 1: Write the failing test**

Add an integration-style test (LLM mocked) asserting the full bank flow: behavioral phase streams first,
its questions are fed into the technical phase (chaining), all questions land, per-phase status is set,
mandatory correction ran, and the bank transitions to `reviewing`:
```python
@pytest.mark.asyncio
async def test_generate_one_bank_streams_both_phases_and_chains(bypass_db, seeded_bank_ctx, monkeypatch):
    from app.modules.question_bank import actors as bank_actors

    calls = []  # capture prior_phase_questions passed to each phase
    async def _fake_phase(**kwargs):
        calls.append((kwargs["phase"], len(kwargs["prior_phase_questions"])))
        # pretend behavioral persisted 2, technical persisted 3
        return 2 if kwargs["phase"] == "behavioral" else 3

    monkeypatch.setattr(bank_actors, "_generate_questions_for_kind", _fake_phase)
    # ... call the (refactored) _generate_one_bank against seeded_bank_ctx ...
    # assert: behavioral ran with 0 prior; technical ran with 2 prior (chaining);
    #         bank.generation_status_by_kind == {"behavioral": "reviewing", "technical": "reviewing"};
    #         bank.status transitioned to reviewing.
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k streams_both_phases_and_chains -v`
Expected: FAIL — `_generate_one_bank` not refactored to the new phase API / chaining.

- [ ] **Step 3: Rewrite `_generate_one_bank` to the D6 three-phase model (no session held across the stream)**

`_generate_one_bank` is refactored to take **primitives** (`bank_id`, `tenant_id`, `started_by`) and own its
short sessions — it no longer receives a long-lived `db`. Structure:

**Phase A — short session (load + prep + wipe + status):**
- open `get_bypass_session()`, `SET LOCAL app.current_tenant`; load bank/stage/instance/job/snapshot by id.
- compute `eligible_behavioral_signals = _filter_behavioral_eligible(snapshot.signals)`,
  `behavioral_prompt = STAGE_TYPE_TO_BEHAVIORAL_PROMPT.get(stage.stage_type)`,
  `technical_prompt = STAGE_TYPE_TO_PROMPT[stage.stage_type]` (raise if None — unchanged).
- `await wipe_ai_questions(db, bank=bank)` (clean regenerate); ensure `status='generating'` (the
  router/pipeline pre-marks it; leave as today). **commit; capture primitives** (`stage.duration_minutes`,
  `snapshot.id`, etc.); close.

**Phase B — NO held session (stream both phases via Task 9 helper):**
- **Behavioral** (skipped when `not eligible_behavioral_signals or behavioral_prompt is None` — preserve
  today's skip; record `behavioral_status = "skipped_no_eligible_signals"`):
  ```python
  behavioral_qs = await _generate_questions_for_kind(
      bank_id=bank_id, tenant_id=tenant_id, job_id=job_id, stage_id=stage_id, snapshot_id=snapshot_id,
      phase="behavioral", eligible_signals=eligible_behavioral_signals, budget_minutes=BEHAVIORAL_BUDGET_MIN,
      prompt_name=behavioral_prompt, start_position=0, prior_phase_questions=[],
  )
  ```
  `behavioral_status = "reviewing"` on success (catch + log → `"failed"`, `behavioral_qs = []` as today).
  Build the chaining payload + total from the RETURNED questions (no DB read needed):
  `prior = [{"text": q.text, "signal_values": q.signal_values, "primary_signal": q.primary_signal} for q in behavioral_qs]`;
  `behavioral_total = sum(float(q.estimated_minutes) for q in behavioral_qs)`.
- **Technical** (always runs):
  ```python
  technical_qs = await _generate_questions_for_kind(
      bank_id=bank_id, tenant_id=tenant_id, job_id=job_id, stage_id=stage_id, snapshot_id=snapshot_id,
      phase="technical", eligible_signals=<full snapshot signals>, prior_phase_questions=prior,
      budget_minutes=max(1, int(stage_duration - behavioral_total)), prompt_name=technical_prompt,
      start_position=len(behavioral_qs),
  )
  ```
  (capture the full snapshot signals as a primitive in Phase A). On failure: `technical_status = "failed"`,
  re-raise after the failure wipe (Phase C-fail) so `_run_stage_generation`'s contract holds (its tests
  assert specific exception types propagate).

**Phase C — short session (reconcile + status + keyterms + transition):**
- open a fresh `get_bypass_session()`, `SET LOCAL`; reload the bank + its questions.
- `bank.generation_status_by_kind = {"behavioral": behavioral_status, "technical": technical_status}`.
- **Reconcile (D2):** project the persisted rows to `GeneratedQuestion`, run
  `_apply_mandatory_correction_in_position_order` (flips `is_mandatory` only — verified it never adds),
  write the flips back to the ORM rows, re-pack positions 0..N-1, flush. Compute total minutes; if
  `> stage_duration`, `logger.warning("question_bank.budget_soft_warning", ...)` (do NOT raise).
- keyterm extraction (unchanged, best-effort); stamp `bank.prompt_version =
  ai_config.question_bank_prompt_version`, `pipeline_version_at_generation`, `stage_config_snapshot`,
  `is_stale=False`; `transition_to_reviewing_after_generation(bank, user_id=started_by)`; commit.

**Failure path (D7):** if the technical phase raised (or any unexpected error), open a short session and
`await wipe_ai_questions(db, bank=bank)` + set `generation_status_by_kind` + `transition_to_failed(bank, ...)`
+ commit, THEN re-raise the original exception. So a failed bank shows **zero** questions (no orphaned
partial set), recruiter rows preserved.

**Actor restructure (`generate_question_bank_stage` / `_run_stage_generation`):** these currently open ONE
`get_bypass_session()` and hold it across the whole call (verified). Refactor so the actor does NOT hold a
session across the stream: it resolves ids (load the bank to get `job_id`/`stage_id`/`snapshot_id` in a
short session, or accept them) and calls the new `_generate_one_bank(bank_id=, tenant_id=, started_by=)`,
which owns its phases. Publish `BANK_STATUS_CHANGED` post-(Phase C / failure) commit as today. **Apply the
same change to the pipeline path** (`_run_one_pipeline_stage_in_session`): it must not wrap the streaming
`_generate_one_bank` in a long-held session either — load/pre-mark in a short session, then call the
phase-owning orchestrator. (The pipeline still runs stages sequentially.)
> The terminal `BANK_STATUS_CHANGED → reviewing` publish triggers the FE re-fetch that reflects the
> Phase-C `is_mandatory` flips, so no extra event is needed for the reconcile.

- [ ] **Step 4: Remove the dead budget-retry tests**

Delete from `tests/test_question_banks_actors.py`:
- `test_generate_one_bank_retries_on_budget_violation_then_succeeds`
- `test_generate_one_bank_fails_after_repeated_budget_violations`
Keep the two direct `_validate_budget_against_stage` raise tests (mandatory/total) — they still pass
(the function is unchanged; it's just no longer called from the gen path).

- [ ] **Step 5: Run the actor + integration suites**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py tests/test_question_banks_integration.py -m "not prompt_quality" -v`
Expected: PASS (streaming flow green; no budget-retry references remain).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py backend/nexus/tests/test_question_banks_actors.py backend/nexus/tests/test_question_banks_integration.py
git commit -m "feat(question-bank): streaming bank gen — behavioral→technical chaining + soft budget, no retry loop"
```

---

## Task 11: Per-phase regenerate — `regenerate_kind_actor`, `RegenerateKindBody`, router (wipe-by-phase)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/schemas.py` (`RegenerateKindBody.kind` Literal)
- Modify: `backend/nexus/app/modules/question_bank/actors.py` (`regenerate_kind_actor`)
- Modify: `backend/nexus/app/modules/question_bank/router.py` (`regenerate_kind` handler)
- Test: `backend/nexus/tests/question_bank/test_regenerate_kind_endpoint.py` + `test_generation_status_by_kind.py`

- [ ] **Step 1: Write the failing test**

Update `tests/question_bank/test_regenerate_kind_endpoint.py` to use the new phase labels
(`"behavioral"`/`"technical"`) and assert the endpoint accepts them + rejects old strings; update
`tests/question_bank/test_generation_status_by_kind.py` to expect the `{"behavioral", "technical"}` keys.

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_regenerate_kind_endpoint.py tests/question_bank/test_generation_status_by_kind.py -v`
Expected: FAIL — new labels not accepted / keys mismatch.

- [ ] **Step 3: Update the schema + actor + router**

(a) `schemas.py` `RegenerateKindBody`:
```python
    kind: Literal["behavioral", "technical"]
```
(b) `router.py` `regenerate_kind`: replace `wipe_ai_questions_of_kind(db, bank=bank, kind=body.kind)` with
`wipe_ai_questions_of_phase(db, bank=bank, phase=body.kind)` (import the new helper via the public API /
intra-module path as appropriate).
(c) `actors.py` `regenerate_kind_actor`: branch on `phase in ("behavioral", "technical")`; select prompt +
eligible signals + budget per phase (behavioral → `_filter_behavioral_eligible` + `BEHAVIORAL_BUDGET_MIN`;
technical → full signals, budget = `stage.duration - other_phase_total`); compute "other phase total" via
`StageQuestion.question_kind.in_(PHASE_QUESTION_KINDS[other_phase])`; call the streaming
`_generate_questions_for_kind(phase=...)`; set `generation_status_by_kind[phase]`; run the post-stream
mandatory correction; transition + publish as today.

(d) **Single-question regen path** (`_regenerate_one_question`, the `regenerate_question` actor): switch it
to the **v2 bank loader** — `PromptLoader(version=ai_config.question_bank_prompt_version).load_pair(
"question_bank_common", "question_bank_regenerate_one")` — so the regenerated `GeneratedQuestion` carries
the v2 schema (`primary_signal` + `difficulty` + new `question_kind`). Without this it loads the v1 common
prompt and the LLM omits `primary_signal` (now required) → validation fails. (Task 7 already updated
`replace_question_in_place` to copy `primary_signal`/`difficulty`.) **Also validate `primary_signal`
(item 3):** `validate_llm_output_against_snapshot` checks `signal_values ⊆ snapshot` + types but **not**
`primary_signal ∈ signal_values`, and D5 removed the Pydantic validator — so `_regenerate_one_question`
must additionally call `validate_streamed_question(result.question, snapshot_signals=snapshot.signals,
snapshot_id=snapshot.id, allowed_types=allowed_types)` (the session is live there, so passing
`snapshot.signals` is fine). Without it a regen could set a `primary_signal` outside `signal_values`. Add a
test asserting that a regen whose `primary_signal ∉ signal_values` raises. Update its existing test in
`tests/test_question_banks_actors.py` if it asserts old kind strings.

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_regenerate_kind_endpoint.py tests/question_bank/test_generation_status_by_kind.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/schemas.py backend/nexus/app/modules/question_bank/actors.py backend/nexus/app/modules/question_bank/router.py backend/nexus/tests/question_bank/test_regenerate_kind_endpoint.py backend/nexus/tests/question_bank/test_generation_status_by_kind.py
git commit -m "feat(question-bank): per-phase regenerate (behavioral/technical) + wipe-by-phase"
```

---

## Task 12: Frontend — API types (`question_kind`, `primary_signal`, `generation_status_by_kind`)

**Files:**
- Modify: `frontend/app/lib/api/question-banks.ts`
- Test: (type-only; covered by `npm run type-check` + the composition test in Task 14)

- [ ] **Step 1: Extend the response types**

In `frontend/app/lib/api/question-banks.ts`:
- `QuestionResponse`: add
  ```ts
    question_kind: 'experience_check' | 'behavioral' | 'technical_scenario' | 'compliance_binary'
    primary_signal: string | null
    difficulty: 'easy' | 'medium' | 'hard' | null
  ```
- `BankResponse`: add
  ```ts
    generation_status_by_kind: Record<string, string>
  ```
(The recruiter `CreateQuestionBody`/`UpdateQuestionBody` FE types are unchanged — the backend bodies aren't
extended in M2; see Task 3 Step 4 scope note.)

- [ ] **Step 2: Type-check**

Run (from `frontend/app/`): `npm run type-check`
Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/lib/api/question-banks.ts
git commit -m "feat(question-bank-ui): add question_kind/primary_signal/generation_status_by_kind to API types"
```

---

## Task 13: Frontend — SSE hook handles `bank.question_added`

**Files:**
- Modify: `frontend/app/lib/hooks/use-questions-status-stream.ts`
- Test: `frontend/app/tests/lib/hooks/use-questions-status-stream.test.tsx` (extend)

- [ ] **Step 1: Write the failing test**

Extend the existing hook test: dispatch a `bank.question_added` event and assert the per-stage bank query
(`['bank', jobId, currentStageId]`) is invalidated (so the bank re-fetches and the new question appears).

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/app/`): `npm run test -- use-questions-status-stream`
Expected: FAIL — `bank.question_added` not handled.

- [ ] **Step 3: Handle the event**

In `use-questions-status-stream.ts` `onmessage`, add `bank.question_added` to the per-stage invalidation
condition:
```ts
              if (
                (ev.event === 'bank.status_changed' ||
                  ev.event === 'bank.question_updated' ||
                  ev.event === 'bank.question_added') &&
                currentStageId
              ) {
                void queryClient.invalidateQueries({
                  queryKey: ['bank', jobId, currentStageId],
                })
              }
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- use-questions-status-stream`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/hooks/use-questions-status-stream.ts frontend/app/tests/lib/hooks/use-questions-status-stream.test.tsx
git commit -m "feat(question-bank-ui): re-fetch bank on bank.question_added (live per-question reveal)"
```

---

## Task 14: Frontend — group `QuestionList` into Behavioral / Technical sections with per-section status

**Files:**
- Modify: `frontend/app/components/dashboard/question-bank/QuestionList.tsx`
- Create: `frontend/app/components/dashboard/question-bank/SectionStatus.tsx` (small per-section status pill)
- Test: `frontend/app/tests/components/QuestionListSections.test.tsx`

- [ ] **Step 1: Write the failing composition test**

`frontend/app/tests/components/QuestionListSections.test.tsx` (parent+child rendered together, per
`feedback_composition_tests`): render `QuestionList` with a `bank` whose questions span both phases
(`experience_check`+`behavioral` and `technical_scenario`) and `generation_status_by_kind = {behavioral:
'reviewing', technical: 'generating'}`. Assert: a "Behavioral" section header + a "Technical" section
header render; the experience_check/behavioral questions are under Behavioral; the technical_scenario
question is under Technical; the Technical section shows a "generating" status. Negative control: a bank
with only technical questions renders no empty Behavioral section.

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/app/`): `npm run test -- QuestionListSections`
Expected: FAIL — sections not implemented.

- [ ] **Step 3: Implement sections**

In `QuestionList.tsx`:
- Define the phase mapping (mirror backend D3):
  ```ts
  const PHASE_OF: Record<string, 'behavioral' | 'technical'> = {
    experience_check: 'behavioral', behavioral: 'behavioral', compliance_binary: 'behavioral',
    technical_scenario: 'technical',
  }
  ```
- Partition `bank.questions` into `behavioral` / `technical` by `PHASE_OF[q.question_kind]`.
- Render a section per non-empty phase: a header ("Behavioral" / "Technical") + a `<SectionStatus>` pill
  reading `bank.generation_status_by_kind[phase]` (e.g. generating → "Generating…", reviewing → hidden or
  "Ready", failed → "Failed", skipped_no_eligible_signals → "None applicable"), then the existing
  `QuestionCard` list for that phase. Preserve the existing empty-state when the whole bank is empty.
- Keep the existing `expandedId` behavior across sections.

Create `SectionStatus.tsx` as a tiny presentational component (px tokens; no new dependency) mapping a
status string → label + style. Reuse `BankStatusBadge` conventions if convenient.

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- QuestionListSections` then `npm run type-check` and `npm run lint`
Expected: PASS / zero errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/question-bank/QuestionList.tsx frontend/app/components/dashboard/question-bank/SectionStatus.tsx frontend/app/tests/components/QuestionListSections.test.tsx
git commit -m "feat(question-bank-ui): Behavioral/Technical sections with per-section status + live append"
```

---

## Task 15: Bank-gen prompt-eval suite (doc 13 — `@pytest.mark.prompt_quality`, 20+ cases)

**Files:**
- Create: `backend/nexus/tests/question_bank/prompt_evals/test_bank_gen_evals.py`
- Create: `backend/nexus/tests/question_bank/prompt_evals/__init__.py`

> Opt-in (real OpenAI API), modelled on `tests/test_question_banks_prompt_quality.py`. Runs the **streaming**
> generator against ≥20 diverse cases (≥3 role/seniority/signal mixes × happy/edge/adversarial) and asserts
> the spoken contract. These are quality gates the user runs on demand, not in the default suite.

- [ ] **Step 1: Write the eval suite**

`backend/nexus/tests/question_bank/prompt_evals/test_bank_gen_evals.py`:
```python
"""Bank-gen prompt-quality evals (engine-v2 M2, doc 13 Surface A). Opt-in:
    docker compose exec nexus pytest tests/question_bank/prompt_evals -m prompt_quality -s
"""
from __future__ import annotations

import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.modules.question_bank.schemas import GeneratedQuestion

pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]

# 20+ cases as (role, seniority, signals[], stage_user_message) tuples — build a
# helper that produces a realistic _build_user_message-shaped string per case.
CASES = [ ... ]  # ≥20 diverse: backend, data eng, support (UK-shift compliance), ML, etc.


async def _generate(case) -> list[GeneratedQuestion]:
    client = get_openai_client()
    loader = PromptLoader(version=ai_config.question_bank_prompt_version)
    system = loader.load_pair("question_bank_common", "question_bank_ai_screening")
    kwargs = dict(model=ai_config.question_bank_model, response_model=GeneratedQuestion,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": case.user_message}], max_retries=1)
    if ai_config.question_bank_effort:
        kwargs["reasoning_effort"] = ai_config.question_bank_effort
    return [q async for q in client.chat.completions.create_iterable(**kwargs)]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_questions_are_spoken_single_focus(case):
    qs = await _generate(case)
    assert qs, "generator produced no questions"
    for q in qs:
        # spoken: short lead, single ask
        assert len(q.text) <= 240, f"too long: {q.text!r}"
        assert " and " not in q.text.lower() or q.text.count("?") <= 1, f"multi-part: {q.text!r}"
        # primary_signal set and a member of signal_values
        assert q.primary_signal and q.primary_signal in q.signal_values
        # depth lives in follow_ups
        assert isinstance(q.follow_ups, list)
        # the generator set per-question difficulty (doc 12 #3 — not just the stage fallback)
        assert q.difficulty in {"easy", "medium", "hard"}, f"difficulty unset: {q.text!r}"
        # new-taxonomy kind set
        assert q.question_kind in {"experience_check", "behavioral", "technical_scenario", "compliance_binary"}


async def test_no_behavioral_technical_overlap_via_chaining():
    """Technical phase, fed the behavioral set, must not restate a behavioral question."""
    # generate behavioral, then technical with the behavioral set chained in; assert
    # no near-duplicate leads (simple token-overlap heuristic or an LLM-grader call).
    ...


async def test_rubric_never_leaks_into_text():
    """No question's spoken text/follow_ups contain rubric/evaluation phrasing."""
    qs = await _generate(CASES[0])
    banned = ("rubric", "red flag", "positive_evidence", "we're looking for", "meets_bar")
    for q in qs:
        blob = (q.text + " " + " ".join(q.follow_ups)).lower()
        assert not any(b in blob for b in banned)
```

> Fill `CASES` with ≥20 realistic cases (the existing prompt-quality test's `_phone_screen_user_message_*`
> helper is a template). Include adversarial cases: a signal set that tempts multi-part questions; a
> UK-shift compliance knockout (assert a `compliance_binary` appears); an underspecified role (assert no
> hallucinated signals — every `signal_value` ∈ the provided set).
> Note: the `" and "`/`?`-count "single-focus" check is a **crude placeholder** — an LLM-grader
> (`single_focus: bool` + `reason`) is the better signal. Since this is an opt-in eval the user runs on
> demand, ship the heuristic now and upgrade to an LLM-grader later; don't over-build it in M2.

- [ ] **Step 2: Run the evals (opt-in)**

Run: `docker compose up -d nexus && docker compose exec nexus pytest tests/question_bank/prompt_evals -m prompt_quality -s`
Expected: PASS (or surfaced quality gaps to feed back into the Task 8 prompts — iterate the prompt, not the
asserts).

- [ ] **Step 3: Confirm the default gate excludes them**

Run: `docker compose run --rm nexus pytest tests/question_bank/prompt_evals -m "not prompt_quality" -q`
Expected: 0 selected (the marker keeps them out of the default run).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/question_bank/prompt_evals/
git commit -m "test(question-bank): bank-gen prompt-quality eval suite (spoken/single-focus/primary_signal/no-overlap)"
```

---

## Task 16: Full regression gate + manual talk-test

**Files:** (no new files — verification)

- [ ] **Step 1: Migration up/down/up**

Run:
```bash
docker compose run --rm nexus alembic upgrade head
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: clean; `alembic current` shows `0045` (head).

- [ ] **Step 2: question_bank + interview_runtime suites green**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_* tests/question_bank tests/interview_runtime -m "not prompt_quality" -q`
Expected: PASS.

- [ ] **Step 3: v1 backstop UNTOUCHED + green (the D1 proof)**

Run: `docker compose run --rm nexus pytest tests/interview_engine -m "not prompt_quality" -q`
Expected: PASS — verifying the `str` relax kept the reference-only v1 suite green with **zero edits** to
`tests/interview_engine/*` or `sample_session_config.json`. (`tests/interview_engine/test_replay_failing_session.py`
may still error on its missing untracked `engine-events/*.json` fixture — that's the documented pre-existing
failure to IGNORE, not introduced by M2.)

- [ ] **Step 4: Module-boundary lint + frontend gates**

Run: `docker compose run --rm nexus pytest tests/test_module_boundaries.py -q`
Run (from `frontend/app/`): `npm run lint && npm run type-check && npm run test`
Expected: PASS / zero errors.

- [ ] **Step 5: Manual talk-test (the user's acceptance method)**

```bash
docker compose up --build           # backend + worker + redis
cd frontend/app && npm run dev       # recruiter dashboard :3000
```
Open a job's question-bank page for an `ai_screening` stage with confirmed signals and click
**Generate questions**. Confirm, by watching:
- Questions **appear one at a time** (not one big batch at the end) — Behavioral section fills first, then
  Technical.
- Each question is **short / single-focus / spoken**; the depth is in its `follow_ups` (expand a card).
- Each has a `primary_signal`, a `difficulty`, and a new-taxonomy `question_kind`.
- The Technical section's questions don't restate the Behavioral ones (chaining worked).
- Per-section status pills reflect generating → ready.

- [ ] **Step 6: Final commit (only if fixups were needed)**

```bash
git add -A
git commit -m "test(question-bank): M2 regression gate green; v1 backstop untouched"
```

---

## M2 acceptance checklist (run before declaring M2 done)

- [ ] R2 spike recorded PER-QUESTION CONFIRMED (or the documented per-set fallback).
- [ ] `alembic upgrade head` / `downgrade -1` / `upgrade head` clean (migration `0045`).
- [ ] `pytest tests/test_question_banks_* tests/question_bank tests/interview_runtime -m "not prompt_quality"` green.
- [ ] `pytest tests/interview_engine -m "not prompt_quality"` green — **v1 backstop untouched** (D1 proof);
      only the pre-existing replay-fixture error remains.
- [ ] `pytest tests/test_module_boundaries.py` green.
- [ ] Frontend `npm run lint && npm run type-check && npm run test` green (incl. the QuestionList sections
      composition test + the SSE-hook `bank.question_added` test).
- [ ] Bank-gen prompt-evals (`-m prompt_quality`) pass (or surfaced gaps fed back into the v2 prompts).
- [ ] Manual talk-test: generating a bank streams questions one-at-a-time into Behavioral/Technical
      sections; questions are short/spoken with depth in follow_ups; each has primary_signal + difficulty +
      new question_kind; no behavioral↔technical overlap.
- [ ] `git log --oneline` shows one focused commit per task; no unrelated file churn; HEAD still
      `feat/interview-engine-v2-m2`; the untracked `scripts/export_job_agent_context.py` was never staged.

## Self-review notes

- **Spec coverage (master §5 M2 deliverables):** migration `0045` (Task 2) ✓; schema additions —
  `models.py`/`schemas.py`/`QuestionConfig` (Tasks 2–4) ✓; rewritten `prompts/v2/question_bank_*` (Task 8) ✓;
  streaming structured output + persist + emit per question (Tasks 7, 9) ✓; behavioral→technical chaining
  (Task 10) ✓; FE sections + live append (Tasks 12–14) ✓; bank-gen prompt-eval suite (Task 15) ✓;
  per-question `difficulty` already shipped (0042) — the generator sets it (Task 9 persist + Task 8 prompt)
  and the evals assert it ✓.
- **CMI-2 (D1):** clean taxonomy at write (DB CHECK + `GeneratedQuestion`), relaxed `str` read projection —
  v1 backstop green untouched. Master plan §3a updated to match.
- **Decisions D2–D7** recorded above so they can't resurface mid-build: D2 removes the budget-retry loop
  (count ceiling + soft warning), D3 keeps per-phase regen working, D4 versions the prompts to v2, D5 keeps
  `GeneratedQuestion` validator-free for streaming (and makes `validate_streamed_question` the sole
  `primary_signal ∈ signal_values` gate), D6 pins the streaming transaction model (no session held across
  the LLM stream — three short sessions; the actor + pipeline path refactored to release the outer
  transaction), D7 wipes on the failure path so a failed bank shows zero, not a partial set.
- **No regex for intent** (memory): nothing in M2 adds intent/keyword matching. The only string-matching is
  the eval suite's quality heuristics + the SectionStatus label map — neither is runtime intent
  classification.
- **Type consistency:** `primary_signal` (str, ∈ signal_values), the four `question_kind` values, the two
  phase labels (`behavioral`/`technical`), and `BANK_QUESTION_ADDED = "bank.question_added"` are spelled
  identically across the migration, ORM, `GeneratedQuestion`, `QuestionConfig`, `PHASE_QUESTION_KINDS`, the
  pub/sub constant, the FE types, and the FE `PHASE_OF` map.
- **Out of scope (unchanged):** the v2 engine core (M3–M5), reporting/analysis, the JD-pipeline prompts
  (`prompts/v1/jd_*`), `question_bank_keyterms` + recruiter `question_refine_single`/`question_create_single`
  prompts (their output schemas don't carry `primary_signal`), and any latency tuning.
