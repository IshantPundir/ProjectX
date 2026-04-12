# Phase 2C.2 — Question Generation

> **Status:** Approved design — ready for implementation planning
> **Date:** 2026-04-12
> **Depends on:** Phase 2C.1 (Pipeline Builder) — shipped
> **Prerequisite for:** Phase 3 (session engine)

---

## What This Delivers

Phase 2C.2 builds the **question bank generation system** that takes a confirmed pipeline (2C.1) and the job's signal schema (2B) and produces **rich, structured, audit-grade interview questions** per pipeline stage. Recruiters review and edit the generated questions through a dedicated review surface, then explicitly confirm each bank to mark it ready for Phase 3 interview sessions.

The quality of this phase determines how well the Phase 3 session engine can screen and evaluate candidates and how defensible the resulting evaluation reports are. The structured schema (evidence-based rubric anchors, follow-up probes, mandatory knockout enforcement) is specifically designed for downstream consumption by a live interview AI that must score candidates consistently, catch bluffing, and produce legally defensible hiring reports.

---

## Core Product Principles

1. **AI decides, human verifies.** The AI autonomously generates every question with full structure (text, evidence, rubric). The recruiter reviews, tweaks, or replaces; never writes from scratch unless they want to.

2. **The anti-lie invariant.** Multi-stage pipelines exist specifically to catch candidates who bluff shallow questions. For any signal probed in an earlier stage at shallow depth, later stages MUST re-probe that signal at greater depth (different angle, same signal). Critical signals (weight=3 or knockout) get re-verified at every subsequent stage; low-weight signals get probed once.

3. **Evidence-based scoring, not scripted answers.** Every question carries positive evidence items (what to listen for), red flags (what indicates weakness), and rubric anchors (what each scoring level contains). The system never generates scripted example answers — these harm calibration and introduce bias.

4. **Audit-grade provenance.** Every question carries source (`ai_generated` / `ai_regenerated` / `recruiter`) and an `edited_by_recruiter` flag. Signal snapshot IDs are pinned on bank generation for full audit trails.

5. **Explicit handoff to Phase 3.** A bank is only usable by interview sessions once the recruiter clicks "Confirm bank". Confirmation requires knockout coverage and duration budget validation. Any edit auto-reverts a confirmed bank to `reviewing` state.

---

## Prerequisite: Phase 2C.1 Stage ID Stability Fix

### Why this is a blocker

Phase 2C.2 stores question banks with a foreign key to `job_pipeline_stages.id`. However, 2C.1's `update_job_pipeline_stages` service (`backend/nexus/app/modules/pipelines/service.py:543`) currently **deletes and re-inserts every stage row on every save**:

```python
async def update_job_pipeline_stages(db, *, instance, stages):
    # DELETE all existing stage rows
    existing = await db.execute(select(JobPipelineStage).where(...))
    for s in existing.scalars().all():
        await db.delete(s)
    await db.flush()
    # INSERT fresh stage rows from the incoming payload
    for stage in stages:
        db.add(JobPipelineStage(...))
```

With auto-save on the pipeline editor (every character typed into a stage name field triggers a PATCH), stage row UUIDs churn on every keystroke. Any question banks FK'd to `stage_id` would be cascade-deleted before the recruiter finishes typing.

### The fix

1. New Pydantic schema `PipelineStageUpdateInput` — extends `PipelineStageInput` with an **optional** `id: UUID | None` field
2. `UpdateJobPipelineRequest.stages` accepts `list[PipelineStageUpdateInput]`
3. `update_job_pipeline_stages` implementation changes to **diff-and-sync**:
   - Load all existing stages for the instance
   - Partition incoming stages by presence of `id`: `incoming_by_id` dict vs `incoming_new` list
   - For each existing stage:
     - In `incoming_by_id` → update fields in place, preserve UUID
     - Not in `incoming_by_id` → `db.delete(stage)` (recruiter removed it)
   - For each `incoming_new` → insert fresh row
4. Frontend: stop calling `stripId` before save. Pass existing stage IDs through the save body. New stages (via "+ Add stage") have `id = undefined`.

**Result:** stage rows survive edits with their UUIDs intact. Questions FK'd to `stage_id` survive stage edits. The `confirmed → reviewing` auto-revert semantics (covered in Section 5) handle the case where editing a stage changes the questions' relevance.

### What happens when a stage IS actually destroyed

- **Edit stage fields** (name, duration, difficulty, etc.) → stage id unchanged → bank survives → auto-revert `confirmed → reviewing`
- **Delete stage via 3-dot menu** → stage row deleted → bank cascade-deleted → questions gone (correct — stage no longer exists)
- **Swap / Reset template** → entire pipeline instance replaced → all stages deleted → all banks deleted → questions gone. The frontend shows a loud warning before swap/reset: *"This will delete N generated questions across M stages. Continue?"*

---

## Architecture Overview

Three-layer architecture, matching the rest of the codebase:

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 16)                                           │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  /jobs/[id]/questions                                     │   │
│  │  ├─ Sidebar: all pipeline stages + bank status badges    │   │
│  │  └─ Main pane: selected stage's bank                      │   │
│  │     (expandable question cards, inline auto-save edit,   │   │
│  │      regenerate individual, confirm bank)                 │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────────┘
                         │ REST + SSE (status stream)
┌────────────────────────▼────────────────────────────────────────┐
│  Nexus Backend — app/modules/question_bank/                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  router.py       11 endpoints (CRUD + gen + SSE)         │   │
│  │  service.py      Bank CRUD, state transitions, regen     │   │
│  │  authz.py        Ancestry-walking authz                  │   │
│  │  actors.py       3 Dramatiq actors (stage, pipeline, regen)│  │
│  │  schemas.py      Pydantic v2 — rich question shape       │   │
│  │  state_machine.py  draft → generating → reviewing → confirmed │
│  │  errors.py       Custom exceptions for 409/422           │   │
│  │  sse.py          SSE status stream                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  LLM layer via app/ai/                                   │   │
│  │  Prompts: prompts/v1/question_bank_common.txt            │   │
│  │         + prompts/v1/question_bank_<stage_type>.txt      │   │
│  │         + prompts/v1/question_bank_regenerate_one.txt    │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────────┘
                         │ SQLAlchemy async
