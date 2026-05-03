# Engine Redesign — Phase 4: `question_kind` schema + bank-generator

**Status:** Draft for user review · **Date:** 2026-05-03 · **Phase:** 4 of 6 in the engine-redesign arc

## Summary

Phase 4 is the **strictly-additive data-layer change** that lights up the
per-kind task routing already shipped in Phase 3. After Phase 4:

- The DB column `stage_questions.question_kind` exists with a 4-value CHECK
  constraint and a default of `'technical_depth'`.
- The bank-generator LLM emits a required `question_kind` per question
  (3 values: `technical_depth | behavioral_star | compliance_binary`).
- The single-question regeneration LLM emits the same field, preserving the
  prior question's kind by default.
- `interview_runtime/service.py::build_session_config` reads the column into
  `QuestionConfig.question_kind` so the engine's factory routes each
  question to the correct task subclass.

Behavior change in production interviews: **none yet**. Every existing bank
keeps `'technical_depth'` for every question (the column DEFAULT), so the
engine continues routing them through `TechnicalDepthTask` exactly as it
did on `main` before Phase 4. Recruiters regenerate a bank to pick up the
new prompt's kind-selection guidance — no automatic backfill.

This phase consumes 3 of the 21 decisions from the
[overview spec](2026-05-02-interview-engine-redesign-overview-design.md):

- **Decision #3** — bank-generator owns kind emission.
- **Decision #18** — new prompt content requires senior-reviewer fairness sign-off.
- **Decision #21** — schema is additive; bank-gen prompt update is opt-in
  regenerate (recruiter-triggered).

## 1 — Decisions locked in this phase's brainstorm

| # | Open question (overview §12.4) | Decision |
|---|---|---|
| P4-Q1 | LLM-schema strictness for `question_kind` | **Strict.** `GeneratedQuestion.question_kind: Literal["technical_depth","behavioral_star","compliance_binary"]`, required, no default. The 4th value (`open_culture`) stays only on the engine-side `QuestionConfig` Literal as a forward-compat shim. |
| P4-Q2 | Prompt-edit shape | **Hybrid (Option C).** `question_bank_common.txt` defines kinds + selection rule once; `question_bank_phone_screen.txt` and `question_bank_ai_screening.txt` add per-stage calibration paragraphs; `question_bank_regenerate_one.txt` adds a one-line "preserve prior kind" rule. |
| P4-Q2b | Per-stage calibration intensity | **Hard ban on the structurally-wrong fit, soft elsewhere (Option C).** Phone screen prefers `compliance_binary` for binary knockouts; AI screening hard-bans `compliance_binary`; `behavioral_star` not expected at either configured stage. |
| P4-Q3 | Backfill for existing banks | **No backfill — opt-in regenerate.** Migration uses DEFAULT; existing banks unaffected. Migration docstring + `backend/nexus/CLAUDE.md` document the post-migration state explicitly. |
| P4-Q4 | Migration / arc ordering | **Single arc, per-task commits.** Schema → ORM → generator schema → prompts → runtime read → tests. Each commit leaves the system coherent. No staging across PRs. |

## 2 — Scope

### 2.1 In scope

