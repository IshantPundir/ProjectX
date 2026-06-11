# Question-Bank Generation Speedup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make question-bank generation much faster and cheaper while holding/improving quality — collapse the two serial LLM calls into one streamed call, delete all obsolete two-phase code/schema/UI, rewrite the generation prompts to a SOTA standard, and downgrade the model via a QA-gated sweep.

**Architecture:** One streamed `create_iterable` call per bank (was behavioral→technical serial pair). The single call self-enforces kind-balance + knockout-gating + dimension distinctness via a rewritten, prescriptive prompt; the deterministic mandatory-correction reconcile stays as a code safety net. Keyterm extraction stays a separate cheap `gpt-5.4-nano` call. Clean-slate deletion: the `regenerate_kind` feature, the `generation_status_by_kind` column, and the per-phase frontend section UI are removed (no back-compat — pure dev mode).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async (asyncpg), Dramatiq, instructor + OpenAI, Alembic, Next.js (frontend/app), Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-06-11-question-bank-generation-speedup-design.md`

**Testing note (read first):** This plan is mostly deletion + refactor + prompt-content, where strict red-green TDD does not cleanly apply. The regression gate is the **existing `tests/question_bank/` suite passing** plus **grep-clean verification** of deleted symbols. Prompt quality is gated by the **manual QA-tester A/B** (Phase 6–7), not unit asserts. New unit tests are added only where the one-call shape changes behavior (Task 2.4). Run the backend suite with:
`docker compose run --rm nexus pytest tests/question_bank -q`

**Commit discipline:** one commit per task, on branch `feat/followups-governed-dimensions` (already checked out — do NOT switch branches). End every commit message with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

---

## File Map (what changes)

**Backend — `backend/nexus/`:**
- `app/config.py` — add `openai_question_bank_max_questions`; (Phase 7) update model/effort defaults.
- `app/ai/config.py` — add `question_bank_max_questions` property.
- `app/modules/question_bank/actors.py` — collapse to one call; delete `regenerate_kind_actor`, `PHASE_QUESTION_KINDS`, `STAGE_TYPE_TO_BEHAVIORAL_PROMPT`, `BEHAVIORAL_BUDGET_MIN`, `_filter_behavioral_eligible`, aliases; rename `_generate_questions_for_kind`→`_stream_bank_questions`; strip phase vocabulary + stale comments.
- `app/modules/question_bank/service.py` — delete `wipe_ai_questions_of_phase` (+ `wipe_ai_questions_of_kind` if orphaned) + `__all__` entries.
- `app/modules/question_bank/schemas.py` — delete `RegenerateKindBody`; remove `BankResponse.generation_status_by_kind`.
- `app/modules/question_bank/router.py` — delete `regenerate_kind` endpoint + dead imports + the `generation_status_by_kind` response mapping.
- `app/modules/question_bank/models.py` — delete the `generation_status_by_kind` column.
- `migrations/versions/0056_drop_generation_status_by_kind.py` — new migration.
- `prompts/v2/question_bank_common.txt` — rewrite from scratch.
- `prompts/v2/question_bank_ai_screening.txt` — rewrite as the unified single-call prompt.
- `prompts/v2/question_bank_ai_screening_behavioral.txt` — DELETE.
- `prompts/v2/question_bank_regenerate_one.txt`, `question_refine_single.txt`, `question_create_single.txt` — rewrite for consistency.
- `prompts/v1/question_bank_*` — audited + deleted (Task 4.7).
- `tests/question_bank/*` — update for the one-call shape; delete tests of deleted code.

**Frontend — `frontend/app/`:**
- `lib/api/question-banks.ts` — remove `generation_status_by_kind`.
- `components/dashboard/question-bank/QuestionList.tsx` — flatten (drop phase sectioning).
- `components/dashboard/question-bank/SectionStatus.tsx` — DELETE.
- `tests/components/QuestionListSections.test.tsx` — update/remove.

---

## Phase 0 — Config (config-driven mandate)

### Task 0.1: Move the runaway ceiling into config

**Files:**
- Modify: `backend/nexus/app/config.py:~219` (near `openai_question_bank_keyterm_model`)
- Modify: `backend/nexus/app/ai/config.py:~78` (near `question_bank_keyterm_model`)
- Modify: `backend/nexus/app/modules/question_bank/actors.py:96`

- [ ] **Step 1: Add the setting** in `app/config.py`, right after the `openai_question_bank_keyterm_model` line:

```python
    # Hard runaway-stop on streamed bank generation (NOT a time budget — a safety
    # cap on questions emitted per generation call). Config-driven, never hardcoded.
    openai_question_bank_max_questions: int = 12
```

- [ ] **Step 2: Expose it on AIConfig** in `app/ai/config.py`, after the `question_bank_keyterm_model` property:

```python
    @property
    def question_bank_max_questions(self) -> int:
        return self._settings.openai_question_bank_max_questions
```

- [ ] **Step 3: Replace the module constant** in `actors.py`. Delete the `STREAM_QUESTION_CEILING = 12` constant (lines ~94-96) and its comment. Replace its one use-site (the `if len(persisted) >= STREAM_QUESTION_CEILING:` check, ~line 515) with:

```python
                if len(persisted) >= ai_config.question_bank_max_questions:
```

And update the `ceiling=STREAM_QUESTION_CEILING` log kwarg (~line 520) to `ceiling=ai_config.question_bank_max_questions`.

- [ ] **Step 4: Verify** no references remain:

Run: `cd backend/nexus && grep -rn "STREAM_QUESTION_CEILING" app/ tests/`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/app/modules/question_bank/actors.py
git commit -m "refactor(question_bank): move runaway question ceiling into AIConfig"
```

---

## Phase 1 — Delete the `regenerate_kind` feature (backend)

> The one-call generator is fast; "regenerate one phase" is meaningless and fully covered by the existing full-stage regenerate (`generate_stage_questions`, which wipes all AI questions and re-runs). Delete the whole vertical.

### Task 1.1: Remove the `regenerate_kind` router endpoint + dead imports

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py`

- [ ] **Step 1: Delete the endpoint** — remove the entire `regenerate_kind` function and its `@router.post(".../banks/regenerate-kind")` decorator (router.py lines ~572-627).

- [ ] **Step 2: Remove now-dead imports** — delete `RegenerateKindBody` from the `schemas` import block (line 47) and `wipe_ai_questions_of_phase` from the `service` import block (line 62). Check whether `BackgroundTasks` and `BankAlreadyGeneratingError` are still used elsewhere in the file:

Run: `grep -n "BackgroundTasks\|BankAlreadyGeneratingError" backend/nexus/app/modules/question_bank/router.py`
If a symbol now has only its import line, remove that import too. (Leave it if other endpoints use it.)

- [ ] **Step 3: Verify the app imports** — the router module must still import cleanly:

Run: `cd backend/nexus && docker compose run --rm nexus python -c "import app.modules.question_bank.router"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/question_bank/router.py
git commit -m "feat(question_bank): remove regenerate-kind endpoint (obsolete with one-call gen)"
```

### Task 1.2: Delete the `regenerate_kind_actor`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py`

- [ ] **Step 1: Delete the actor** — remove the entire `regenerate_kind_actor` function and its `@dramatiq.actor(...)` decorator and the `# Actor: per-phase regenerate` section header (actors.py lines ~1631-1937, i.e. to end of file).

- [ ] **Step 2: Verify** no references remain:

Run: `cd backend/nexus && grep -rn "regenerate_kind_actor" app/ tests/`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py
git commit -m "feat(question_bank): delete regenerate_kind_actor"
```

### Task 1.3: Delete the `RegenerateKindBody` schema

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/schemas.py`

- [ ] **Step 1: Delete** the `RegenerateKindBody` class (schemas.py lines ~259-271, the class + its docstring).

- [ ] **Step 2: Verify:**

Run: `cd backend/nexus && grep -rn "RegenerateKindBody" app/ tests/`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/question_bank/schemas.py
git commit -m "feat(question_bank): delete RegenerateKindBody schema"
```

### Task 1.4: Delete the phase-wipe service functions

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/service.py`

- [ ] **Step 1: Check `wipe_ai_questions_of_kind` (line ~617) for callers** (it predates `_of_phase`):

Run: `cd backend/nexus && grep -rn "wipe_ai_questions_of_kind\|wipe_ai_questions_of_phase" app/ tests/`

- [ ] **Step 2: Delete `wipe_ai_questions_of_phase`** (service.py lines ~705-730). Delete `wipe_ai_questions_of_kind` too **only if** Step 1 showed no remaining callers outside the deleted code. Keep `wipe_ai_questions` (still used by the generator).

- [ ] **Step 3: Remove from `__all__`** (service.py:~1011) — delete the `"wipe_ai_questions_of_phase"` entry (and `"wipe_ai_questions_of_kind"` if deleted).

- [ ] **Step 4: Verify:**

Run: `cd backend/nexus && grep -rn "wipe_ai_questions_of_phase" app/ tests/`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py
git commit -m "feat(question_bank): delete phase-scoped wipe helpers"
```

### Task 1.5: Delete `PHASE_QUESTION_KINDS`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py`
- Modify: `backend/nexus/app/modules/question_bank/models.py` (a comment references it)

- [ ] **Step 1: Delete the `PHASE_QUESTION_KINDS` dict** (actors.py lines ~98-104) and its comment.

- [ ] **Step 2: Fix the stale model docstring** — in `models.py`, the `generation_status_by_kind` column doc references `actors.PHASE_QUESTION_KINDS`. This column is deleted in Phase 3, so leave the column for now but remove the dangling reference is unnecessary (the whole column goes away). Skip if Phase 3 follows immediately; otherwise just don't import the symbol.

- [ ] **Step 3: Verify:**

Run: `cd backend/nexus && grep -rn "PHASE_QUESTION_KINDS" app/ tests/`
Expected: no output (model docstring text mention is a string comment, acceptable until Phase 3 deletes the column).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py
git commit -m "feat(question_bank): delete PHASE_QUESTION_KINDS partition"
```

---

## Phase 2 — Collapse to one streamed call

### Task 2.1: Simplify `_build_user_message` (drop chaining + budget split)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py` (`_build_user_message`, lines ~167-348)

- [ ] **Step 1: Change the signature** — remove the `prior_phase_questions` and `budget_minutes` params. New signature:

```python
def _build_user_message(
    *,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
    company_profile: dict | None,
    stage: JobPipelineStage,
    pipeline_stages: list[dict],
    prior_stages_questions: list[dict],
) -> str:
```

- [ ] **Step 2: Delete the chaining block** — remove the entire `if prior_phase_questions:` block (lines ~269-302) that renders `# ALREADY-GENERATED BEHAVIORAL QUESTIONS — DO NOT OVERLAP`.

- [ ] **Step 3: Simplify the budget block** — replace the `budget_target = budget_minutes if ... else stage.duration_minutes` logic and the `Target time for this phase` wording with a single stage-duration budget. Use `stage.duration_minutes` directly; change the heading text from "FOR THIS STAGE (phase)" to a single-call framing and drop the `Target time for this phase` line (keep the eligible-signal density guidance).

- [ ] **Step 4: Strip stale comments** — remove all `engine-v2 M2`, `decision D2/D3/D6`, and `phase` references in this function's docstring/comments. Describe behavior as-is.

- [ ] **Step 5: Verify** the module imports:

Run: `cd backend/nexus && docker compose run --rm nexus python -c "import app.modules.question_bank.actors"`
Expected: no error (callers updated in 2.2/2.3 — if a NameError on `_build_user_message` args appears at call sites, that's expected until 2.3; this step only checks import-time syntax).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py
git commit -m "refactor(question_bank): _build_user_message — single-call (no phase chaining/budget split)"
```

### Task 2.2: Rename `_generate_questions_for_kind` → `_stream_bank_questions`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py` (function def ~377-603)

- [ ] **Step 1: Rename + drop phase params.** New signature (drop `phase`, `prior_phase_questions`, `budget_minutes` → use stage duration via the caller; keep `eligible_signals` = full set):

```python
async def _stream_bank_questions(
    *,
    bank_id: UUID,
    tenant_id: UUID,
    job_id: UUID,
    stage_id: UUID,
    snapshot_id: UUID,
    eligible_signals: list[dict],
    prompt_name: str,
    start_position: int,
    correlation_id: str = "",
) -> list[GeneratedQuestion]:
```

- [ ] **Step 2: Update the body** — remove `phase` from: the `_build_user_message(...)` call (drop `prior_phase_questions`/`budget_minutes`), the `metadata` dict (drop `"question_phase"`), the `set_llm_span_attributes(..., question_kind=phase)` call (drop the `question_kind` kwarg or set a static `"bank"`), the `effective_corr` f-string, and every `phase=phase` logging kwarg / pubsub payload `"phase"` key. Replace the `"phase": phase` pubsub field with nothing (remove the key).

- [ ] **Step 3: Update the docstring** — remove `decision D6` / `phase` wording; describe the single streamed call.

- [ ] **Step 4: Verify** the symbol is renamed everywhere:

Run: `cd backend/nexus && grep -rn "_generate_questions_for_kind" app/ tests/`
Expected: no output after Task 2.3 updates the caller; if test files reference it, fix them in Task 2.4.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py
git commit -m "refactor(question_bank): rename _generate_questions_for_kind -> _stream_bank_questions (one call)"
```

### Task 2.3: Rewrite `_generate_one_bank` Phase B to a single call

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py` (`_generate_one_bank`, lines ~606-922) + delete the helper constants/functions it used.

- [ ] **Step 1: Delete the two-phase scaffolding** at module top:
  - `BEHAVIORAL_BUDGET_MIN` constant (~line 85-92) + comment.
  - `STAGE_TYPE_TO_BEHAVIORAL_PROMPT` dict (~line 129-131).
  - `_filter_behavioral_eligible` function (~line 139-164).
  - The test-only aliases `_load_pipeline_context` / `_load_prior_stages_questions` (~line 135-136).

- [ ] **Step 2: Rewrite Phase A** in `_generate_one_bank` — delete `eligible_behavioral_signals` + `behavioral_prompt` resolution. Keep `technical_prompt = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)` but rename the local to `prompt_name` (it's now the single prompt). Keep the `transition_to_failed` guard when `prompt_name is None`.

- [ ] **Step 3: Rewrite Phase B** — replace the entire behavioral-then-technical block (lines ~701-772) with ONE call:

```python
    try:
        # ---- Phase B: single streamed generation call (NO held session) ----
        await _stream_bank_questions(
            bank_id=bank_id,
            tenant_id=tenant_id,
            job_id=job_id,
            stage_id=stage_id,
            snapshot_id=snapshot_id,
            eligible_signals=snapshot_signals,   # full set
            prompt_name=prompt_name,
            start_position=0,
            correlation_id=correlation_id,
        )
```

  Delete `behavioral_questions`, `behavioral_status`, `behavioral_total`, `technical_status`, the `prior` list, and the inner try/except that set `behavioral_status`.

- [ ] **Step 4: Rewrite Phase C status write** — replace the `bank.generation_status_by_kind = {...}` assignment (lines ~789-792) with **nothing** (the column is deleted in Phase 3; the `bank.status` state machine already records generating/reviewing/failed). Keep the reconcile (`_apply_mandatory_correction_in_position_order`), position re-pack, soft over-budget warning, keyterm extraction, metadata stamp, and `transition_to_reviewing_after_generation`.

- [ ] **Step 5: Rewrite the failure path** (lines ~900-922) — remove the `fbank.generation_status_by_kind = {...}` write; keep `wipe_ai_questions` + `transition_to_failed`.

- [ ] **Step 6: Strip stale comments** — remove `engine-v2 M2`, `decision D2/D3/D6/D7`, `3-phase model`, `behavioral`/`technical phase` wording from `_generate_one_bank`'s docstring; describe the one-call flow (Phase A load/wipe → Phase B one streamed call → Phase C reconcile).

- [ ] **Step 7: Verify** import + no orphan symbols:

Run: `cd backend/nexus && docker compose run --rm nexus python -c "import app.modules.question_bank.actors" && grep -rn "BEHAVIORAL_BUDGET_MIN\|STAGE_TYPE_TO_BEHAVIORAL_PROMPT\|_filter_behavioral_eligible\|_load_pipeline_context\|_load_prior_stages_questions\|prior_phase_questions\|behavioral_status\|technical_status" app/modules/question_bank/`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py
git commit -m "feat(question_bank): collapse behavioral+technical into one streamed generation call"
```

### Task 2.4: Update the actor tests for the one-call shape

**Files:**
- Modify: `backend/nexus/tests/question_bank/` (the generation tests)

- [ ] **Step 1: Find the affected tests:**

Run: `cd backend/nexus && grep -rln "_generate_questions_for_kind\|generation_status_by_kind\|behavioral\|prior_phase_questions\|regenerate_kind\|_load_pipeline_context\|_load_prior_stages_questions" tests/question_bank/`

- [ ] **Step 2: Update each hit** — point monkeypatches/asserts at `_stream_bank_questions`, assert it is called **once** per bank (not twice), drop assertions on `generation_status_by_kind` and behavioral/technical phase counts, and delete tests that exclusively covered `regenerate_kind_actor` / `wipe_ai_questions_of_phase`. Keep and retarget the deterministic mandatory-correction tests (they assert `_apply_mandatory_correction_in_position_order` flips `is_mandatory` to knockout-only — unchanged behavior).

- [ ] **Step 3: Run the suite:**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/question_bank -q`
Expected: PASS (no errors, no references to deleted symbols).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/question_bank
git commit -m "test(question_bank): update generation tests for one-call shape"
```

---

## Phase 3 — Drop the `generation_status_by_kind` column

### Task 3.1: Migration to drop the column

**Files:**
- Create: `backend/nexus/migrations/versions/0056_drop_generation_status_by_kind.py`

- [ ] **Step 1: Confirm the current head:**

Run: `cd backend/nexus && grep -rl "down_revision = None\|revision = " migrations/versions/ | xargs grep -h "^revision = " | sort | tail -5`
(Expected head per project memory: `0055`. Use the actual latest `revision` id as `down_revision`.)

- [ ] **Step 2: Write the migration:**

```python
"""drop generation_status_by_kind

Redundant with the bank.status state machine after the generation pipeline
collapsed to a single call (no per-phase partial state). Dev mode: column drop,
no data preserved.

Revision ID: 0056_drop_generation_status_by_kind
Revises: 0055_followups_governed_dimensions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056_drop_generation_status_by_kind"
down_revision = "0055_followups_governed_dimensions"  # set to the verified head
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("stage_question_banks", "generation_status_by_kind")


def downgrade() -> None:
    op.add_column(
        "stage_question_banks",
        sa.Column(
            "generation_status_by_kind",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
```

- [ ] **Step 3: Apply it:**

Run: `cd backend/nexus && docker compose run --rm nexus alembic upgrade head`
Expected: applies `0056` with no error.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/migrations/versions/0056_drop_generation_status_by_kind.py
git commit -m "migrate(question_bank): drop generation_status_by_kind column"
```

### Task 3.2: Remove the ORM column + schema field + router mapping

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/models.py:63-75`
- Modify: `backend/nexus/app/modules/question_bank/schemas.py:309-315`
- Modify: `backend/nexus/app/modules/question_bank/router.py:275`

- [ ] **Step 1: Delete the ORM column** — remove the `generation_status_by_kind` `mapped_column` block from `StageQuestionBank` (models.py lines 63-75).

- [ ] **Step 2: Delete the response field** — remove the `generation_status_by_kind: dict[str, str] = Field(...)` block from `BankResponse` (schemas.py lines ~309-315).

- [ ] **Step 3: Delete the router mapping** — remove the `generation_status_by_kind=bank.generation_status_by_kind,` line from the `BankResponse(...)` construction (router.py:275). Also check `service.get_banks_for_pipeline` for a `BankResponse(...)` construction that sets the field:

Run: `cd backend/nexus && grep -rn "generation_status_by_kind" app/`
Expected after edits: no output.

- [ ] **Step 4: Run the suite:**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/question_bank -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/models.py backend/nexus/app/modules/question_bank/schemas.py backend/nexus/app/modules/question_bank/router.py
git commit -m "feat(question_bank): remove generation_status_by_kind from ORM/schema/router"
```

---

## Phase 4 — SOTA prompt rewrite

> Prompts are **content, not code** — their acceptance test is the QA-tester A/B (Phase 6), not unit asserts. These tasks specify the required structure + must-keep constraints + new principles. The exact wording is authored during execution and iterated against QA. Apply [[feedback_prompt_principles_not_examples]]: teach principles + WHY, no replayed conversation examples.

### Task 4.1: Constraint inventory (safeguard before rewriting)

**Files:**
- Create: `docs/superpowers/specs/2026-06-11-question-bank-prompt-constraint-inventory.md`

- [ ] **Step 1: Read the current prompts** and list every load-bearing rule:

Run: `cd backend/nexus && cat prompts/v2/question_bank_common.txt prompts/v2/question_bank_ai_screening.txt prompts/v2/question_bank_ai_screening_behavioral.txt`

- [ ] **Step 2: Write the inventory** — a checklist of constraints the rewrite MUST preserve. At minimum: one-of/"or" handling (credit any valid option; never collapse "Java/Python/Ruby"); `is_mandatory`=knockout-only; copy signal `value` verbatim; spoken constraints (≤240 chars, ONE ask, numbers-in-words, conversational); `primary_signal` ∈ `signal_values`; evaluator-only fields describe a *spoken* answer with concrete observables; knockout-coverage completeness; required-before-preferred; `question_kind` taxonomy + per-question `difficulty`; FollowUpDimension shape (`dimension`/`intent`/`seed_probe`/non-empty `listen_for`); within-bank dimension distinctness.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-11-question-bank-prompt-constraint-inventory.md
git commit -m "docs(question_bank): prompt constraint inventory before SOTA rewrite"
```

### Task 4.2: Rewrite `question_bank_common.txt`

**Files:**
- Modify (rewrite): `backend/nexus/prompts/v2/question_bank_common.txt`

- [ ] **Step 1: Rewrite from scratch**, tightening the 297-line header to high-signal, prescriptive instruction. Required content:
  - Role + task (spoken screening questions read verbatim by a live voice AI; evaluator grades against rubric).
  - The output contract (the `GeneratedQuestion` fields) — but lean on the schema, don't restate validation.
  - Every constraint from the Task 4.1 inventory.
  - **New quality principles (from the QA analysis):** (a) self-contained, scenario-committed leads — every verbatim-read lead answerable on first hearing; ban bare comparative framings ("which would you pick?") lacking an inline scenario; (b) follow-up dimensions must be **semantically** distinct, not just distinct slugs; (c) weight-aware coverage — never spend budget on a w1 signal while a w3 competency is unprobed.
  - **Replace** the per-question 7-point self-critique section with a concise authoring recipe (see Task 4.3).
  - Cache-friendly framing: this is the static prefix; dynamic context follows in the user message.

- [ ] **Step 2: Sanity-check load** — the loader reads it fresh per call; confirm no template-placeholder syntax was introduced that breaks the system prompt (this file is used as a system prompt verbatim, no `.replace()`):

Run: `cd backend/nexus && docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; print(len(PromptLoader(version='v2').get('question_bank_common')))"`
Expected: a positive integer.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/prompts/v2/question_bank_common.txt
git commit -m "feat(question_bank): SOTA rewrite of common prompt header"
```

### Task 4.3: Rewrite `question_bank_ai_screening.txt` as the unified single-call prompt

**Files:**
- Modify (rewrite): `backend/nexus/prompts/v2/question_bank_ai_screening.txt`

- [ ] **Step 1: Rewrite** as ONE self-contained prompt that emits all four `question_kind`s in one pass (no behavioral/technical phase split). Required: the **authoring recipe** —
  1. one mandatory knockout-verification question per knockout signal (`experience_check`/`compliance_binary`);
  2. a true STAR `behavioral` per behavioral-type required signal (MUST NOT be crowded out by technical scenarios);
  3. `technical_scenario` depth probes for the highest-weight competencies first;
  4. stop when high-weight signals are covered — signal density over count.
  - Verification-before-depth ordering.
  - Within-bank dimension distinctness across ALL questions (the model attends to its own earlier questions).
  - `difficulty` calibration guidance (claim-checks easy; STAR easy/medium; scenarios medium/hard by depth).

- [ ] **Step 2: Confirm the stage→prompt mapping is unchanged** — `STAGE_TYPE_TO_PROMPT["ai_screening"] = "question_bank_ai_screening"` still resolves (actors.py:120-123). No code change needed; the file is now the single prompt.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/prompts/v2/question_bank_ai_screening.txt
git commit -m "feat(question_bank): unified single-call ai_screening prompt"
```

### Task 4.4: Delete the retired behavioral prompt

**Files:**
- Delete: `backend/nexus/prompts/v2/question_bank_ai_screening_behavioral.txt`

- [ ] **Step 1: Confirm no caller** (Task 1.5/2.3 removed `STAGE_TYPE_TO_BEHAVIORAL_PROMPT`):

Run: `cd backend/nexus && grep -rn "question_bank_ai_screening_behavioral" app/ tests/`
Expected: no output.

- [ ] **Step 2: Delete the file:**

```bash
git rm backend/nexus/prompts/v2/question_bank_ai_screening_behavioral.txt
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(question_bank): delete retired behavioral-phase prompt"
```

### Task 4.5: Rewrite the regenerate-one + refine + draft prompts

**Files:**
- Modify (rewrite): `backend/nexus/prompts/v2/question_bank_regenerate_one.txt`
- Modify (rewrite): `backend/nexus/prompts/v2/question_refine_single.txt`
- Modify (rewrite): `backend/nexus/prompts/v2/question_create_single.txt`

- [ ] **Step 1: Rewrite `question_bank_regenerate_one.txt`** to match the new `common` header rules + the new FollowUpDimension/quality rules. It already receives sibling questions for dedup — keep that and add the semantic-distinctness rule. It is used as a system prompt via `load_pair("question_bank_common", "question_bank_regenerate_one")` (actors.py:1383), so no `.replace()` placeholders here.

- [ ] **Step 2: Rewrite `question_refine_single.txt` and `question_create_single.txt`** — these use `prompt_loader.get(...)` + `.replace("{placeholder}", ...)` (refine.py:245-268, 306-320). **Preserve every `{placeholder}` token exactly** (`{signals_json}`, `{stage_name}`, `{stage_type}`, `{stage_difficulty}`, `{stage_duration_minutes}`, `{signal_filter_types}`, `{pass_criteria_json}`, `{existing_bank_json}`, `{prior_banks_json}`, `{question_text}`, `{question_signal_probed}`, `{question_mandatory}`, `{instruction}` — and for create, the subset it uses). Update the surrounding instruction text to the new SOTA standard.

- [ ] **Step 3: Verify placeholders intact:**

Run: `cd backend/nexus && grep -o "{[a-z_]*}" prompts/v2/question_refine_single.txt | sort -u && echo "---" && grep -o "{[a-z_]*}" prompts/v2/question_create_single.txt | sort -u`
Expected: the same placeholder set the `.replace()` calls in `refine.py` reference (cross-check against refine.py:245-268 and 306-320).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v2/question_bank_regenerate_one.txt backend/nexus/prompts/v2/question_refine_single.txt backend/nexus/prompts/v2/question_create_single.txt
git commit -m "feat(question_bank): SOTA rewrite of regenerate/refine/draft prompts"
```

### Task 4.6: Wire `prompt_cache_key` on the generation call

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py` (`_create_question_iterable`, ~355-374; and `_stream_bank_questions` to pass a key)

- [ ] **Step 1: Pass a cache key** — instructor forwards unknown kwargs to the OpenAI call. Add a `prompt_cache_key` to `_create_question_iterable`'s `call_kwargs`, keyed by job (stable across a job's stages so they share the cached static prefix). Thread `job_id` into `_create_question_iterable` and set:

```python
    call_kwargs = dict(
        model=ai_config.question_bank_model,
        response_model=GeneratedQuestion,
        messages=kwargs["messages"],
        max_retries=1,
        metadata=kwargs.get("metadata", {}),
        prompt_cache_key=f"qbank-gen-{kwargs['job_id']}",
    )
```

  Pass `job_id=str(job_id)` from `_stream_bank_questions` into the `_create_question_iterable(...)` call.

- [ ] **Step 2: Verify** it doesn't break the streaming call against a mock — run the generation unit tests:

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/question_bank -q`
Expected: PASS. (If the test's fake `_create_question_iterable` monkeypatch ignores kwargs, this is inert in tests and active in prod.)

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py
git commit -m "perf(question_bank): pass prompt_cache_key on generation call (per-job prefix reuse)"
```

### Task 4.7: Audit + delete dead `prompts/v1/question_bank_*`

**Files:**
- Delete (after audit): `backend/nexus/prompts/v1/question_bank_*` files with no live caller.

- [ ] **Step 1: Find every v1 question_bank prompt name referenced in code:**

Run: `cd backend/nexus && for f in prompts/v1/question_bank_*.txt prompts/v1/question_create_single.txt prompts/v1/question_refine_single.txt; do n=$(basename "$f" .txt); echo -n "$n: "; grep -rl "\"$n\"\|'$n'" app/ | grep -v __pycache__ | head -1 || echo "NO CALLER"; done`

- [ ] **Step 2: Confirm the loader version** — the module uses `PromptLoader(version=question_bank_prompt_version="v2")` and `refine.py` uses the global `prompt_loader`. Check what version the global `prompt_loader` resolves for these names:

Run: `cd backend/nexus && grep -rn "prompt_loader = \|PromptLoader(" app/ai/prompts.py`
(If the global `prompt_loader` defaults to v1 for `question_bank_keyterms`, that file stays — `extract_bank_keyterms` calls `prompt_loader.get("question_bank_keyterms")`, refine.py:119. Do NOT delete `question_bank_keyterms.txt` from the version the keyterm call resolves.)

- [ ] **Step 3: Delete only the confirmed-dead files** (those printing "NO CALLER" in Step 1 AND not resolved by any active loader). Keep anything with a live caller (notably the keyterm prompt for whichever version `extract_bank_keyterms` resolves, and any `human_interview`/`take_home` prompt still mapped). `git rm` each dead file.

- [ ] **Step 4: Verify the app still boots + keyterm path resolves:**

Run: `cd backend/nexus && docker compose run --rm nexus python -c "from app.modules.question_bank.refine import extract_bank_keyterms; from app.ai.prompts import prompt_loader; prompt_loader.get('question_bank_keyterms')"`
Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add -A backend/nexus/prompts/v1/
git commit -m "chore(question_bank): delete dead v1 question_bank prompts (caller-audited)"
```

---

## Phase 5 — Frontend cleanup (`frontend/app`)

### Task 5.1: Remove `generation_status_by_kind` from the API type

**Files:**
- Modify: `frontend/app/lib/api/question-banks.ts:77`

- [ ] **Step 1: Delete the field** — remove `generation_status_by_kind: Record<string, string>` from the bank type/interface.

- [ ] **Step 2: Find downstream type errors:**

Run: `cd frontend/app && npm run type-check 2>&1 | grep -i "generation_status_by_kind" || echo "no direct type refs"`

- [ ] **Step 3: Commit**

```bash
git add frontend/app/lib/api/question-banks.ts
git commit -m "feat(app): drop generation_status_by_kind from question-bank API type"
```

### Task 5.2: Flatten `QuestionList` + delete `SectionStatus`

**Files:**
- Modify: `frontend/app/components/dashboard/question-bank/QuestionList.tsx`
- Delete: `frontend/app/components/dashboard/question-bank/SectionStatus.tsx`

- [ ] **Step 1: Remove phase sectioning** — in `QuestionList.tsx`, delete the logic that groups questions by generation phase and reads `bank.generation_status_by_kind[phase]` (line ~63) and renders `<SectionStatus>`. Render a **flat, position-ordered list** of questions. (Cosmetic grouping by each question's persisted `question_kind` is allowed — driven by `question.question_kind`, never by a bank-level phase/status. Prefer the simplest flat list unless grouping clearly helps readability.)

- [ ] **Step 2: Delete the component:**

```bash
git rm frontend/app/components/dashboard/question-bank/SectionStatus.tsx
```

- [ ] **Step 3: Remove any "regenerate behavioral/technical" affordance** — search and remove the button/handler that called the deleted `regenerate-kind` endpoint:

Run: `cd frontend/app && grep -rn "regenerate-kind\|regenerateKind\|RegenerateKind" --include=*.ts --include=*.tsx . | grep -v node_modules | grep -v .next`
Remove any hits (button, API call, handler). Recruiters use the existing full-stage regenerate.

- [ ] **Step 4: Type-check + build:**

Run: `cd frontend/app && npm run type-check`
Expected: no errors referencing the deleted symbols.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/question-bank/
git commit -m "feat(app): flatten QuestionList, delete per-phase SectionStatus + regenerate-kind UI"
```

### Task 5.3: Update the frontend tests

**Files:**
- Modify/Delete: `frontend/app/tests/components/QuestionListSections.test.tsx`

- [ ] **Step 1: Update or delete** — remove the per-section/`generation_status_by_kind`/SectionStatus assertions. If the test's sole purpose was phase sections, delete it; otherwise rewrite it to assert the flat list renders all questions in position order.

- [ ] **Step 2: Run the frontend tests:**

Run: `cd frontend/app && npm run test`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/tests
git commit -m "test(app): update QuestionList tests for flat (no-phase) rendering"
```

---

## Phase 6 — QA-tester A/B on the CURRENT model

> Establishes that the new one-call prompt holds/raises quality BEFORE touching the model. No code change — generation + analysis only.

### Task 6.1: Regenerate + QA-analyze on `gpt-5`/`medium`

- [ ] **Step 1: Restart the worker** so it runs the new code (no hot-reload):

Run: `cd backend/nexus && docker compose up -d --force-recreate nexus-worker`

- [ ] **Step 2: Regenerate the Workato bank** (job `ce6dad9a-8903-4396-8f29-8e36da9bd2a3`, stage `2ea4f4a3-4199-4403-9e2b-744284c8233f`) via the recruiter UI or by enqueuing `generate_question_bank_stage`, plus 1–2 other real JDs for breadth.

- [ ] **Step 3: Deep QA-tester analysis** — pull the full bank from the DB (the same asyncpg dump used in the original Workato QA) and assess: knockout coverage complete + mandatory-gated; leads self-contained/scenario-committed; follow-up dimensions semantically distinct with concrete `listen_for`; rubric/evidence specific+spoken+tool-named; weight-aware required-signal coverage; behavioral STAR present (the one-call risk). Compare head-to-head against the pre-rework bank.

- [ ] **Step 4: Gate** — the new prompt PASSES only if it holds-or-beats the current bank on every dimension. If a category is dropped (e.g. behavioral STAR missing), add the Approach-2 rigid section-labeling to `question_bank_ai_screening.txt` (## VERIFICATION / ## BEHAVIORAL / ## DEPTH), regenerate, re-QA. Record findings (no commit needed unless a prompt edit results — then commit the prompt).

---

## Phase 7 — Model / effort sweep (config-only) + QA

### Task 7.1: Sweep down and pick the cheapest config that holds quality

**Files:**
- Modify (final step only): `backend/nexus/app/config.py:215-216`

- [ ] **Step 1: For each config**, set env + restart worker, regenerate the Workato bank, QA-analyze (Phase 6 method), record pass/fail:
  - `OPENAI_QUESTION_BANK_MODEL=gpt-5.4-mini`, `OPENAI_QUESTION_BANK_EFFORT=low`
  - if `low` drops quality → `OPENAI_QUESTION_BANK_EFFORT=medium`
  - if mini insufficient → `OPENAI_QUESTION_BANK_MODEL=gpt-5.4`, `EFFORT=low`

  Set via the running container env (or `.env`) + `docker compose up -d --force-recreate nexus-worker` between configs.

- [ ] **Step 2: Pick the winner** — the cheapest config that holds-or-beats quality on the Workato bank + the other JDs.

- [ ] **Step 3: Update the defaults** in `app/config.py`:

```python
    openai_question_bank_model: str = "gpt-5.4-mini"   # winner of the QA sweep
    openai_question_bank_effort: str = "low"           # winner of the QA sweep
```

  Also update the stale comment on `question_bank_prompt_version` (line ~222) that says "engine-v2 M2".

- [ ] **Step 4: Final regression:**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/question_bank -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/config.py
git commit -m "perf(question_bank): default to QA-validated cheaper model/effort for generation"
```

---

## Self-Review (run after the plan is written)

**Spec coverage:**
- §3 one-call architecture → Phase 2. §3.3 renames → Task 2.2/2.3. §4 prompt rewrite → Phase 4. §5 model sweep → Phase 7. §6.1 regenerate_kind deletion → Phase 1. §6.2 column drop → Phase 3. §6.3 stale scaffolding → Task 2.3. §6.4 prompt deletions → Task 4.4/4.7. §6.5 frontend → Phase 5. §7 quality gate → Phase 6 + 7. §5 config-driven ceiling → Phase 0. ✅ all covered.

**Placeholder scan:** Prompt-content tasks (4.2/4.3/4.5) intentionally specify structure + constraints, not verbatim text — flagged explicitly as content-not-code gated by QA, which is correct for prompt artifacts. No `TBD`/`TODO` left.

**Type/symbol consistency:** `_stream_bank_questions` defined in 2.2, called in 2.3 ✅. `question_bank_max_questions` config (0.1) used in 0.1 Step 3 ✅. Migration `down_revision` flagged to verify against actual head ✅. Prompt placeholders cross-checked against `refine.py` (Task 4.5 Step 3) ✅.