┌────────────────────────▼────────────────────────────────────────┐
│  PostgreSQL                                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  stage_question_banks    — one per JobPipelineStage      │   │
│  │  stage_questions         — rich question rows            │   │
│  │  (both tenant-scoped with RLS, migration 0006)           │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Architectural Decisions

- **One bank per pipeline stage, not per job.** Matches 2C.1's modular pipeline architecture (1–5+ stages per job). A fixed "two banks" model couldn't represent single-stage or four-plus-stage pipelines. Each stage's bank is generated with that stage's specific metadata (type, duration, difficulty, signal_filter.include_types).

- **Separate tables, not JSONB on `job_pipeline_stages`.** Rich question structure (~10 fields with nested arrays and discriminated rubric object) is not a good fit for JSONB on a parent row. Separate `stage_question_banks` + `stage_questions` tables give clean FKs, proper indexing (including GIN on `signal_values`), cascade behavior, and audit-friendly structure.

- **Bank belongs to the stage instance, not the template.** The `stage_question_banks` row is FK'd to `job_pipeline_stages` (the per-job instance), not `pipeline_template_stages`. This matches 2C.1's snapshot invariant: templates are reusable recipes; banks are generated for one specific job's specific stage instance.

- **State machine is per-stage-bank, not per-pipeline.** Each stage's bank transitions independently: `draft → generating → reviewing → confirmed`. A 3-stage pipeline can have stage 1 confirmed, stage 2 reviewing, stage 3 draft. Phase 3 sessions refuse to start unless **all stages the candidate will pass through** are `confirmed`.

- **Questions carry provenance.** `source: 'ai_generated' | 'ai_regenerated' | 'recruiter'` (same pattern as signals in Phase 2B). Provenance tracks editability, drives "Regenerate all" semantics (wipe ai-sourced, preserve recruiter-sourced), and supports audit compliance.

- **Signal snapshot pinning.** Every bank carries `signal_snapshot_id` FK, set at generation time. This enables audit trails ("generated against snapshot v3") and staleness detection ("job's current confirmed snapshot is v4 → bank is stale").

- **Manual trigger for generation.** Recruiters explicitly click "Generate questions" per stage (or "Generate all" for the pipeline). No auto-generation on pipeline apply. Rationale: recruiters want room to tune stage metadata (duration, difficulty) first, then commit to generation once the stage is finalized.

- **Module boundaries.** `question_bank` depends on: `pipelines` (reads `JobPipelineStage`), `jd` (reads signals from `JobPostingSignalSnapshot`), `org_units` (reads company profile), `ai` (LLM layer). It does NOT depend on: session, reporting, analysis — those are consumers in Phase 3+.

- **Hook into the pipeline page.** The existing `/jobs/[id]/pipeline` page gets one addition: a "Review questions →" button in the header (with a "N of M banks confirmed" chip). Pipeline page stays focused on structure, questions page stays focused on content.

---

## Data Model

### `stage_question_banks`

```sql
CREATE TABLE stage_question_banks (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL,
    stage_id              UUID NOT NULL REFERENCES job_pipeline_stages(id) ON DELETE CASCADE,
    job_posting_id        UUID NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    signal_snapshot_id    UUID NOT NULL REFERENCES job_posting_signal_snapshots(id),
    status                TEXT NOT NULL DEFAULT 'draft',
    prompt_version        TEXT NOT NULL DEFAULT 'v1',
    generation_error      TEXT,
    generated_at          TIMESTAMPTZ,
    generated_by          UUID REFERENCES users(id),
    confirmed_at          TIMESTAMPTZ,
    confirmed_by          UUID REFERENCES users(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('draft', 'generating', 'reviewing', 'confirmed', 'failed'))
);

CREATE UNIQUE INDEX ix_stage_question_banks_stage ON stage_question_banks(stage_id);
CREATE INDEX ix_stage_question_banks_job ON stage_question_banks(job_posting_id);
CREATE INDEX ix_stage_question_banks_tenant_status ON stage_question_banks(tenant_id, status);
CREATE INDEX ix_stage_question_banks_snapshot ON stage_question_banks(signal_snapshot_id);

ALTER TABLE stage_question_banks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON stage_question_banks
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON stage_question_banks
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**State transitions:**

```
Generation flow:
    draft       → generating → reviewing (on LLM success)
    reviewing   → generating (on "Regenerate all" from reviewing)
    confirmed   → generating (on "Regenerate all" from confirmed)
    failed      → generating (on retry — direct, skips draft)
    generating  → failed (on LLM error) — carries error message

Recruiter-edit auto-revert:
    confirmed   → reviewing (on ANY question edit, create, delete, reorder,
                              or single-question regen completion)
                              — also CLEARS confirmed_at and confirmed_by

First-content transition:
    draft       → reviewing (on first question created by recruiter
                              before generation has run)

Explicit confirmation:
    reviewing   → confirmed (on POST /confirm; gated by coverage + budget checks)