| Surface | Change |
|---|---|
| `migrations/versions/0026_question_kind_column.py` (NEW) | `ALTER TABLE stage_questions ADD COLUMN question_kind TEXT NOT NULL DEFAULT 'technical_depth'` + CHECK constraint allowing all 4 engine-side values. PG11+ metadata-only column add. |
| `app/modules/question_bank/models.py` | New `StageQuestion.question_kind` mapped column (Text, server_default='technical_depth'). |
| `app/modules/question_bank/schemas.py` | New required field on `GeneratedQuestion`: `question_kind: Literal["technical_depth","behavioral_star","compliance_binary"]`. |
| `app/modules/question_bank/service.py` (`write_generated_questions`) | Persist `question_kind` from each generated question to the new DB column. |
| `prompts/v1/question_bank_common.txt` | New "§6 Question kind — choose the engine task subclass" block. |
| `prompts/v1/question_bank_phone_screen.txt` | New "Question kind selection (this stage)" paragraph. |
| `prompts/v1/question_bank_ai_screening.txt` | New "Question kind selection (this stage)" paragraph with HARD BAN on `compliance_binary`. |
| `prompts/v1/question_bank_regenerate_one.txt` | New one-line "preserve prior kind unless replacement signals materially change" rule + a one-sentence note that the field is required in the schema. |
| `app/modules/interview_runtime/service.py` (`build_session_config`) | Pass `question_kind=q.question_kind` to the `QuestionConfig(...)` constructor at line ~189. |
| `tests/test_question_banks_schemas.py` (extend) | Strict-Literal validation tests. |
| `tests/test_question_banks_actors.py` (extend) | LLM-output-to-DB propagation through bulk gen + regen-one. |
| `tests/test_question_banks_service.py` (extend) | Recruiter-authored question lands as default kind. |
| `tests/test_question_banks_migration_0026.py` (NEW) | Migration apply + downgrade + CHECK enforcement. |
| `tests/test_question_banks_prompt_quality.py` (NEW, `prompt_quality` tier) | Real-LLM coverage of phone-screen kind emission, ai_screening hard-ban, regen-one kind preservation. |
| `tests/test_interview_runtime_service.py` (extend or NEW) | `build_session_config` reads the new column into `QuestionConfig.question_kind`. |
| `migrations/versions/0026_question_kind_column.py` docstring | Documents the post-migration state per Decision #21 and P4-Q3. |
| `backend/nexus/CLAUDE.md` | Migration entry for `0026_*`; Phase 4 status block update; Phase 4 hand-off pointer to Phase 5. |
| `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` | Phase status index row for Phase 4: `⚪ → 🟠 → 🔵 → ✅`, with spec + plan paths filled in as each artifact lands. |

### 2.2 Explicitly out of scope

- **Recruiter API surface** — `CreateQuestionBody`, `UpdateQuestionBody`, `QuestionResponse` stay clean. Recruiter-authored questions take the DB default. Surfacing `question_kind` in the recruiter dashboard is a separate, post-arc frontend ticket (per overview spec acceptance gate 9).
- **Refine/Draft flow (`refine.py`)** — uses separate `RefineResponse` / `DraftResponse` schemas without rubric/evidence. The LLM there is not the bank-generator and does not produce `GeneratedQuestion`. No prompt or schema edits in Phase 4.
- **Engine internals** — `app/modules/interview_engine/tasks/factory.py`, `controller.py`, `tasks/{behavioral,compliance_binary,technical_depth}.py`, and any prompt under `prompts/v1/interview/` — already correct from Phase 3; **MUST NOT BE TOUCHED** in Phase 4.
- **`question_bank_human_interview.txt` and `question_bank_take_home.txt`** — exist on disk but have no callers (excluded by `STAGE_TYPE_TO_PROMPT`). Leave alone. When `human_interview` is later re-wired, that PR adds the kind-selection block to its prompt.
- **Backfill of existing banks** — opt-in regenerate via the existing path, per P4-Q3.
- **Frontend** — none of the three apps touch in Phase 4.
- **Phase 5+ work** — `KnockoutFailure` model, tenant `knockout_policy`, `session_outcome` enum, audio authority, e2e gate.

## 3 — Architectural shape

### 3.1 Two LLM call sites that emit `question_kind`

```
question_bank/actors.py
├── _generate_one_bank
│     └─ instructor → StageQuestionBankOutput
│                       └─ list[GeneratedQuestion]   ← question_kind here (required)
│         └─ write_generated_questions
│               └─ INSERT INTO stage_questions (..., question_kind) VALUES (...)
│
└── _regenerate_one_question
      └─ instructor → SingleQuestionOutput
                        └─ question: GeneratedQuestion ← question_kind here (required)
          └─ replace_question_in_place
                └─ UPDATE stage_questions SET (..., question_kind = ?) WHERE id = ?
```

Both paths share the `GeneratedQuestion` schema. Both share the
`question_bank_common.txt` system prompt. Stage-type prompts apply only to
`_generate_one_bank` (per stage); regen-one uses
`question_bank_regenerate_one.txt` instead.

### 3.2 Recruiter-authored path (no LLM)

```
POST /api/jobs/.../questions
  CreateQuestionBody (no question_kind field)
    └─ create_recruiter_question(...)            ← service.py:566
          └─ StageQuestion(...) — no question_kind kwarg passed
                └─ INSERT INTO stage_questions (...) VALUES (...)
                      └─ DB DEFAULT 'technical_depth' fires  ← server-side default
```