```

**Rationale:**
- `draft` as the initial state gives Phase 3 a clean check (`status = 'confirmed'`) without NULL handling
- `confirmed → reviewing` on ANY edit prevents silent drift from a confirmed state
- Clearing `confirmed_at` / `confirmed_by` on auto-revert means the column always reflects the CURRENT confirmation state. Full audit trail of past confirmations lives in the `audit_log` table via `log_event`.
- `failed → generating` is direct (no intermediate `draft` hop) — retrying a failed bank goes straight to generation. The old `generation_error` is cleared on the transition.
- No explicit "unconfirm" endpoint — auto-revert on edit + regenerate-from-any-state covers all cases.

### `stage_questions`

```sql
CREATE TABLE stage_questions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL,
    bank_id               UUID NOT NULL REFERENCES stage_question_banks(id) ON DELETE CASCADE,
    position              INTEGER NOT NULL CHECK (position >= 0),
    source                TEXT NOT NULL,
    text                  TEXT NOT NULL,
    signal_values         TEXT[] NOT NULL,
    estimated_minutes     NUMERIC(4,1) NOT NULL,
    is_mandatory          BOOLEAN NOT NULL DEFAULT FALSE,
    follow_ups            JSONB NOT NULL DEFAULT '[]',
    positive_evidence     JSONB NOT NULL DEFAULT '[]',
    red_flags             JSONB NOT NULL DEFAULT '[]',
    rubric                JSONB NOT NULL,
    evaluation_hint       TEXT NOT NULL,
    edited_by_recruiter   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (source IN ('ai_generated', 'ai_regenerated', 'recruiter'))
);

CREATE UNIQUE INDEX ix_stage_questions_bank_position ON stage_questions(bank_id, position);
CREATE INDEX ix_stage_questions_signal_values_gin ON stage_questions USING GIN (signal_values);

ALTER TABLE stage_questions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON stage_questions
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON stage_questions
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**Field decisions:**

| Field | Type | Rationale |
|---|---|---|
| `signal_values` | `TEXT[]` (GIN-indexed) | 1–3 signals per question. **Signals do not have stable UUIDs** — the Phase 2B signal schema identifies signals only by their `value` string inside the `job_posting_signal_snapshots.signals` JSONB array. Questions reference signals by value. The bank's pinned `signal_snapshot_id` makes these references stable (snapshots are append-only; values inside a given snapshot version never change). Lookup is O(n) over the snapshot's ~5–20 signals — trivial. |
| `follow_ups`, `positive_evidence`, `red_flags` | `JSONB` | Small arrays, always read with the parent question, never queried independently. JSONB is the right granularity. |
| `rubric` | `JSONB` | `{excellent, meets_bar, below_bar}` is a unit. Future level changes bump prompt version, not schema. |
| Depth target | **omitted** | Phase 3 reads stage difficulty directly via `bank → stage` join. Terminology clash with stage difficulty avoided. |

### Cascade behavior

| Event | Cascade |
|---|---|
| Delete `JobPipelineStage` | → cascade to `stage_question_banks` → cascade to `stage_questions` |
| Delete `JobPosting` | → cascade through pipeline stages → through banks → through questions |
| Delete `JobPipelineInstance` (swap/reset) | → cascade through stages → through banks → through questions |
| Delete signal snapshot | **prevented** — FK is not `ON DELETE CASCADE`. Snapshots are append-only. |

No orphaned rows possible under any workflow.

---

## Generation Flow + Prompt Design

### Prompt file layout

```
prompts/v1/
├── question_bank_common.txt            # Shared header: principles, allocation rules, output schema
├── question_bank_phone_screen.txt      # Phone screen specialization
├── question_bank_ai_interview.txt      # AI deep interview specialization
├── question_bank_human_interview.txt   # Human-led interview specialization
├── question_bank_panel_interview.txt   # Panel interview specialization
├── question_bank_take_home.txt         # Take-home assignment specialization
└── question_bank_regenerate_one.txt    # Single-question regeneration
```

`PromptLoader` is extended with a `load_pair(common_name, type_name)` method that concatenates `common + type` at call time. Versioning remains per-directory (`v1/`, `v2/`).

### The common header establishes five principles

**1. Anti-lie invariant** (the most important rule). The prompt teaches the LLM:

> *"Candidates can bluff shallow questions. A phone screen that asks 'Do you have 5 years of Python?' gets 'Yes' from both experts and memorized liars. Later stages exist specifically to catch the liars by re-probing the same signals at greater depth."*
>
> *For signals that were probed in an earlier stage:*
> - *weight=3 or knockout → you MUST re-probe at greater depth, different angle, same signal*
> - *weight=2 → re-probe if budget allows, with a harder angle, otherwise skip*
> - *weight=1 → skip, use budget for uncovered signals*
>
> *For signals not yet probed → probe at this stage's depth target.*

**2. Evidence-based scoring.** Every question must have 3–5 positive evidence items, 2–3 red flags, and 3-level rubric anchors. The prompt explicitly bans scripted example answers:

> *"Do NOT write 'the candidate describes a time they debugged a production issue.' Write what to LISTEN FOR: 'names specific observability tools (logs, APM, tracing); describes hypothesis-verify loop; mentions blameless post-mortem.' Anchors describe what an answer contains, never what the answer is."*

**3. Signal allocation math.**

> *"Allocate questions proportionally to weight × priority. weight=3 or knockout → 2 questions (one verification, one depth). weight=2 → 1 question. weight=1 → 0–1. Cap by duration budget — each Q takes ~3–8 min including candidate answer and follow-ups. Sum of estimated_minutes should be 85–105% of stage duration."*

**4. Mandatory knockouts.** Any signal with `knockout=true` gets a corresponding question with `is_mandatory=true`, appearing early in stage order (low position number).

**5. Company tone without contamination.**

> *"The company profile (`about`, `industry`, `company_stage`, `hiring_bar`) from the org unit should subtly shape question phrasing. A scrappy Series A startup (`company_stage`) doesn't ask FAANG-style questions. A mature enterprise doesn't ask early-stage-style questions. Calibrate difficulty within the stage's difficulty level using `hiring_bar`. Use `industry` and `about` to choose domain-appropriate examples and framing. NEVER mention the company name in the question text — tone only."*

### Per-stage-type specializations

| Stage type | Q count | Style | Depth |
|---|---|---|---|
| `phone_screen` | 3–5 (5–10 min) or 5–8 (10–15 min) | Short, direct, closed. Knockout-heavy. 1–2 sentences per Q. | Shallow — verification not mastery |
| `ai_interview` | 6–8 (30–60 min) | Open-ended, hypothesis-verify, technical depth. | Deep — multi-level probing |
| `human_interview` | 6–8 (45–60 min) | Structured behavioral + technical mix. Delivered by human. | Medium to deep |
| `panel_interview` | 8–12 (60–90 min) | Calibration-grade questions, senior bar. Mix of types. | Deep |
| `take_home` | 1 big problem + eval criteria | Structured assignment with deliverables. | Deep |

For example, `phone_screen.txt` appends:

> *"This is a SHORT SCREENING CALL. The conductor is an AI bot. Questions are 1–2 sentences. Follow-ups are 1–2 quick clarifications ('What cluster size?'), never deep probes. Most questions should map to knockout or weight=3 signals. Skip weight=1 unless budget is open. Target: 3–8 questions depending on duration."*

### Prior-stage context section

When generating stage N, the prompt input includes:

```
## Pipeline context

This pipeline has 3 stages. You are generating questions for STAGE 2.

### Stage 1 — Phone Screen (already generated, 4 questions)
  Duration: 10 min · Difficulty: easy · Conductor: AI bot

  Q0 [mandatory · probes: "Apigee experience" (knockout, w=3)]:
    "Do you have hands-on production experience with Apigee?"
    Rubric.meets_bar: "Names specific proxy types and at least one production deployment"

  Q1 [mandatory · probes: "GKE experience" (knockout, w=3)]:
    "Have you run Kubernetes workloads in production?"
    Rubric.meets_bar: "Names cluster size and describes at least one operational incident"

  [... etc]

### Stage 2 — AI Technical Interview (CURRENT — you are generating this)
  Duration: 45 min · Difficulty: hard · Conductor: AI bot

### Stage 3 — Hiring Panel (not yet generated)
  Duration: 60 min · Difficulty: hard · Conductor: human panel
  (This stage will probe the same signal pool at similar depth. Leave some
   headroom for them — don't exhaust every signal at max depth here.)
```

The "leave headroom" hint prevents stage 2 from saturating all depth questions, so the panel has something to probe.

### Structured output schema (what `instructor` validates)

```python
from pydantic import BaseModel, Field

class QuestionRubric(BaseModel):
    excellent: str = Field(..., min_length=20, max_length=300,
                           description="Anchor for top-of-scale — what a strong answer contains")
    meets_bar: str = Field(..., min_length=20, max_length=300,
                           description="Anchor for middle — what an acceptable answer contains")
    below_bar: str = Field(..., min_length=20, max_length=300,
                           description="Anchor for bottom — what a weak answer looks like")

class GeneratedQuestion(BaseModel):
    position: int = Field(..., ge=0)
    text: str = Field(..., min_length=10, max_length=500)
    signal_values: list[str] = Field(..., min_length=1, max_length=3,
                                      description="Signal VALUES from the pinned snapshot that "
                                                  "this question probes. Must exactly match values "
                                                  "in the snapshot's signals array.")
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool
    follow_ups: list[str] = Field(..., min_length=0, max_length=3)
    positive_evidence: list[str] = Field(..., min_length=3, max_length=5)
    red_flags: list[str] = Field(..., min_length=2, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)

class StageQuestionBankOutput(BaseModel):
    stage_summary: str = Field(..., min_length=20, max_length=300,
                                description="1-sentence: what this stage tests")
    questions: list[GeneratedQuestion] = Field(..., min_length=1, max_length=15)
    coverage_notes: str = Field(..., min_length=20, max_length=500,
                                description="Chain-of-thought: why you allocated questions this way. "
                                            "Captured by Langfuse trace for debugging — NOT stored in the DB.")
```

`instructor` automatically rejects outputs that don't match and retries with the validation error as context. This catches ~95% of schema violations before they hit the DB. The `coverage_notes` field is a chain-of-thought artifact that flows through the Langfuse trace (which already captures full LLM input/output) — the service reads it for logging but discards it from the persisted question bank.

### Post-validation checks (application-level)

After `instructor` validates, the service runs additional checks against the pinned snapshot:

1. **Signal value existence:** every `signal_value` in every question must exactly match a `value` in the pinned snapshot's `signals` array. Any unmatched value → the LLM hallucinated a signal → `bank.status = 'failed'` with error listing unmatched values.
2. **Knockout → mandatory coherence:** for each question's `signal_values`, look up the matching signals in the snapshot; if any of them has `knockout=true` but the question has `is_mandatory=false`, the server flips `is_mandatory=true` and logs a warning (the LLM should have set this but didn't — safe auto-correction).
3. **Signal type filter enforcement:** every signal referenced by a question must have `type` in `stage.signal_filter.include_types`. Any violation → `bank.status = 'failed'` (the LLM probed a signal type this stage is not supposed to touch).
4. **Duration budget:** sum of `estimated_minutes` is within 85–105% of `stage.duration_minutes`. Warning only, doesn't fail the bank (recruiter can regenerate or edit).
5. **Question count within range for stage type.** Warning only.
6. **No duplicate signal coverage at same depth** (two questions both probing signal X at "depth" level). Warning only.

Failures in 1 and 3 → bank → `failed` with specific error message naming the offending signals.
Warning in 2 is auto-corrected server-side before writing to DB.
Warnings in 4, 5, 6 → log via structlog and proceed.

### Dramatiq actors