Recruiter cannot author or edit `question_kind` directly through the
API surface. The only way a recruiter-authored question can end up with
a non-default kind is via `POST /questions/{id}/regenerate`, which goes
through `_regenerate_one_question` and the LLM path (§3.1) — and that
path requires the LLM to emit `question_kind` per the strict 3-value
Literal. This is intentional: every non-default kind decision lands on
the LLM-traceable, fairness-reviewed path (Decision #18).

### 3.3 Runtime read

```
session/start (LiveKit dispatch)
  └─ engine container loads SessionConfig via in-process call
        build_session_config(session_id, tenant_id):
          └─ for q in stage_questions:
                yield QuestionConfig(..., question_kind=q.question_kind)
                                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                              ← NEW LINE in Phase 4
  └─ controller boots
        for q in config.stage.questions:
            task = build_task_for(q, ctx)   ← already routes on q.question_kind (Phase 3)
            await asyncio.wait_for(
                task.run(),
                timeout=effective_budget_seconds_for(q),  ← already 60s-caps compliance (Phase 3)
            )
```

Phase 4 is **the missing wire** that makes Phase 3's already-shipped routing
actually fire on real questions.

## 4 — Data shapes

### 4.1 DB column

```sql
ALTER TABLE stage_questions
  ADD COLUMN question_kind TEXT NOT NULL DEFAULT 'technical_depth';

ALTER TABLE stage_questions
  ADD CONSTRAINT stage_questions_question_kind_check
  CHECK (question_kind IN
    ('technical_depth', 'behavioral_star', 'compliance_binary', 'open_culture'));
```

**Why all 4 values in the CHECK** even though the generator only emits 3:
the engine-side `QuestionConfig.question_kind` Literal includes
`'open_culture'` as a forward-compat slot for an eventual `OpenCultureTask`.
If a future code path writes `'open_culture'` (e.g., when human_interview is
re-wired and the OpenCultureTask is implemented), the DB shouldn't reject
it. Including it in the CHECK now avoids a follow-up migration. Documented
in the migration's docstring.

**Why DEFAULT** with PG11+ metadata-only column add: rewrite-free, near-
instant on tables of any size, and gives existing banks a deterministic
post-migration state without a backfill query.

### 4.2 ORM column

```python
# app/modules/question_bank/models.py — added after evaluation_hint
question_kind: Mapped[str] = mapped_column(
    Text, nullable=False, server_default=sql_text("'technical_depth'")
)
```

`server_default` matches the migration so SQLAlchemy creates rows correctly
when the model is instantiated without an explicit value (recruiter-authored
path, fixtures).

**Implementation note for `create_question` in `service.py`:** when
constructing the `StageQuestion` instance from a `CreateQuestionBody`, do
NOT pass `question_kind=None` or `question_kind=body.question_kind` (the
field doesn't exist on the body). Either omit the kwarg entirely (the
column will fall through to `server_default`) or explicitly pass
`question_kind="technical_depth"`. Both are correct; the explicit form
is preferred because it makes the recruiter-authored default visible at
the call site and keeps the Python-level row state consistent with the
DB row state without an extra SELECT after INSERT.

### 4.3 LLM-output schema (`GeneratedQuestion`)

```python
# app/modules/question_bank/schemas.py — added to GeneratedQuestion
question_kind: Literal[
    "technical_depth",
    "behavioral_star",
    "compliance_binary",
] = Field(
    ...,
    description=(
        "Which task subclass the engine routes this question to. See the "
        "common prompt §6 for selection rules."
    ),
)
```

**Strict, required, 3 values.** `instructor` rejects any LLM output that
omits or supplies an out-of-Literal value. This makes silent demotions in
regen-one impossible.

### 4.4 Engine-side schema (unchanged in Phase 4)

`interview_runtime/schemas.py::QuestionConfig.question_kind` keeps its
existing 4-value Literal with default `"technical_depth"` (set in Phase 3
by commit `a55a3bf`). Phase 4 adds the runtime line that **explicitly
passes** `question_kind=q.question_kind` so the default no longer fires
in production.

### 4.5 Recruiter API surface (unchanged in Phase 4)

`CreateQuestionBody`, `UpdateQuestionBody`, `QuestionResponse` get
**no field**. Recruiter cannot author, edit, or read `question_kind`
through the API. To upgrade an existing recruiter-authored question's
kind, the recruiter clicks "regenerate" — the existing
`POST /questions/{id}/regenerate` endpoint goes through the LLM path
and the new prompt rule (§5.4) lets the LLM pick a kind for the
replacement. No delete needed.
This matches overview spec acceptance gate 9 and Decision #18.

## 5 — Prompt edits (the senior-reviewer-loaded part)

Per Decision #18 + root CLAUDE.md "Human Review Required For: candidate
scoring and classification thresholds", every prompt edit below is a
classification-threshold change that requires senior-reviewer sign-off in
the PR description.

### 5.1 `prompts/v1/question_bank_common.txt` — append §6

```
# 6. Question kind — choose the engine task subclass

Each question MUST declare a `question_kind` field that tells the live
screening AI which task subclass to dispatch. Three values, exactly one
per question:

  - `compliance_binary` — yes/no attestation about a candidate-self-
    disclosed eligibility fact. The answer is binary; a "no" is a
    knockout against the signal. ≤60s to ask and answer. Examples:
    "Can you work the UK shift (1pm-9pm UK time)?", "Are you legally
    authorized to work in the United States without sponsorship?",
    "Are you willing to relocate to Bangalore for this role?", "Do you
    currently hold an active AWS Solutions Architect Professional
    certification?". Strong fit when: the underlying signal has
    `knockout=true` AND the substance is a fact about the candidate's
    situation, eligibility, or credentials (NOT a skill they must
    demonstrate at depth).

  - `behavioral_star` — past-experience narrative that fits Situation /
    Task / Action / Result shape. The candidate is describing one
    specific event from their work history. Examples: "Tell me about a
    time you had to push back on a technical decision from a senior
    engineer.", "Walk me through a situation where a production incident
    required you to coordinate across three teams under pressure."
    Strong fit when: the underlying signal has `type=behavioral` AND
    `evaluation_method=behavioral_question`.

  - `technical_depth` — DEFAULT. Open-ended technical, scenario, or
    design question that probes HOW the candidate thinks. Examples:
    "How would you design a rate limiter for an API serving 100k
    req/sec?", "Walk me through debugging a 5xx storm in a payment
    service." Use this when the question doesn't fit the two above.
    The vast majority of questions are this kind.

Rules:
  - Mutually exclusive — pick exactly one.
  - The kind drives the engine's per-question budget and probe cap.
    Misclassification costs the candidate real interview time.
  - Per-stage prompts may BAN certain kinds at this stage. Honor the ban.
  - NEVER use the kind to encode anything that could correlate with a
    protected class. The kind is a structural routing decision based on
    question shape, not on the candidate or the signal's social meaning.
```

### 5.2 `prompts/v1/question_bank_phone_screen.txt` — append a section

```
### Question kind selection (this stage)

Phone screen is the natural home for `compliance_binary`: short, binary,
knockout-gating attestations. Expect 0-2 `compliance_binary` questions
per bank — exactly one per binary knockout signal in scope (UK shift,
work auth, willingness to relocate, hard credential check). All other
questions should be `technical_depth` shallow verifications.
`behavioral_star` is not expected at this stage — the phone screen's
depth target is shallow verification, not narrative. If a behavioral
signal is in scope and you must probe it, emit `technical_depth` framed
as a closed verification ("Have you ever had to give negative feedback
to a peer in writing?") rather than a STAR-shaped narrative.
```

### 5.3 `prompts/v1/question_bank_ai_screening.txt` — append the hard-ban paragraph

```
### Question kind selection (this stage)

AI screening's per-question budget assumes 3-5 minute deep-dive cognition.
`compliance_binary` is BANNED at this stage — a 60-second yes/no in a
30-minute deep-dive robs budget from depth probes this stage exists to
deliver. Binary attestations belong in the phone screen. If you find
yourself reaching for one here, either re-frame the underlying intent
as a `technical_depth` scenario, or recognize the signal should have
been verified at the phone screen and skip it.

`behavioral_star` is also not expected — this stage skips behavioral
signals entirely (see the allocation rule above). Every question this
stage emits should be `technical_depth`.
```

The "BANNED" wording is load-bearing — the prompt_quality test asserts
ai_screening output across ≥3 runs never contains a `compliance_binary`
question.

### 5.4 `prompts/v1/question_bank_regenerate_one.txt` — augment "Your task"

Insert under "Your task" bullet list:

```
- **Preserve the original question's `question_kind` unless
  `replace_signal_values` is provided AND the new signal class
  materially changes the question shape** (e.g., swapping a
  `compliance_binary` knockout signal for a `competency` depth signal).
  If unsure, keep the same kind. The replacement question's
  `question_kind` field is REQUIRED — see the common prompt §6 for
  selection rules.
```

### 5.5 Senior-reviewer fairness sign-off checklist (PR description)

Every Phase-4 PR that touches prompt files carries this checklist in the
description, walked by the senior reviewer:

- [ ] No prompt text uses biased phrasing or examples that imply a protected class.
- [ ] The "kind is structural, not social" guard in §6 is intact and unambiguous.
- [ ] No example question reproduces a problematic real-world ask (e.g., "where are you from", "do you have kids").
- [ ] The `compliance_binary` examples are factual self-disclosures (work auth, shift, relocation, certification), not AI-inferred personality traits.
- [ ] The BAN on `compliance_binary` in ai_screening is intact (prevents budget-stealing misclassification that would cost candidates depth-probe time unequally).
- [ ] No prompt body changes the existing rules around protected classes, evidence-based scoring, or borderline handling.
- [ ] Reviewer name and date in the PR description.

## 6 — Tests

Tests live at top-level `tests/test_question_banks_*.py` matching the
existing convention (no `tests/question_bank/` subdirectory).

### 6.1 Test matrix

| Test file | Tier | What it asserts |
|---|---|---|
| `tests/test_question_banks_schemas.py` (extend) | unit | `GeneratedQuestion` requires `question_kind`; rejects missing field with `pydantic.ValidationError`; rejects out-of-Literal values; accepts each of the 3 valid values; documents that `open_culture` is intentionally NOT in the generator's Literal. |
| `tests/test_question_banks_actors.py` (extend) | unit | Mock the LLM client. Assert `_generate_one_bank` propagates `question_kind` from the LLM output through `write_generated_questions` to the persisted row. Same for `_regenerate_one_question`. Use ≥3 fixture items in `positive_evidence` and ≥2 in `red_flags`. |
| `tests/test_question_banks_service.py` (extend) | unit | `write_generated_questions` writes `question_kind` per question; recruiter-authored question via `create_question` (no `question_kind` in body) lands as `'technical_depth'` in DB. |
| `tests/test_question_banks_migration_0026.py` (NEW) | integration | Migration applies cleanly to a database with existing `confirmed` banks (use existing fixtures). Existing rows have `question_kind = 'technical_depth'`. CHECK constraint rejects an out-of-allowlist value. The downgrade path drops cleanly. |
| `tests/test_question_banks_prompt_quality.py` (NEW, `prompt_quality` tier) | prompt_quality (real LLM) | (a) Phone screen with a UK-shift knockout signal emits **at least one** `compliance_binary` question and at most one per binary knockout. (b) AI screening across N=3 runs emits **zero** `compliance_binary` questions (the hard-ban assertion). (c) Regen-one preserves the kind of the question being replaced when `replace_signal_values` is unchanged. Use exact-field checks on the validated output (no `judge()` needed for these structural assertions). |
| `tests/test_interview_runtime_service.py` (extend or NEW) | unit | `build_session_config` reads `q.question_kind` from the row into `QuestionConfig.question_kind`. Verify with both `'technical_depth'` (default) and one non-default value (e.g., `'compliance_binary'`) in the fixture. |

### 6.2 Coverage targets

- `question_bank/schemas.py` — 100% line coverage on the new field's validation paths (rejection on missing, rejection on bad value, acceptance on each of the 3).
- `question_bank/actors.py` write path — branch-covered for the new field through both bulk gen and regen-one.
- `question_bank/service.py::write_generated_questions` — line-covered for the new persistence write.
- `interview_runtime/service.py::build_session_config` — the new line reaches every test that exercises the runtime read; assert with both default and non-default fixture values.

### 6.3 Plan-template guard rails (pre-empt Phase-3 recurring bugs)

Phase 3's plan had two recurring template bugs the implementing subagent
had to correct multiple times. Phase 4's plan pre-empts them:

- Every fixture `GeneratedQuestion` instance MUST set `question_kind="technical_depth"` (or another valid kind where the test exercises a non-default path). The audit found **5 test files with ~5-7 fixture/inline construction sites** that need the key added — touch them in the same task that introduces the schema field so the unit test suite stays green at every commit. Concrete sites:
  - `tests/test_question_banks_schemas.py` — `_valid_generated_question(**overrides)` helper (the central one). Adding `question_kind="technical_depth"` to its base dict propagates the default to every test that uses it.
  - `tests/test_question_banks_actors.py` — one inline `GeneratedQuestion(...)` and one `_make_question(...) -> GeneratedQuestion` helper.
  - `tests/test_question_banks_service.py` — one `_make_question(...) -> GeneratedQuestion` helper.
  - `tests/test_question_banks_events.py` — one inline `GeneratedQuestion(...)` (regen-one mock).
  - `tests/test_question_banks_integration.py` — one inline `GeneratedQuestion(...)` in a list comprehension.
  - **`tests/interview_engine/` and `tests/interview_runtime/`**: zero changes needed — those use the engine-side `QuestionConfig`, where `question_kind` is already optional with default `"technical_depth"` from Phase 3.
- Pydantic-rejection tests use `pydantic.ValidationError`, not bare `Exception`.
- `pytest` import is module-level, not function-level.
- `positive_evidence` lists ≥3 items, `red_flags` ≥2 items.
- All cross-module imports go through public API (`from app.modules.interview_runtime import QuestionConfig`, NOT `from app.modules.interview_runtime.schemas import QuestionConfig`). `tests/test_module_boundaries.py` will fail anything else.

### 6.4 Coverage in Docker workaround

If any new test ends up importing `livekit.agents`, use the documented
`python -m coverage run` workaround from `backend/nexus/CLAUDE.md` →
"Coverage in Docker — pytest-cov + Python 3.13 segfault workaround".
Likely not needed because question_bank tests don't touch livekit, but
called out so the implementer doesn't get surprised.

### 6.5 Pre-existing test failures: out of scope

`test_auth_login.py`, `test_auth_service.py`, `test_pipelines_service.py`,
`test_session_schemas.py`, `test_audit.py` — Phase 4 does not fix these.
Phase 4's gate is "the question_bank, interview_runtime, and
interview_engine subsets stay green and grow; `test_module_boundaries.py`
stays green." Phase 3 closed `test_module_boundaries.py` as part of
`ec3bfb8` — keep it that way.

## 7 — Migration safety + post-migration documentation

### 7.1 Why this migration is safe

- `ALTER TABLE ... ADD COLUMN ... TEXT NOT NULL DEFAULT '...'` is metadata-only since PG11 — no table rewrite, no row scan, near-instant.
- The DEFAULT means there is no NULL window for existing rows. Application
  code can safely assume `question_kind` is non-null immediately after
  the migration completes.
- The CHECK constraint validates against a fixed allowlist; the bank-gen
  prompt only emits the 3 generator-allowed values; the engine-side
  Literal accepts all 4. No runtime path can violate the constraint.
- The migration is fully reversible — `downgrade()` drops the constraint
  then the column.

### 7.2 Post-migration state (per Decision #21 + P4-Q3)

The migration's docstring documents:

> Adds `stage_questions.question_kind` (TEXT NOT NULL DEFAULT
> 'technical_depth') with a CHECK constraint allowing all 4 engine-side
> values. Metadata-only column add (PG11+); no table rewrite.
>
> **Post-migration state:** every existing row reads `'technical_depth'`.
> Existing banks remain in their current `confirmed`/`reviewing` status.
> To get real per-question kinds, recruiters regenerate via
> `POST /api/jobs/{id}/banks/{bank_id}/regenerate` (existing endpoint) —
> the new bank-gen prompt picks the right kind per question. **No
> automatic backfill is performed**, by design (see Phase-4 design
> spec §"Backfill"). Engine routes default-kind questions through
> `TechnicalDepthTask` — the same behavior as `main` today, so no
> regression.

`backend/nexus/CLAUDE.md` migrations list gets a matching entry:

> `0026_question_kind_column` — **Phase 4**: adds
> `stage_questions.question_kind` (TEXT NOT NULL DEFAULT
> `'technical_depth'`, CHECK in
> `('technical_depth','behavioral_star','compliance_binary','open_culture')`).
> Bank-generator now emits the field; existing rows get the default.
> Recruiters regenerate to upgrade old banks (no automatic backfill).

### 7.3 Phase 4 status block in `backend/nexus/CLAUDE.md`

Add a new bullet to the "Current State" list:

> - **Phase 3D.engine-redesign-4** — done: `stage_questions.question_kind`
>   column added (migration `0026_*`); bank-generator now emits the
>   field per question (3-value Literal: `technical_depth |
>   behavioral_star | compliance_binary`); regen-one preserves prior
>   kind; `interview_runtime.build_session_config` reads the column
>   into `QuestionConfig.question_kind`. Existing banks unchanged
>   (default `'technical_depth'`); recruiters regenerate to pick up
>   the new prompt's kind selection. Recruiter API surface unchanged
>   (`question_kind` not in request/response schemas). See spec
>   `docs/superpowers/specs/2026-05-03-engine-redesign-phase-4-question-kind-schema-design.md`.

## 8 — Acceptance gates

Phase 4 is "shipped" when all of the following hold:

1. Migration `0026_question_kind_column.py` applies cleanly + the downgrade
   test passes.
2. ORM `StageQuestion.question_kind` column is in `models.py` with
   `server_default='technical_depth'`.
3. `GeneratedQuestion` has the strict 3-value Literal field, required
   (no default).
4. Both bulk-gen (`_generate_one_bank` + `write_generated_questions`)
   and regen-one (`_regenerate_one_question` + `replace_question_in_place`)
   persistence paths populate the column.
5. All four prompt edits land with the senior-reviewer fairness sign-off
   checklist (§5.5) completed in the PR description.
6. `build_session_config` passes `question_kind` into `QuestionConfig`
   at the constructor site.
7. All new + extended tests are green; `test_module_boundaries.py` stays
   green; pre-existing failures are unchanged (not fixed, not made worse).
8. Migration docstring + `backend/nexus/CLAUDE.md` migration list +
   "Current State" block updated.
9. Overview spec `Phase status index` row for Phase 4 flipped to
   `✅ shipped` in the same commit that ships the final Phase 4 artifact,
   with this spec's path and the Phase-4 plan's path filled in.
10. Recruiter API surface confirmed unchanged: `git grep -n
    "question_kind"` in `app/modules/question_bank/{router.py,schemas.py}`
    does NOT touch `CreateQuestionBody`, `UpdateQuestionBody`, or
    `QuestionResponse`.

## 9 — Phase 5 hand-off

Phase 5 inherits a system that:

- Has `question_kind` plumbed end-to-end (DB column, ORM, generator schema,
  generator prompts, regen-one prompt, runtime read, factory routing,
  per-task budgets).
- Has no automatic backfill — old banks read `'technical_depth'` until
  recruiter-regenerated.
- Still routes everything to `TechnicalDepthTask` for un-regenerated banks.
- Recruiter API surface still does not expose `question_kind`.

Phase 5's job (knockout policy + tenant settings) is independent of the
`question_kind` plumbing — it adds `KnockoutFailure` model,
`SessionResult.knockout_failures`, tenant `engine_knockout_policy`, and
`session_outcome` enum expansion. Phase 4 does not pre-stage anything for
Phase 5.

## 10 — Glossary additions

- **`question_kind` (DB column)**: TEXT NOT NULL DEFAULT 'technical_depth' on
  `stage_questions`. Set by the bank-generator LLM at write time;
  defaulted on recruiter-authored rows; defaulted on every pre-Phase-4
  row at migration time. Read by the engine at session start.
- **Generator-allowed kinds**: `technical_depth`, `behavioral_star`,
  `compliance_binary`. The 3-value strict Literal on `GeneratedQuestion`.
- **Engine-allowed kinds**: `technical_depth`, `behavioral_star`,
  `compliance_binary`, `open_culture`. The 4-value Literal on
  `QuestionConfig`. The 4th value is a forward-compat slot the generator
  intentionally never emits in Phase 4.
- **Default-kind question**: any `stage_questions` row with `question_kind
  = 'technical_depth'`. Includes both genuine technical-depth questions
  and pre-Phase-4 / un-regenerated rows. The engine cannot tell them
  apart by inspection — both route through `TechnicalDepthTask`.