```python
@dramatiq.actor(max_retries=2, time_limit=60_000)
def generate_question_bank_stage(bank_id: str) -> None:
    """Generate questions for ONE stage's bank. Retries on transient failures."""

@dramatiq.actor(max_retries=0, time_limit=600_000)
def generate_question_bank_pipeline(instance_id: str, started_by: str) -> None:
    """Generate banks for ALL stages in a pipeline, SEQUENTIALLY.

    Sequential is REQUIRED for the anti-lie invariant — stage N needs to see
    stages 1..N-1's questions. Parallel execution would break coherence.

    On mid-pipeline failure: marks that stage 'failed', CONTINUES to next stage.
    User retries failed stages individually.
    """

@dramatiq.actor(max_retries=2, time_limit=30_000)
def regenerate_question(question_id: str, replace_signal_values: list[str] | None = None) -> None:
    """Regenerate a single question slot. Uses the regenerate-one prompt
    template which takes 'other questions in this bank' as 'do not duplicate'
    context. Replaces the question in place, preserving its UUID.

    replace_signal_values: if provided, the new question probes these signals
    instead of the original's signals. Otherwise, it probes the same signals
    as the question being replaced. Source flips to 'ai_regenerated'.

    On completion, bank.status flips confirmed → reviewing if it was confirmed
    (consistent with the edit auto-revert rule)."""
```

**Why `max_retries=0` on the pipeline actor:** retrying a 10-minute actor is expensive and risks double-generation. Individual stage retries cover transient failures.

**Dramatiq queue:** use the same queue the existing JD enhancement actors use (Phase 2A's `extract_and_enhance_jd` and re-enrich actors). If Phase 2A registered actors on the default queue, these new actors go there too. If Phase 2A has a dedicated AI queue, reuse that name. Consistency with the existing pattern > naming aspiration.

### SSE status stream

Reuses the SSE infrastructure from Phase 2A's `app/modules/jd/sse.py`. Endpoint: `GET /api/jobs/{id}/pipeline/questions/status-stream`.

Event shapes:

```json
{"type": "bank.status_changed", "stage_id": "...", "status": "generating"}
{"type": "bank.status_changed", "stage_id": "...", "status": "reviewing",
 "question_count": 6, "total_minutes": 42}
{"type": "bank.status_changed", "stage_id": "...", "status": "failed",
 "error": "OpenAI API timeout after 60s"}
{"type": "bank.question_updated", "stage_id": "...", "question_id": "...",
 "action": "replaced"}
{"type": "pipeline.generation_complete", "succeeded": 2, "failed": 1, "total": 3}
```

### Error recovery

Generation failure → `bank.status = 'failed'`, `bank.generation_error = error_message`, SSE emits failure event. The frontend shows a red error card with a "Retry" button. Retry → POST the same generate endpoint → bank transitions back to `generating`.

### Cost and latency envelope

Rough numbers for a 5-stage pipeline (GPT-5 rates):
- Input tokens per stage: ~4k
- Output tokens per stage: ~3k
- Per-stage cost: ~$0.03–0.05
- Full pipeline (5 stages sequential): ~$0.15–0.25
- With worst-case retries: ~$0.50 max per job

At 100 jobs/month: ~$50/month in LLM costs. Negligible.

Latency:
- Single stage: ~10–30 seconds
- Full pipeline (5 stages sequential): ~60–150 seconds
- Single-question regeneration: ~5–15 seconds

All async via Dramatiq; the user sees progress via SSE.

---

## API Endpoints

11 endpoints, all nested under `/api/jobs/{job_id}/pipeline/...`. All use `get_tenant_db` + `get_current_user_roles`. A new `require_bank_access(db, bank_id, user, action)` helper walks `bank → stage → instance → job → org_unit → ancestry` for authz.

### Read endpoints

```
GET  /api/jobs/{job_id}/pipeline/questions
     → BanksOverviewResponse { banks: [BankResponse] }
     Lightweight list for the sidebar — one row per stage. Does NOT include
     question text/rubric/evidence.

GET  /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions
     → BankWithQuestionsResponse { ...bank fields, questions: [QuestionResponse] }
     Full detail for the main pane.
```

### Generation endpoints

```
POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/generate
     Response: 202 { bank_id, status: 'generating' }
     - Creates 'draft' bank if missing
     - 409 if bank.status == 'generating' (already in flight)
     - Fires single-stage Dramatiq actor

POST /api/jobs/{job_id}/pipeline/questions/generate-all
     Response: 202 { instance_id, status: 'generating' }
     - 409 if ANY bank in the pipeline is 'generating'
     - Fires sequential pipeline Dramatiq actor

POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}/regenerate
     Request: { replace_signal_values?: string[] }
     Response: 202 { question_id, status: 'regenerating' }
     - Fires single-question regen actor
     - On completion: bank status flips confirmed → reviewing if needed
```

### Mutation endpoints (recruiter edits)

```
POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions
     Request: CreateQuestionBody (full question with all fields required,
              includes signal_values: string[] matching the pinned snapshot)
     Response: 201 { question: QuestionResponse }
     - source = 'recruiter' (forced server-side)
     - Validates every signal_value exists in the bank's pinned snapshot AND
       is in stage.signal_filter.include_types
     - Bank status flips draft → reviewing (if it had no questions before)
     - Bank status flips confirmed → reviewing (if it was confirmed)

PATCH /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}
      Request: UpdateQuestionBody (any subset of editable fields; signal_values
               follows the same validation as create)
      Response: 200 { question: QuestionResponse }
      - Sets edited_by_recruiter = true
      - Bank status flips confirmed → reviewing if needed
      - On auto-revert: clears bank.confirmed_at and bank.confirmed_by
        (audit trail lives in audit_log, not on the bank row)

DELETE /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}
       Response: 204
       - Re-packs remaining positions to 0..N-1
       - Bank status flips confirmed → reviewing (auto-revert with confirmed_at cleared)

PATCH /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/reorder
      Request: { question_ids: UUID[] } — new order
      Response: 200 { bank: BankWithQuestionsResponse }
      - Validates incoming IDs match existing set
      - Atomic position reassignment
```

### State transition

```
POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/confirm
     Response: 200 { bank: BankResponse }
     - 409 if bank.status != 'reviewing'
     - 409 if ANY knockout signal has no mandatory question
     - 409 if duration budget is outside 50–150% of stage.duration_minutes
     - Sets status = 'confirmed', confirmed_at, confirmed_by
```

No explicit unconfirm endpoint — auto-revert on edit + regenerate-from-any-state covers every real use case.

### SSE stream

```
GET /api/jobs/{job_id}/pipeline/questions/status-stream
    Content-Type: text/event-stream
    Streams bank status transitions and question update events.
    Client reconnects via standard SSE on disconnect.
```

### Authz walkup helper

```python
async def require_bank_access(
    db: AsyncSession,
    bank_id: UUID,
    user: UserContext,
    action: Literal['view', 'manage'],
) -> tuple[StageQuestionBank, JobPipelineStage, JobPosting]:
    """Walks bank → stage → instance → job → org_unit → ancestry.

    - 404 when the bank doesn't exist in the tenant's scope. This includes
      cross-tenant access: RLS hides other tenants' rows, so a cross-tenant
      request looks identical to a missing bank (information hiding pattern).
    - 403 when the bank exists but the user lacks `jobs.{action}` on any
      ancestor org unit.
    """

async def require_question_access(db, question_id, user, action):
    """Same walk starting from a question."""
```

Uses the `get_org_unit_ancestry` helper from `app.modules.org_units.service` (promoted in Phase 2C.1's final polish commit).

---

## Frontend UX

### Route and navigation

- **New route:** `/jobs/[jobId]/questions/page.tsx`
- **Pipeline page gets ONE addition:** "Review questions →" button in the header with a "N of M banks confirmed" chip. Clicking navigates to the questions page.

### File layout

```
frontend/app/
├── app/(dashboard)/jobs/[jobId]/questions/
│   └── page.tsx
├── components/dashboard/question-bank/
│   ├── QuestionsReviewContent.tsx
│   ├── QuestionSidebar.tsx
│   ├── BankStatusBadge.tsx
│   ├── QuestionsMainPane.tsx
│   ├── BankHeader.tsx
│   ├── QuestionList.tsx
│   ├── QuestionCard.tsx
│   ├── QuestionEditForm.tsx
│   ├── QuestionRubricExpanded.tsx
│   ├── AddCustomQuestionDialog.tsx
│   └── ConfirmBankDialog.tsx
└── lib/hooks/
    ├── use-banks-overview.ts
    ├── use-bank-with-questions.ts
    ├── use-generate-questions.ts
    ├── use-regenerate-question.ts
    ├── use-save-question.ts
    ├── use-confirm-bank.ts
    └── use-questions-status-stream.ts
```

11 components, 7 hook files. Convention matches Phase 2C.1.

### Component tree

```
QuestionsReviewPage (outer page, auth guard)
  └─ QuestionsReviewContent (owns selectedStageId, loads banks overview)
      ├─ QuestionSidebar (stages + BankStatusBadges)
      └─ QuestionsMainPane (loads selected bank's full detail)
          ├─ BankHeader (title + action buttons + auto-save indicator)
          ├─ QuestionList
          │   └─ QuestionCard (×N, collapsed → expanded)
          │       ├─ QuestionEditForm (inline, auto-save)
          │       └─ QuestionRubricExpanded (evidence, red flags, follow-ups, rubric)
          └─ AddCustomQuestionButton (opens AddCustomQuestionDialog)
```

### State management

- **Server state:** TanStack Query. Keys: `['banks', jobId]` for overview, `['bank', jobId, stageId]` for detail.
- **UI state:** local `useState` for selected stage, expanded question, edit-in-progress, dialog flags.
- **Auto-save:** debounced PATCH on question edits, same pattern as the pipeline editor (`saveTimerRef` + `isDirty` + unmount-flush). 800ms debounce, "All changes saved" / "Saving…" / "Failed" indicator in the BankHeader.
- **SSE subscription:** `useQuestionsStatusStream(jobId)` via `@microsoft/fetch-event-source`. Invalidates TanStack Query keys on each event.

### SSE → query invalidation mapping

```
SSE event                               →  invalidates
─────────────────────────────────────────  ────────────────────────────────
bank.status_changed (any stage)         →  ['banks', jobId]
bank.status_changed (selected stage)    →  ['bank', jobId, stageId]
bank.question_updated                   →  ['bank', jobId, stageId]
pipeline.generation_complete            →  ['banks', jobId]
```

### Key interaction flows

**Flow 1 — First visit:**
1. Recruiter clicks "Review questions →" on pipeline page → `/jobs/{id}/questions`
2. Sidebar populates with all stages in `draft` state
3. First stage auto-selected → main pane shows empty state with "Generate questions" button

**Flow 2 — Generate questions for one stage:**
1. Click "Generate questions" → POST 202
2. SSE → `bank.status_changed: generating`
3. Main pane shows live progress card
4. ~20s later, SSE → `bank.status_changed: reviewing`
5. Questions appear, badge flips to REVIEWING (amber)

**Flow 3 — Edit a question inline:**
1. Click card → expands
2. Click "Edit" on evaluation_hint → inline editor
3. Type → 800ms debounce → PATCH
4. "Saving…" → "All changes saved" → `edited_by_recruiter=true` badge appears

**Flow 4 — Regenerate one question:**
1. ⋯ menu → "Regenerate this question"
2. Confirm dialog: "Replace this AI-generated question? Your edits will be lost."
3. POST 202 → card enters spinner overlay
4. SSE → `bank.question_updated` → card refreshes with new content

**Flow 5 — Add custom question:**
1. "+ Add a custom question" → dialog with full form (text, signal picker, rubric, evidence)
2. Signal picker shows signals from pinned snapshot filtered by stage's `include_types`
3. Client-side Zod validation → POST 201 → question appears at end of list with `recruiter` badge

**Flow 6 — Confirm bank:**
1. Click "Confirm bank" → `ConfirmBankDialog` opens with coverage summary
2. If any knockout uncovered → red warning, button disabled
3. On confirm → POST 200 → badge flips to CONFIRMED (green)

**Flow 7 — Editing a confirmed bank:**
1. Recruiter opens a confirmed bank and edits a question
2. Auto-save PATCH → server flips `status = 'reviewing'`
3. Sidebar badge flips CONFIRMED → REVIEWING
4. Banner in main pane: "Bank was unlocked for editing. Re-confirm when done."

### Visual states (color-coded across sidebar, main pane, toasts)

| State | Color | Icon | UI affordance |
|---|---|---|---|
| `draft` | gray | — | "Generate questions" button |
| `generating` | blue | spinner | Progress card, generate disabled |
| `reviewing` | amber | — | "Confirm bank" button, editable |
| `confirmed` | green | lock | Shows `confirmed_at` timestamp |
| `failed` | red | alert | Error card with "Retry" |

### Empty / loading / error states

- **No pipeline:** redirect to `/jobs/{id}/pipeline` with "Build a pipeline first"
- **All banks draft:** "No questions generated yet. Click 'Generate all' to create banks for every stage."
- **Selected stage empty:** "This stage has no questions yet. Click 'Generate questions' to start."
- **All banks confirmed:** subtle banner: "All banks confirmed and ready for interviews ✓"
- **Signal snapshot stale:** yellow banner: "Signals have changed since this bank was generated. Click 'Regenerate' to pick up the latest signals."
- **Generation failure:** red error card with error message + Retry
- **Save failure:** red toast with retry

### Staleness detection (`is_stale` flag)

Computed server-side when serializing the `BankResponse`. The `job_postings` table does NOT have a `latest_confirmed_snapshot_id` column — instead, the "latest confirmed snapshot" is derived via a query against `job_posting_signal_snapshots`:

```sql
SELECT id FROM job_posting_signal_snapshots
WHERE job_posting_id = :job_id AND confirmed_at IS NOT NULL
ORDER BY version DESC
LIMIT 1
```

The `is_stale` flag is true when `bank.signal_snapshot_id != <result of the above query>`. The service caches this lookup across all banks in a single request (one query per job, not per bank) when building `BanksOverviewResponse`.

When `is_stale=true`, the frontend shows a yellow banner on the affected bank: *"Signals have changed since this bank was generated. Click 'Regenerate' to pick up the latest signals."* Prevents silent drift.

### Tests (vitest)

Two new component test files:
- `tests/components/QuestionCard.test.tsx` — render, expand, inline edit triggers PATCH
- `tests/components/BankStatusBadge.test.tsx` — all 5 states with correct colors/icons

---

## Testing Strategy

### Backend test files

```
backend/nexus/tests/
├── test_question_banks_schemas.py          (~8 tests)
├── test_question_banks_service.py          (~25 tests)
├── test_question_banks_authz.py            (~10 tests)
├── test_question_banks_actors.py           (~12 tests — mocked LLM)
├── test_question_banks_router.py           (~18 tests)
├── test_question_banks_integration.py      (~3 end-to-end tests)
└── test_pipeline_stage_id_stability.py     (~5 regression tests for 2C.1 fix)
```

### Key backend test coverage

- Schema validation (Pydantic constraints, min/max bounds)
- State machine transitions (legal + illegal)
- Edit → `confirmed → reviewing` auto-revert
- Cascade behavior (stage/job/instance delete → bank + questions gone)
- Tenant isolation (RLS)
- `is_stale` computation
- Knockout coverage enforcement at confirm time
- Duration budget validation at confirm time
- Authz walkup correctness
- Actor flow with mocked LLM (happy path + failure paths)
- Sequential pipeline generation preserves prior-stage context
- Regenerate-one preserves question UUID
- SSE status events
- All 11 HTTP endpoints: happy + error paths
- End-to-end integration test (create job → confirm signals → apply pipeline → generate → edit → confirm)

### 2C.1 regression tests (critical)

- Update pipeline stages with IDs preserved → row UUIDs unchanged
- Update adds a new stage → existing stages unchanged
- Update removes a stage → other rows preserved
- Questions FK'd to a stage survive a stage update ← the key regression for 2C.2

### Frontend tests (vitest)

- `QuestionCard.test.tsx` (~4 tests)
- `BankStatusBadge.test.tsx` (~5 tests)

### What we explicitly DON'T test

- LLM output quality per se (stochastic; manual eval + Langfuse traces)
- Prompt A/B comparisons (deferred to eval phase)
- Load testing (deferred to Phase 3)
- Cross-browser compatibility (Chrome-only for dev)

### Test count estimate

| Category | Tests |
|---|---|
| Backend schemas | ~8 |
| Backend service | ~25 |
| Backend authz | ~10 |
| Backend actors | ~12 |
| Backend router | ~18 |
| Backend integration | ~3 |
| Backend regression (2C.1 fix) | ~5 |
| Frontend components | ~9 |
| **Total new tests** | **~90** |

Post-Phase-2C.2 target: ~261 backend + ~22 frontend tests (baseline 180 + 13 → +81 + 9).

### Observability

- structlog correlation_id on every bank operation
- Langfuse traces for every LLM call
- Dramatiq queue metrics
- Sentry for uncaught exceptions
- Audit log (`log_event`) for every state transition and recruiter edit

---

## Migration + Rollout

### Migration

**One new migration: `0006_question_banks.py`**
- `down_revision = "0005_simplify_signal_filter"`
- Creates both tables with RLS, indexes, constraints
- No data migration (no pre-existing question bank data)
- Down migration drops both tables

The 2C.1 fix is a code change only — no migration.

### Rollout plan

1. Deploy backend first with migration applied. New endpoints exist; no UI calls them.
2. Deploy frontend. Pipeline page gets "Review questions →" button.
3. Smoke-test in production with 1–2 real jobs.
4. Monitor Langfuse + Sentry for 24h.
5. If clean → announce. If bugs → fix, redeploy.

**No feature flag.** New surface, opt-in via the button. Blast radius is contained.

### Rollback

- **Frontend revert** → button disappears → users can't reach new surface
- **Backend revert** → endpoints disappear; in-flight Dramatiq actors finish gracefully
- **`alembic downgrade 0005_simplify_signal_filter`** → both tables dropped
- No user-visible data loss in MVP (Phase 3 not built yet, so nothing consumes confirmed banks)

### Smoke test checklist (definition of done)

Before marking the phase complete, run through this manually:

1. Create new job → paste real JD → wait for extraction
2. Confirm signals on the new job
3. Auto-apply fires → pipeline applied from org unit default template
4. Navigate to `/jobs/{id}/pipeline` → see "Review questions →" button with "0 of 3 banks confirmed"
5. Click → `/jobs/{id}/questions` loads
6. Sidebar shows 3 stages in "Draft"
7. Click "Generate all" → SSE shows transitions through `generating` → `reviewing`
8. ~60–150s later, all stages show REVIEWING with question counts
9. Click each stage → verify question cards render with full rubric on expand
10. Edit a question text → verify auto-save fires → status flips to REVIEWING
11. ⋯ menu → "Regenerate this question" → spinner → new question arrives
12. Add a custom question → `recruiter` badge displayed
13. Click "Confirm bank" on stage 1 → coverage summary → confirm → green badge
14. Navigate back to pipeline page → header chip now shows "1 of 3 banks confirmed"
15. Delete a mandatory question covering a knockout in stage 2 → try to confirm stage 2 → 409 error with specific message
16. Edit a stage's duration in the pipeline editor → navigate back to questions → bank still exists (tests the 2C.1 fix)
17. Swap the pipeline template → old banks cascade-deleted → new empty banks appear

Any failure in 1–17 blocks the phase.

---

## Scope Boundaries

### What's in scope for Phase 2C.2

- Data model: 2 new tables, 1 migration, RLS
- Generation: 3 Dramatiq actors, 7 prompt files, `instructor`-validated structured output
- State machine: `draft → generating → reviewing → confirmed` + `failed`
- API: 11 endpoints + SSE status stream
- Frontend: new `/jobs/{id}/questions` route, 11 components, 7 hook files
- Anti-lie coherence: full prior-stage context in prompts, weight-based re-verification
- Bank confirmation gate: hard block on uncovered knockouts + duration budget
- 2C.1 fix: `update_job_pipeline_stages` preserves stage IDs
- Observability: Langfuse + Sentry + structlog + audit log
- Testing: ~90 new tests

### What's explicitly OUT of scope (deferred)

| Deferred | To where | Why |
|---|---|---|
| Phase 3 session consumption | Phase 3 | Different surface |
| Per-candidate customization | Phase 4+ | Needs candidate data layer |
| Question library / cross-job reuse | Phase 4+ | YAGNI |
| A/B prompt evolution | Phase 4+ | Needs session data |
| Real-answer calibration | Phase 4+ | Needs production session data |
| Full edit history / versioning | Phase 2D or 4 | `edited_by_recruiter` flag is MVP-sufficient |
| Question analytics | Phase 4+ | Needs cumulative session data |
| Drag-to-reorder | N/A | Move-up/down is enough |
| Search / filter within a bank | N/A | Banks are small |
| Multi-language questions | Phase 4+ | English-only |
| Video/audio/code questions | Phase 4+ | Text-only |
| Bulk confirm | N/A | Per-stage review is deliberate friction |
| Non-knockout coverage report | Phase 2D+ | Knockout gate covers critical case |
| Bank export / print | Phase 3 | Needed when real human panels exist |
| Collaborative editing | N/A | Last-write-wins |
| Feature flag rollout | N/A | Additive surface, easy rollback |

### Edge cases spelled out

**Regenerating a bank when questions have been recruiter-edited:** Confirm dialog loudly warns about edits that will be lost. All `source='ai_generated'` questions are wiped regardless of `edited_by_recruiter`. Only `source='recruiter'` questions are preserved.

**Mid-pipeline generation failure:** "Generate all" actor continues to next stage on failure. Failed stage gets marked `failed` with error; recruiter retries individually.

**Signal snapshot changes during in-flight generation:** Actor pins `signal_snapshot_id` at start; completes against pinned version; resulting bank is immediately `is_stale=true` if job's latest snapshot has moved forward.

**Signal removal after confirmation:** Bank stays confirmed, but `is_stale=true` surfaces the drift. Recruiter regenerates to pick up current state.

**Two recruiters editing simultaneously:** Last-write-wins via optimistic PATCH. TanStack Query refetches on window focus.

### Design invariants (must not be violated in future phases)

1. Every generated question must have positive evidence, red flags, and a 3-level rubric.
2. Every knockout signal must be probed by a mandatory question in at least one stage before the bank is confirmable.
3. A confirmed bank's questions cannot be silently replaced. Regenerate is explicit, auto-revert on edit is the only auto-transition.
4. Signal snapshot pinning is sacred. Questions carry FK to a specific snapshot version forever.
5. Provenance is immutable. `source` field doesn't change except via explicit conversion action (deferred).
6. The anti-lie invariant is a prompt rule, not a schema rule. Tests must verify the prompt's behavior via mocked LLM.
7. Questions don't cross stage boundaries. One bank belongs to one stage.
8. The bank's `status` field is the single source of truth for Phase 3 eligibility.

### Future-phase hooks

- **Phase 2D** (state machine lock + ATS stub): adds a job-level `questions_confirmed` status. No 2C.2 schema changes needed.
- **Phase 3** (session engine): reads confirmed banks, uses `probes.follow_ups` for live probing, `positive_evidence`/`red_flags` for real-time scoring, `rubric` for final scoring, enforces `is_mandatory`. No 2C.2 changes.
- **Phase 4** (candidate personalization): adds a layer of per-candidate variation derived from the bank + candidate context. Bank remains a template. No 2C.2 changes.
- **Phase 5** (prompt evolution / A-B eval): `bank.prompt_version` column already supports this. No 2C.2 changes.

Every follow-on phase can consume the 2C.2 data model without schema changes — the test that we've scoped the boundary correctly.

---
