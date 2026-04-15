# Phase 2C.2 Implementation — Developer Documentation

**Scope:** Per-stage question bank generation — Dramatiq actor with adaptive coverage, mandatory-demotion auto-correction, bundling discipline, coverage-notes audit trail, SSE progress stream, and the recruiter review surface
**Status:** Complete and functional
**Last updated:** 2026-04-15

See also:
- Design spec: `docs/superpowers/specs/2026-04-12-phase-2c2-question-generation-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-12-phase-2c2-question-generation.md`
- Phase 2C.1 walkthrough: `docs/phase-2c1-implementation.md` (pipeline builder — provides the stages this phase attaches banks to)
- Phase 2B walkthrough: `docs/phase-2b-implementation.md` (signal snapshots — every bank pins one)
- Phase 2A walkthrough: `docs/phase-2a-implementation.md` (`app/ai/` layer, Dramatiq actor pattern, Langfuse wiring)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema (migrations 0006, 0007)](#2-database-schema-migrations-0006-0007)
3. [Generation Flow](#3-generation-flow)
4. [Adaptive Coverage and Mandatory Demotion](#4-adaptive-coverage-and-mandatory-demotion)
5. [Read-Idempotent `list_banks` GET](#5-read-idempotent-list_banks-get)
6. [Bulk Load — `get_banks_for_pipeline`](#6-bulk-load--get_banks_for_pipeline)
7. [State Machine](#7-state-machine)
8. [Langfuse Wiring](#8-langfuse-wiring)
9. [SSE Progress Stream](#9-sse-progress-stream)
10. [API Reference](#10-api-reference)
11. [Frontend Architecture](#11-frontend-architecture)
12. [Known Gaps](#12-known-gaps)
13. [Cross-references](#13-cross-references)

---

## 1. Architecture Overview

Phase 2C.1 ended with a recruiter owning a pipeline instance: one `job_pipeline_instances` row per confirmed job and an ordered list of `job_pipeline_stages` rows hanging off it. Phase 2C.2 attaches, to each of those stage rows, a **bank of structured interview questions** — 8–15 rich entries per stage with rubric anchors, evidence items, red flags, follow-ups, and explicit signal references. Generation is manually triggered per stage (or for the whole pipeline at once) and runs in a Dramatiq actor against the OpenAI API via `app/ai/`; the resulting bank lives in two new tables (`stage_question_banks`, `stage_questions`) and carries a per-bank state machine from `draft` through `generating` → `reviewing` → `confirmed`.

Four ideas do the heavy lifting:

1. **One bank per stage, not per job.** `stage_question_banks.stage_id` is `UNIQUE`, FK to `job_pipeline_stages.id`, `ON DELETE CASCADE`. That cascade is load-bearing twice: it means swap/reset in the pipeline editor wipes question rows automatically, *and* it means Phase 2C.1's diff-and-sync stage update (`pipelines/service.py::update_job_pipeline_stages`) — which preserves stage UUIDs through edits — is the reason bank rows survive every rename, difficulty tweak, and duration change in the upstream pipeline editor. Banks FK to stage ids, so the UUID stability contract is a 2C.1 invariant that 2C.2 depends on. See Phase 2C.1 Section 4 for the diff-and-sync walk.
2. **Per-stage-type prompt specialization on top of a shared common header.** A single `prompts/v1/question_bank_common.txt` carries five core principles (anti-lie invariant, evidence-based scoring, signal allocation math, mandatory-knockout handling, company tone) and five per-stage-type files (`question_bank_phone_screen.txt`, `..._ai_interview.txt`, `..._human_interview.txt`, `..._panel_interview.txt`, `..._take_home.txt`) specialize count/style/depth. A sixth file `question_bank_regenerate_one.txt` powers the single-question regen flow. The common header and the specialization are concatenated at call time by `PromptLoader.load_pair('question_bank_common', type_prompt)` — no subclassing, no inheritance, just two files read and joined with `\n\n`.
3. **Adaptive coverage with mandatory demotion.** Stage `duration_minutes` is a **session time limit** that the live interview bot honors, not a generation budget. Recruiters want the full menu generated — the session bot will skip what it cannot fit. The *only* hard constraint is that the sum of `estimated_minutes` across **mandatory** questions must fit inside `duration_minutes`, because mandatory questions are unskippable. A post-LLM pass in `service.validate_llm_output_against_snapshot` walks questions in position order, claims exactly one mandatory slot per distinct knockout signal for the earliest question that probes it, and **auto-demotes** subsequent knockout-probing questions to optional depth probes. `validate_mandatory_fits_session` then re-checks the mandatory budget at confirmation time and raises `MandatoryOverrunError` if it still does not fit. Section 4 walks the full logic.
4. **Read-idempotent sidebar overview.** The `GET /api/jobs/{id}/pipeline/questions` endpoint is the sidebar's data source and gets polled aggressively from `useQuestionsStatusStream` + TanStack Query. Before the Batch G fix (commit `23e78bc`), that handler called `ensure_bank_exists` in a loop and wrote a `draft` row for every stage on every request — 8 rows per poll for an 8-stage pipeline, a slow leak into `stage_question_banks`. The endpoint is now strictly read-only: stages with no bank get a synthetic `PlaceholderBankResponse(status="not_generated")` and the only write path into `stage_question_banks` is `POST /.../questions/generate` (which still calls `ensure_bank_exists` lazily, as the spec intends). Section 5 details the fix.

Phase 2C.2 does not touch the JD state machine or the pipeline state machine. A pipeline instance has no `status` column; only individual bank rows transition. A 3-stage pipeline can have stage 1 confirmed, stage 2 reviewing, and stage 3 still draft at the same time.

### Module layout (`backend/nexus/app/modules/question_bank/`)

```
question_bank/
├── __init__.py
├── router.py          ← /api/jobs/{id}/pipeline/questions (read-idempotent list),
│                        /api/jobs/{id}/pipeline/stages/{id}/questions (detail + CRUD),
│                        /api/jobs/{id}/pipeline/stages/{id}/questions/generate,
│                        /api/jobs/{id}/pipeline/questions/generate-all,
│                        /api/jobs/{id}/pipeline/stages/{id}/questions/{qid}/regenerate,
│                        /api/jobs/{id}/pipeline/stages/{id}/questions/reorder (literal-first),
│                        /api/jobs/{id}/pipeline/stages/{id}/questions/confirm,
│                        /api/jobs/{id}/pipeline/questions/status-stream (SSE)
├── service.py         ← ensure_bank_exists, get_banks_for_pipeline (4-query bulk),
│                        validate_llm_output_against_snapshot (mandatory demotion pass),
│                        validate_knockout_coverage, validate_mandatory_fits_session,
│                        write_generated_questions, replace_question_in_place,
│                        create_recruiter_question, update_question, delete_question,
│                        reorder_questions, confirm_bank
├── state_machine.py   ← BankStatus Literal + LEGAL transition map,
│                        transition_to_generating/reviewing/failed/confirmed,
│                        auto_revert_on_edit
├── actors.py          ← @dramatiq.actor generate_question_bank_stage / _pipeline /
│                        regenerate_question; @observe _generate_one_bank +
│                        _regenerate_one_question helpers
├── authz.py           ← require_bank_access, require_bank_access_by_stage,
│                        require_question_access, require_pipeline_access
├── schemas.py         ← GeneratedQuestion / QuestionRubric / StageQuestionBankOutput
│                        (LLM output) + Create/Update/Reorder/RegenerateBody (API input) +
│                        BankResponse / BankWithQuestionsResponse /
│                        PlaceholderBankResponse / BanksOverviewResponse
├── sse.py             ← stream_question_bank_status — polls via get_tenant_session,
│                        dedup via last_snapshots dict, idle timeout, disconnect detect
└── errors.py          ← BankAlreadyGeneratingError, IllegalTransitionError,
                         BankNotInReviewingError, KnockoutUnprobedError,
                         MandatoryOverrunError, SignalValueNotInSnapshotError,
                         SignalTypeNotAllowedError, ReorderMismatchError,
                         ReorderDuplicateError
```

### Prompt files (`backend/nexus/prompts/v1/`)

```
question_bank_common.txt            ← shared header: principles + anti-lie + ordering rules
question_bank_phone_screen.txt      ← short, verification-focused, knockout-heavy
question_bank_ai_interview.txt      ← deep, multi-level probing
question_bank_human_interview.txt   ← behavioral + technical mix
question_bank_panel_interview.txt   ← calibration-grade, senior bar
question_bank_take_home.txt         ← structured assignment
question_bank_regenerate_one.txt    ← single-question regeneration
```

### Frontend surface (`frontend/app/`)

```
app/(dashboard)/jobs/[jobId]/
├── questions/page.tsx              ← redirect-only shell — forwards to /pipeline?stage=...
└── pipeline/page.tsx               ← hosts UnifiedPipelineView, which owns the
                                       inspector panel (questions tab lives there)

components/dashboard/question-bank/
├── QuestionsMainPane.tsx           ← main pane — header + question list + dialogs
├── BankHeader.tsx                  ← title, status badge, is_stale banner, generate /
│                                       regenerate / add / confirm buttons, save indicator
├── BankStatusBadge.tsx             ← draft / generating / reviewing / confirmed / failed
├── QuestionList.tsx                ← renders QuestionCards with single-expanded accordion
├── QuestionCard.tsx                ← collapsed + expanded header, MANDATORY / CUSTOM /
│                                       REGENERATED / EDITED badges, 3-dot menu
├── QuestionEditForm.tsx            ← inline text + evaluation_hint edit, 800 ms debounce,
│                                       key={question.id} remount (commit 2d16c2f)
├── QuestionRubricExpanded.tsx      ← rubric + evidence + red flags + follow-ups view
├── AddCustomQuestionDialog.tsx     ← hand-written recruiter question form
└── ConfirmBankDialog.tsx           ← pre-confirm coverage summary + Escape-to-close

lib/api/question-banks.ts           ← typed questionBanksApi namespace
lib/hooks/
├── use-banks-overview.ts           ← GET list (sidebar)
├── use-bank-with-questions.ts      ← GET detail (main pane)
├── use-generate-questions.ts       ← POST stage + POST generate-all
├── use-regenerate-question.ts      ← POST /regenerate
├── use-save-question.ts            ← create, update, delete, reorder mutations
├── use-confirm-bank.ts             ← POST /confirm
└── use-questions-status-stream.ts  ← SSE via fetch-event-source, ref-mirrored
                                       selectedStageId (commit 2dfa766)
```

The "questions page" route `/jobs/[jobId]/questions` is a single-file redirect: it reads `useBanksOverview`, picks the first stage, and `router.replace`s to `/jobs/{id}/pipeline?stage=...`. **Spec drift** — the design spec proposed a dedicated questions page with sidebar + main pane; what shipped is one merged page (`/jobs/{id}/pipeline`) where `UnifiedPipelineView` hosts the `StageInspectorPanel` with two tabs ("Questions" + "Configuration"). The question-bank surface is a tab inside the pipeline editor, not a sibling page. The redirect preserves spec-era links in the wild.

---

## 2. Database Schema (migrations 0006, 0007)

Phase 2C.2 ships two Alembic migrations. `0006_question_banks` creates both tables with their constraints, indexes, and RLS policies. `0007_add_coverage_notes` adds a single `coverage_notes TEXT` column to `stage_question_banks` so the LLM's allocation chain-of-thought is persisted for audit rather than lost in the Langfuse trace.

Head after 0007: `0007_add_coverage_notes`. Subsequent migrations 0008–0012 are the RLS hardening pass and are described in the hardening walkthrough.

### `0006_question_banks`

Down revision: `0005_simplify_signal_filter`. Creates two tables, enables RLS on both, and installs the v1 RLS policy pair on each.

#### `stage_question_banks`

One row per `job_pipeline_stages.id`. Status-machine root.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `tenant_id` | UUID NOT NULL, FK → `clients.id` | RLS scoping |
| `stage_id` | UUID NOT NULL, FK → `job_pipeline_stages.id` **ON DELETE CASCADE** | Unique via `ix_stage_question_banks_stage` |
| `job_posting_id` | UUID NOT NULL, FK → `job_postings.id` **ON DELETE CASCADE** | Convenience for bulk loads and RLS predicate wiring |
| `signal_snapshot_id` | UUID NOT NULL, FK → `job_posting_signal_snapshots.id` | Pinned at bank creation; **no CASCADE** — snapshots are append-only |
| `status` | TEXT NOT NULL DEFAULT `'draft'` | CHECK `IN ('draft','generating','reviewing','confirmed','failed')` |
| `prompt_version` | TEXT NOT NULL DEFAULT `'v1'` | Bumps on prompt evolution (none shipped yet) |
| `generation_error` | TEXT NULL | Error message when `status = 'failed'` |
| `generated_at` | TIMESTAMPTZ NULL | Set on `generating → reviewing` |
| `generated_by` | UUID NULL, FK → `users.id` | Recruiter who triggered generation |
| `confirmed_at` | TIMESTAMPTZ NULL | Set on `reviewing → confirmed`, cleared on auto-revert |
| `confirmed_by` | UUID NULL, FK → `users.id` | Same lifecycle |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | Stamped from Python on every mutation — no DB trigger |
| `coverage_notes` | TEXT NULL | Added by 0007; LLM's allocation chain-of-thought |

**Indexes:**
- `ix_stage_question_banks_stage` UNIQUE on `(stage_id)` — one bank per stage.
- `ix_stage_question_banks_job` on `(job_posting_id)` — bulk loads keyed by job.
- `ix_stage_question_banks_tenant_status` on `(tenant_id, status)` — future ops dashboards.
- `ix_stage_question_banks_snapshot` on `(signal_snapshot_id)` — trace which banks pinned which snapshot.

#### `stage_questions`

Rich question rows. One-to-many under `stage_question_banks`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID NOT NULL, FK → `clients.id` | |
| `bank_id` | UUID NOT NULL, FK → `stage_question_banks.id` **ON DELETE CASCADE** | |
| `position` | INTEGER NOT NULL | CHECK `>= 0`; re-packed to 0..N-1 after every mutation |
| `source` | TEXT NOT NULL | CHECK `IN ('ai_generated','ai_regenerated','recruiter')` |
| `text` | TEXT NOT NULL | Question prompt |
| `signal_values` | `TEXT[]` NOT NULL | 1–3 values, exact match against pinned snapshot's signals |
| `estimated_minutes` | `NUMERIC(4,1)` NOT NULL | Per-question time budget hint |
| `is_mandatory` | BOOLEAN NOT NULL DEFAULT false | One per knockout signal; auto-corrected by service |
| `follow_ups` | JSONB NOT NULL DEFAULT `'[]'` | 0–3 short follow-up probes |
| `positive_evidence` | JSONB NOT NULL DEFAULT `'[]'` | 3–5 items |
| `red_flags` | JSONB NOT NULL DEFAULT `'[]'` | 2–3 items |
| `rubric` | JSONB NOT NULL | `{excellent, meets_bar, below_bar}` anchors |
| `evaluation_hint` | TEXT NOT NULL | 1-line scoring hint |
| `edited_by_recruiter` | BOOLEAN NOT NULL DEFAULT false | Flipped on any recruiter update, cleared on regeneration |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | Stamped from Python |

**Indexes:**
- `ix_stage_questions_bank_position` UNIQUE on `(bank_id, position)` — positions are contiguous within a bank.
- `ix_stage_questions_signal_values_gin` GIN on `signal_values` — "which questions probe signal X?" lookups in Phase 3.

Notable column choices:
- **`signal_values` is `TEXT[]`, not a UUID FK.** Phase 2B's signals live inside the `job_posting_signal_snapshots.signals` JSONB array and do not have stable UUIDs. Banks reference signals by their `value` string, and the bank's pinned `signal_snapshot_id` makes those references stable (snapshots are append-only; values inside a version never change).
- **`follow_ups` / `positive_evidence` / `red_flags` are JSONB, not child tables.** Small arrays, always read with the parent question, never queried independently.
- **`rubric` is a JSONB object, not three columns.** Prompt-version bumps can restructure without schema work.
- **No "depth target" column.** Phase 3 reads `stage.difficulty` via the `bank → stage` join instead, avoiding a terminology clash with stage difficulty.

### Cascade behavior

| Delete event | Cascade result |
|---|---|
| `job_postings` row | → `job_pipeline_instances` → `job_pipeline_stages` → `stage_question_banks` → `stage_questions` |
| `job_pipeline_instances` row (swap/reset) | → stages → banks → questions |
| `job_pipeline_stages` row (recruiter deletes a stage) | → bank → questions |
| `job_posting_signal_snapshots` row | **prevented** — FK is plain (no `ON DELETE CASCADE`); snapshots are append-only by design |
| Recruiter edits a stage field (name/duration/difficulty/etc.) | Stage id preserved by 2C.1's diff-and-sync → bank row **survives** |

No orphaned rows are possible under any supported workflow.

### RLS

0006 enables RLS on both tables and installs the v1 policy pair on each:

```sql
CREATE POLICY "tenant_isolation" ON <table>
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);

CREATE POLICY "service_role_bypass" ON <table>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**Spec drift — RLS pattern at ship time vs. canonical form.** This is the same three-way drift that Phase 2C.1's 0004 shipped with:

1. `tenant_isolation` has `USING` only — no `WITH CHECK`. This is the "FOR SELECT trap" documented in root `CLAUDE.md`: with only `USING`, the implicit CHECK falls through to `service_bypass`, which is false when `app.bypass_rls` is unset, silently blocking tenant-scoped INSERTs / UPDATEs / DELETEs.
2. Bypass policy is named `service_role_bypass` instead of the canonical `service_bypass`.
3. Tenant predicate uses raw `::uuid` without `NULLIF`, which crashes on the pooled-connection empty-GUC case.

All three are repaired by later migrations and are safe **today**:

- Migration **0011** (`rls_nullif_tenant`) drops every `tenant_isolation` policy on `stage_question_banks` and `stage_questions` and recreates them with the full-command form (`USING (...) WITH CHECK (...)`) **and** the `NULLIF(current_setting(...), '')::uuid` wrapping.
- Migration **0012** (`rename_service_role_bypass`) renames `service_role_bypass` → `service_bypass` on both tables.
- Migration **0010** introduces the `nexus_app` role (`NOBYPASSRLS`); `get_tenant_db` / `get_bypass_db` / `get_tenant_session` all run `SET LOCAL ROLE nexus_app` at session start. Without the switch, application queries connect as `postgres` (with `rolbypassrls=true`) and every policy is a silent no-op.
- **Startup assertion** `_assert_rls_completeness` in `app/main.py` lists both tables in `_TENANT_SCOPED_TABLES` and aborts boot if either is missing `tenant_isolation` (with non-NULL `WITH CHECK`) or `service_bypass`.

The net effect at runtime head (`0012_rename_service_role_bypass`) is the correct full-command, NULLIF-wrapped, canonically-named pair on both tables. Do not copy the 0006 policy DDL as a template.

### `0007_add_coverage_notes`

Down revision: `0006_question_banks`. Single column addition:

```python
op.add_column(
    "stage_question_banks",
    sa.Column("coverage_notes", sa.Text(), nullable=True),
)
```

**Spec drift — coverage notes persistence.** The original spec explicitly said `coverage_notes` would be a Langfuse-trace-only artifact, discarded after logging: *"Captured by Langfuse trace for debugging — NOT stored in the DB."* What shipped stores it on the bank row instead. The rationale lives in the 0007 docstring: *"persist the LLM's chain-of-thought about question allocation so recruiters/auditors can understand why the bank was structured a particular way."* The LLM output schema (`StageQuestionBankOutput.coverage_notes` in `schemas.py`) was widened to `max_length=2000` and its description updated to reflect the new persistence contract. The actor writes it via `bank.coverage_notes = result.coverage_notes` immediately before `write_generated_questions`. The `BankResponse` shape exposes it for frontend consumption though no component currently renders it — it is a future review-surface hook.

---

## 3. Generation Flow

Generation is a two-step user gesture: the recruiter clicks "Generate questions" (or "Regenerate all"), the router flips the bank into `generating` and commits, then a Dramatiq actor runs the LLM call + post-validation + DB writes asynchronously. SSE carries progress to the frontend.

### Per-stage generation — `POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/generate`

1. `require_bank_access_by_stage(db, job_id, stage_id, user, 'manage')` loads the stage + instance + job, walks ancestry for `jobs.manage`, and tries to load an existing bank row for the stage. Returns `(bank | None, stage, job)`. 404 if the stage doesn't exist for this job; 403 if the permission walk fails.
2. If `bank is None`, call `ensure_bank_exists(db, stage=stage, job=job)`. This helper loads the latest confirmed signal snapshot via `get_latest_confirmed_snapshot` (ORDER BY version DESC WHERE confirmed_at IS NOT NULL LIMIT 1) and creates a new row with `status='draft'`, `prompt_version='v1'`, and `signal_snapshot_id` pinned to that snapshot. If no confirmed snapshot exists, it raises `RuntimeError` — the router propagates this as a 500 (should never happen; generation is gated on `signals_confirmed` further upstream in 2C.1's auto-apply, but the guard is defensive).
3. `transition_to_generating(bank)` flips `status` to `generating` and clears `generation_error`. If the bank was already in `generating`, it raises `BankAlreadyGeneratingError` (mapped to HTTP 409 by the global exception handler in `app/main.py`).
4. `bank.generated_by = user.user.id`.
5. **Capture `bank.id` and `bank.tenant_id` into locals before commit.** After `db.commit()`, the tenant session has `app.current_tenant` unset and attribute refreshes would hit RLS and return zero rows. This is a shared footgun across the router — `reorder_questions_endpoint` and `confirm_bank_endpoint` also build their response objects before commit for the same reason, and the source comments call it out as an integration-test harness trap production does not cushion.
6. `await db.flush()` then `await db.commit()`.
7. Call `_safe_dispatch_generate_stage(bank_id, tenant_id, user.user.id)`, which wraps `bank_actors.generate_question_bank_stage.send(...)` in a try/except. If the enqueue fails (Redis outage, broker error), it opens a fresh `get_tenant_session`, verifies the bank is still in `generating`, transitions it to `failed` with the operator-friendly message `"Failed to enqueue generation job — please retry"`, commits, and raises HTTP 503. Without this, a failed `.send()` after the request-scoped commit would leave the bank stuck in `generating` forever with no actor running. Modeled after `jd/router.py::_safe_dispatch_extraction`.
8. Return `202 { bank_id, status: 'generating' }`.

### Dramatiq actor — `generate_question_bank_stage(bank_id, tenant_id, started_by)`

Registered with `max_retries=2`, `min_backoff=2_000`, `max_backoff=60_000` ms, `queue_name="question_bank_generation"`. Entry point:

1. `async with get_bypass_session() as db` opens a worker-side session. Unlike `get_tenant_session`, `get_bypass_session` does not pre-set `app.current_tenant`, so the actor executes a raw `SET LOCAL app.current_tenant = '<uuid>'` immediately to enable RLS for the session. `uuid.UUID(tenant_id)` canonicalises the input string before the SET to block any injection angle on the broker payload.
2. Load the bank, stage, instance, job, and pinned snapshot — one query each, all scoped by UUID.
3. Delegate to `_generate_one_bank(...)` which owns the LLM call, validation, and DB writes (see below).
4. On success, write a `question_bank.bank_generated` audit event via `log_event` and `await db.commit()`.
5. **On exception**, only commit if the bank's status is `'failed'`. Otherwise log `question_bank.stage_actor_rollback` with the observed status, roll back, and re-raise. Commit `1a0b847` introduced this check: before the fix, the catch-all committed regardless of whether `_generate_one_bank` had reached its own `except` branch — so a DB outage between the LLM call and the status-transition write would commit partially-written state. The `if bank.status == 'failed'` check is the invariant that "only the failed-status row gets persisted on unhappy path."

### `_generate_one_bank` — the observable generation path

Decorated with `@observe(name="question_bank_generate")`. Wrapping only this inner helper (rather than the whole actor) mirrors the `jd/actors.py::_run_extraction` pattern: @observe covers the LLM call + post-validation + write, but not the session bootstrap and not the audit-log write. Steps:

1. `langfuse_context.update_current_trace(session_id=str(bank.id), tags=[...], metadata={...})` stamps the trace with `bank_id`, `stage_id`, `stage_type`, `tenant_id`, `job_posting_id`, `model`, `reasoning_effort`, and `prompt_version`. `session_id=bank.id` groups all retries of the same bank into one Langfuse session. Section 8 has the full trace wiring.
2. `find_company_profile_in_ancestry(db, job.org_unit_id)` — re-use of Phase 2A's ancestry walker. Returns the nearest non-null `company_profile` JSONB going up the org unit tree. Used for the "company tone" prompt section (about / industry / company_stage / hiring_bar).
3. `_load_pipeline_context(db, instance_id=instance.id)` — loads every stage in the pipeline in position order, returning a list of dicts with id / position / name / stage_type / duration_minutes / difficulty / advance_behavior. Used in the prompt's "pipeline context" section so the LLM knows what stages come before and after the current one.
4. `_load_prior_stages_questions(db, instance_id=instance.id, current_position=stage.position)` — loads questions from every stage with `position < current_position`, grouped by stage. This is the **anti-lie input**: the LLM sees what earlier stages asked so it can re-probe at different angles instead of duplicating. Phase 2B's prompt-context-ordering rule (context before document) is followed here via `_build_user_message`: company profile + JD + signals first, then the pipeline context, then the current stage metadata.
5. `type_prompt = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)` resolves the stage-type key (`phone_screen` → `question_bank_phone_screen`, etc.). A missing mapping raises `RuntimeError` (every stage type in the 2C.1 enum has a prompt file).
6. `system_prompt = prompt_loader.load_pair('question_bank_common', type_prompt)` — `PromptLoader.load_pair` reads both files (via the in-memory cache) and returns `header + '\n\n' + specialization`. No inheritance, no template engine.
7. `user_message = _build_user_message(...)` — ordered `# JOB CONTEXT` → enriched JD → `# COMPANY PROFILE` → `# SIGNALS TO ASSESS` (full snapshot dump with value / type / priority / weight / knockout / stage_tag) → `# PIPELINE CONTEXT` (every stage, with prior-stage questions inlined under each) → `# THIS STAGE'S METADATA` → closing instruction. Strings are rendered via Python f-strings; `repr()` on `signal['value']` preserves quotes so the LLM copies the string verbatim.
8. `get_openai_client()` returns a Phase 2A-shared `instructor.AsyncInstructor` wrapping `langfuse.openai.AsyncOpenAI`. Self-hosted Langfuse is enforced via the `_is_langfuse_cloud_host()` check inside the factory.
9. `client.chat.completions.create(...)` is called with `response_model=StageQuestionBankOutput`, `model=ai_config.question_bank_model`, `reasoning_effort=ai_config.question_bank_effort`, `max_retries=1`, and an explicit `name="question_bank_generate_call1"` for Langfuse. `AIConfig` reads the model id and effort from `settings.openai_question_bank_model` / `settings.openai_question_bank_effort`, so swapping models is a `.env` change — not a code change.
10. Log `question_bank.llm_call.start` before the call and `question_bank.llm_call.complete` after, with `duration_sec`, `question_count`, and `coverage_notes_preview` (first 100 chars). Errors log `question_bank.llm_call.failed` with `error_type`, truncated `error_message`, and `exc_info=True` before re-raising.
11. Run `validate_llm_output_against_snapshot(db, snapshot=snapshot, allowed_types=allowed_types, questions=result.questions)` — post-LLM validation pass. Section 4 walks this in full.
12. **Persist coverage_notes**: `bank.coverage_notes = result.coverage_notes`. This is the 0007-era change — the spec wanted this to live only in the Langfuse trace, but the shipped behavior writes it on the bank row so later audit surfaces can read it directly.
13. `write_generated_questions(db, bank=bank, questions=validated, source="ai_generated")` — deletes all existing `ai_generated` and `ai_regenerated` rows for the bank (recruiter-sourced questions are preserved), inserts the new ones with their positions offset past any surviving recruiter rows, flushes, then re-packs positions to 0..N-1. The re-pack step matters because the offset-insert strategy leaves holes if a bank had, say, a recruiter question at position 3.
14. `transition_to_reviewing_after_generation(bank, user_id=started_by)` flips status to `reviewing`, stamps `generated_at`, `generated_by`, and `updated_at`.
15. On any exception anywhere above, `transition_to_failed(bank, error=str(exc)[:500])` flips status to `failed` with the error truncated to 500 chars. Then re-raise. The outer actor's `except` then commits only this status-flip row (per commit `1a0b847`).

### Pipeline-wide generation — `POST /api/jobs/{job_id}/pipeline/questions/generate-all`

Router side (`generate_all_questions`):

1. `require_pipeline_access(db, job_id, user, 'manage')` loads the instance + job and walks ancestry.
2. Hard 409 if **any** bank in this pipeline is already `generating` (one query: `WHERE job_posting_id = :job_id AND status = 'generating'`). The `generating` guard is the only way a half-running pipeline actor can block a fresh one.
3. **Unlike the per-stage path, `generate-all` does not pre-transition any banks.** All status transitions happen inside the actor's loop, so there is nothing to roll back if the `.send()` fails. `_safe_dispatch_generate_pipeline` only logs and raises 503.
4. Capture `instance_id`, `tenant_id` into locals before commit. `await db.commit()`. Dispatch. Return `202 { bank_id: null, status: 'generating' }`.

Actor (`generate_question_bank_pipeline`, `max_retries=0`, `time_limit=1_800_000` ms = 30 minutes):

1. `get_bypass_session` + `SET LOCAL app.current_tenant`.
2. Load the instance, job, and all stages in position order.
3. For each stage, in order:
   - `ensure_bank_exists(db, stage=stage, job=job)` — creates the bank row if the stage doesn't have one yet.
   - `transition_to_generating(bank)`. If this raises (e.g. the bank was already in `generating` via an earlier single-stage trigger), log `question_bank.skip_busy_stage` and `continue` to the next stage.
   - Load the pinned snapshot.
   - Call `_generate_one_bank(...)`. On success, increment `succeeded`, `flush`, continue.
   - On exception, log `question_bank.pipeline_stage_failed`, increment `failed`, `flush`, and `continue`. `_generate_one_bank` has already transitioned the bank to `failed` via its inner except branch.
4. After the loop, `log_event` a `question_bank.pipeline_generation_complete` audit row with `{succeeded, failed, total}`, then `await db.commit()`.

`max_retries=0` on the pipeline actor is deliberate: retrying a 30-minute actor is expensive and risks double-generation. Transient failures are absorbed per-stage and the recruiter retries the specific failed stages through the per-stage endpoint.

**Sequential is load-bearing.** The anti-lie invariant (common prompt, principle #1) requires that stage N sees stages 1..N-1's questions in its prompt input. Parallel execution would break coherence — two stages generating simultaneously would both receive `prior_stages_questions` missing the other's output, defeating the re-probe-at-greater-depth rule.

### Single-question regeneration — `POST .../questions/{question_id}/regenerate`

Router side:

1. `require_question_access(...)` loads `(question, bank, stage, job)` and walks ancestry.
2. Capture locals before commit. `await db.commit()` (no pre-transition — the bank stays in whatever state it was).
3. `_safe_dispatch_regenerate_question(...)` enqueues `regenerate_question.send(...)`.
4. Return `202 { bank_id, status: 'generating' }`.

Actor (`regenerate_question`, `max_retries=2`):

1. `get_bypass_session` + `SET LOCAL app.current_tenant`.
2. Load question, bank, stage, instance (unused, kept as `_instance`), job, snapshot.
3. Call `_regenerate_one_question(...)` (also `@observe`-decorated — Section 8) which:
   - Stamps Langfuse trace metadata (with `question_id` added to the tags).
   - Loads the sibling questions via `get_bank_questions` minus the one being replaced.
   - Builds a focused user message: `# JOB CONTEXT` → `# SIGNALS` → `# CURRENT QUESTION BEING REPLACED` → `# TARGET SIGNALS (probe these)` → `# OTHER QUESTIONS IN THIS STAGE'S BANK — DO NOT DUPLICATE` → `# STAGE METADATA` → closing instruction.
   - `load_pair('question_bank_common', 'question_bank_regenerate_one')`.
   - Calls the LLM with `response_model=SingleQuestionOutput` and `name="question_bank_regenerate_call1"`.
   - Runs `validate_llm_output_against_snapshot` on the single-item list.
   - `replace_question_in_place(db, question=question, new_data=result.question)` overwrites every field in place, keeping the same UUID, flipping `source='ai_regenerated'` and `edited_by_recruiter=False`.
   - `auto_revert_on_edit(bank)` flips `confirmed → reviewing` if needed.
4. `log_event` a `question_bank.question_regenerated` audit row. `await db.commit()`.

`replace_signal_values` in the request body optionally retargets the regeneration to a different set of signals (in-place swap; otherwise the new question probes the same signals as the old one).

---

## 4. Adaptive Coverage and Mandatory Demotion

The spec's original framing was "cap generation to the session time budget." The shipped behavior is the opposite: generate the full menu and let the session bot skip what it cannot fit. The only hard budget is **mandatory question minutes** ≤ `stage.duration_minutes`. Two pieces of logic keep that invariant:

### Mandatory demotion (post-LLM, `service.validate_llm_output_against_snapshot`)

Called immediately after the LLM returns and before any DB writes. Takes the pinned snapshot, the stage's allowed signal types, and the LLM's `list[GeneratedQuestion]`. Two passes:

**Pass 1 — signal existence and type filter.**

1. Build `snapshot_by_value = {s['value']: s for s in snapshot.signals}`.
2. For every question, for every `signal_value` in `question.signal_values`:
   - If the value is not in `snapshot_by_value`, raise `SignalValueNotInSnapshotError` (the LLM hallucinated a signal). The outer actor catches this, transitions the bank to `failed`, and surfaces the error via the `BankResponse.generation_error` column.
   - If the signal's `type` is not in `allowed_types` (the stage's `signal_filter.include_types`), raise `SignalTypeNotAllowedError`. Same failure path. The LLM should never reach this branch because the prompt explicitly calls out the type filter, but the server-side check is the belt to the prompt's braces.

**Pass 2 — mandatory auto-correction in position order.**

1. Build `knockout_values = {s['value'] for s in snapshot.signals if s.get('knockout', False)}`.
2. Initialize `knockouts_covered: set[str] = set()`.
3. Sort the questions by `position` and walk them in order:
   - `knockouts_in_q = set(q.signal_values) & knockout_values`.
   - If `knockouts_in_q` is empty, the question probes no knockouts. Leave `is_mandatory` as the LLM sent it (trust the LLM for non-knockout mandatoriness).
   - Otherwise, compute `unclaimed = knockouts_in_q - knockouts_covered`. If any knockout in this question is still unclaimed, this is the **earliest** question probing those knockouts and must be mandatory: if `is_mandatory=False` from the LLM, flip to `True` and log `question_bank.upgraded_to_mandatory` with reason `"earliest_knockout_question_must_be_mandatory"`. Mark those knockouts as covered.
   - If all knockouts in this question are **already covered** by earlier mandatory questions, this is a depth re-probe (same signal, different angle). If `is_mandatory=True` from the LLM, flip to `False` and log `question_bank.demoted_to_optional` with reason `"duplicate_knockout_coverage"`. Leave `is_mandatory=False` if it was already false.
4. Return the (possibly-mutated) list.

The invariant after this pass: **every knockout signal has exactly one mandatory question — the earliest in position order that probes it.** Subsequent probes of the same knockout are optional depth probes the session bot can skip under time pressure.

The phone-screen prompt file also carries a hard override: *"phone screen uses EXACTLY ONE question per knockout. The AI interview (next stage) handles depth re-probing."* The post-validation logic doesn't know about this stage-type rule — it only enforces the "earliest one is mandatory, later ones are optional" invariant, which is compatible with both styles.

### Mandatory budget check at confirmation — `service.validate_mandatory_fits_session`

Called from `confirm_bank` (alongside `validate_knockout_coverage`) before the `reviewing → confirmed` transition:

1. Load the stage row.
2. Load every question in the bank.
3. `mandatory_total = float(sum(q.estimated_minutes for q in questions if q.is_mandatory))`.
4. If `mandatory_total > stage.duration_minutes`, raise `MandatoryOverrunError(bank_id, mandatory_minutes, stage_minutes)`.

`MandatoryOverrunError` is mapped to HTTP 409 by the global handler in `app/main.py`. The error message directly tells the recruiter their options: *"The session bot cannot skip mandatory questions — either shorten mandatory questions, demote some to optional, or increase the stage duration."*

The common prompt (`question_bank_common.txt`, principle #3) instructs the LLM to target mandatory total ≤ 70–80% of `duration_minutes` (i.e. 20–30% headroom). The server-side check is absolute: it allows 100% fill but blocks 100%+. If the LLM was aggressive and hit 100.1%, confirmation fails and the recruiter must edit.

### Knockout coverage check — `service.validate_knockout_coverage`

Also called from `confirm_bank`. Walks the pinned snapshot's signals, filters to those with `knockout=True` AND `type IN stage.signal_filter.include_types`, and raises `KnockoutUnprobedError(signal_value, bank_id)` for any knockout whose value is **not** covered by at least one mandatory question in the bank. The type filter is critical: a behavioral knockout doesn't need to be covered by a mandatory question in an `ai_interview` stage whose filter excludes `behavioral` — that knockout is explicitly off-limits for this stage.

### Bundling discipline

The spec's "bundling discipline" framing — related follow-up questions grouped together rather than flat — is implemented entirely in the prompt, not in post-validation. Two mechanisms:

1. **Positional ordering in the prompt** (`question_bank_common.txt`, principle #3): the LLM is told to order output as positions 0..M-1 for mandatory knockout verification, M..K for mandatory non-knockout, K+1..P for optional knockout depth re-probes, P+1..W for optional weight-2 probes, W+1..end for weight-1 bonus. A candidate cut at position 3 should still have had their knockouts verified — the ordering **is** the bundling.
2. **Follow-ups as a schema field**, not separate questions. Each `GeneratedQuestion` carries `follow_ups: list[str]` with 0–3 short probes the conductor can fire based on the candidate's response. Related sub-topics are either folded into a single question's `follow_ups` or (if genuinely independent) split into sibling questions — the common prompt's banned-patterns rule #10 explicitly forbids "questions that bundle more than 2 orthogonal sub-topics in the text."

There is no post-processing step that re-sorts or re-bundles. The server trusts the prompt for layout and trusts `validate_llm_output_against_snapshot` for correctness.

---

## 5. Read-Idempotent `list_banks` GET

`GET /api/jobs/{job_id}/pipeline/questions` returns a `BanksOverviewResponse { banks: [BankResponse | PlaceholderBankResponse] }` — one entry per pipeline stage, in stage position order. The sidebar component polls this via TanStack Query whenever an SSE event arrives (see Section 9) and again on window-focus through the default TanStack staleness semantics.

**Before commit `23e78bc`** this handler called `ensure_bank_exists(db, stage, job)` in a loop. Every poll against an 8-stage pipeline with no banks wrote 8 `draft` rows to `stage_question_banks`. At ~2 polls per second during an active SSE stream, that's a sustained write stream for what is nominally a read endpoint — plus a cascade-delete at the end of each session when the polling stops. Violation of GET semantics, slow leak into `stage_question_banks`, and a nightmare for anyone trying to reason about when a bank row was actually created.

**The fix (commit `23e78bc`):**

1. The endpoint loads every stage for the instance in position order (one query).
2. Calls `get_banks_for_pipeline(db, instance)` which returns `(bank, question_count, total_minutes, is_stale)` tuples for only the stages that **already have a bank row** (four queries total — see Section 6).
3. Builds a `banks_by_stage: dict[UUID, tuple[...]]` lookup.
4. Walks stages in order. For each stage:
   - If a bank tuple exists, append a full `BankResponse` via `_bank_to_response`.
   - Otherwise, append `PlaceholderBankResponse(stage_id=stage.id, status='not_generated', question_count=0, total_minutes=0.0)`.
5. Return the combined list. **Zero writes.**

`PlaceholderBankResponse` is a new shape in `schemas.py`:

```python
class PlaceholderBankResponse(BaseModel):
    stage_id: UUID
    status: Literal["not_generated"] = "not_generated"
    question_count: int = 0
    total_minutes: float = 0.0
```

The `BanksOverviewResponse.banks` field is a `list[BankResponse | PlaceholderBankResponse]` union. The frontend keys off `status == 'not_generated'` to render the "Generate questions" call-to-action instead of a bank card — no client-side behavior change beyond the new discriminant.

**Writes still happen eagerly** on other paths: the per-stage generation endpoint calls `ensure_bank_exists` (step 2 of Section 3's flow), the pipeline-wide actor calls it per stage, and the `GET /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions` detail endpoint still calls it (so opening a stage's main pane creates the draft row if missing — the detail endpoint is called on demand, not polled). Those are the *only* paths that create bank rows. The list endpoint is strictly read-only.

A regression test in `test_question_banks_router.py` (added in the same commit) builds a 3-stage pipeline with zero banks, hits the list endpoint twice, and asserts `SELECT COUNT(*) FROM stage_question_banks` stays at zero.

---

## 6. Bulk Load — `get_banks_for_pipeline`

`service.get_banks_for_pipeline(db, instance)` returns `list[tuple[StageQuestionBank, int, float, bool]]` — one tuple per stage that has a bank, carrying `(bank, question_count, total_minutes, is_stale)`. Called by the list endpoint and (indirectly) by anything that needs a whole-pipeline overview.

**Query count: exactly 4, regardless of pipeline size.**

1. Load all stages for the instance in position order.
2. Bulk-load all banks for those stages in one `WHERE stage_id IN (...)` query. Build `banks_by_stage: dict[UUID, StageQuestionBank]`.
3. Bulk-load all questions for those banks in one `WHERE bank_id IN (...)` query. Build `questions_by_bank: dict[UUID, list[StageQuestion]]`.
4. Load the job's latest confirmed signal snapshot once (single `get_latest_confirmed_snapshot(db, instance.job_posting_id)` call).

Then assemble the output tuples in Python: for each stage, look up its bank (skip if absent), compute `question_count = len(questions)` and `total_minutes = float(sum(q.estimated_minutes for q in questions))`, and compute `is_stale = latest_id is not None and bank.signal_snapshot_id != latest_id`.

Before this optimization the shape was 1 + 2N: one query for the stages, then two extra queries per stage (one for the bank, one for its questions). For an 8-stage pipeline that's 17 queries per page load. Under SSE-driven polling the cost compounds quickly: 17 queries × 2 polls per second during a live generation is a measurable pool tax.

The comment in `service.py` calls out the old shape explicitly: *"Previously this was a 1 + 2N loop that fired two extra SELECTs per stage on every pipeline overview load — painful under concurrency for a pipeline with 8 stages (1 + 16 = 17 queries per page load). (B7 fix.)"*

**Staleness computation** is part of the same bulk load: the latest confirmed snapshot is resolved once (step 4) and every bank's `is_stale` is computed against the cached `latest_id`. For the single-bank detail endpoint, `compute_is_stale(db, bank)` runs the same snapshot query but scoped to one bank — the bulk path caches it to avoid N lookups.

The per-request single-query `get_latest_confirmed_snapshot` has no session-level cache, but `get_banks_for_pipeline` calls it at most once per list request and the detail endpoint calls it at most once per detail request. Good enough.

---

## 7. State Machine

`state_machine.py` owns a `BankStatus = Literal["draft", "generating", "reviewing", "confirmed", "failed"]` type and a `LEGAL: dict[BankStatus, set[BankStatus]]` transition map. `schemas.py` imports `BankStatus` so the API response shapes stay in sync with the DB-backed invariant — both layers always agree on what the five states are (the "B8 consolidation" note in the source calls out that they used to be redefined per-layer).

### Transitions

| From | To | Helper | Notes |
|---|---|---|---|
| `draft` | `generating` | `transition_to_generating` | Explicit recruiter trigger; raises `BankAlreadyGeneratingError` if bank is already `generating`, `IllegalTransitionError` for any other illegal source |
| `draft` | `reviewing` | `auto_revert_on_edit` (implicit) | First recruiter-created question on a bank that has never been generated — see `create_recruiter_question` |
| `draft` | `failed` | `transition_to_failed` | Defensive — unreachable via the current actor flow (actors always go through `generating` first), but the LEGAL map permits it |
| `generating` | `reviewing` | `transition_to_reviewing_after_generation(user_id=...)` | On LLM success; stamps `generated_at`, `generated_by`, `updated_at`. Guards against wrong source state with an explicit `RuntimeError` (not `assert` — asserts are stripped under `python -O`) |
| `generating` | `failed` | `transition_to_failed(error=str)` | On LLM / validation error; stamps `generation_error` (truncated to 500 chars) |
| `reviewing` | `generating` | `transition_to_generating` | "Regenerate all" on a reviewing bank |
| `reviewing` | `confirmed` | `transition_to_confirmed(user_id=...)` | Explicit confirm; raises `BankNotInReviewingError` if source is anything else |
| `confirmed` | `generating` | `transition_to_generating` | "Regenerate all" on a confirmed bank |
| `confirmed` | `reviewing` | `auto_revert_on_edit` (implicit) | Triggered by any recruiter edit/create/delete/reorder/regen-completion that mutates the bank |
| `failed` | `generating` | `transition_to_generating` | Retry after failure; direct, no intermediate `draft` hop. Clears `generation_error` |

### Error classes (all mapped to HTTP responses in `app/main.py`)

| Error | HTTP | Raised when |
|---|---|---|
| `BankAlreadyGeneratingError` | 409 | Double-trigger: `transition_to_generating` called while bank is already in `generating` |
| `IllegalTransitionError` | 409 (body includes `from_state`, `to_state`) | Any other illegal source state for a transition |
| `BankNotInReviewingError` | 409 | `confirm_bank` called on a non-`reviewing` bank |
| `KnockoutUnprobedError` | 409 (body includes `signal_value`) | `confirm_bank` — a knockout signal (filtered by stage type) has no mandatory question |
| `MandatoryOverrunError` | 409 | `confirm_bank` — mandatory minutes total exceeds `stage.duration_minutes` |
| `SignalValueNotInSnapshotError` | 400 | Post-LLM validation: question references a signal value that doesn't exist in the pinned snapshot. Actor catches and transitions bank to `failed`. For create/update mutations, propagates as 400 from the router. |
| `SignalTypeNotAllowedError` | 400 | Post-LLM validation: question references a signal whose type is not in the stage's `include_types`. Same routing as above. |
| `ReorderMismatchError` | 400 | `reorder_questions` — the set of `question_ids` in the body isn't exactly the bank's current question set |
| `ReorderDuplicateError` | 400 | `reorder_questions` — the list contains the same ID twice. Checked **before** the set-equality check so a body like `[A, A, B]` is caught correctly (it would pass the `{A, B}` set comparison). |

All eight are registered as FastAPI exception handlers in `app/main.py::create_app()` and return structured `{detail: str, ...}` bodies. The two reorder errors are the only ones mapped to 400 (they are input validation); everything else is 409 (state / constraint conflict).

### `auto_revert_on_edit`

Called by every recruiter mutation (`create_recruiter_question`, `update_question`, `delete_question`, `reorder_questions`) and by the regenerate-one path (`_regenerate_one_question`). Two side effects:

- `confirmed` → `reviewing`: also clears `confirmed_at` and `confirmed_by`. Returns `True`.
- `draft` → `reviewing`: stamps `updated_at`. Returns `True`.
- Any other state: no change. Returns `False`.

Clearing `confirmed_at` / `confirmed_by` means the column pair always reflects the **current** confirmation state. Historical audit of past confirmations lives in `audit_log` via `log_event`, which every mutation also writes.

`UpdateQuestionBody` has a `@model_validator(mode="after")` that rejects an empty `{}` PATCH body. Without this guard, a sloppy client PATCH of `{}` would pass Pydantic (all fields default to `None`), hit `update_question`, and call `auto_revert_on_edit` — silently flipping a confirmed bank back to reviewing with zero field changes. The validator raises `ValueError("At least one field must be provided to update")`, which FastAPI converts to 422.

### Reorder route order

`reorder_questions_endpoint` is declared **before** `patch_question` in `router.py`. The comment inline calls out why:

> NOTE: `reorder_questions_endpoint` is intentionally declared BEFORE `patch_question` so FastAPI registers the literal-path route first. Otherwise a PATCH to `/reorder` would try to parse "reorder" as a UUID for `{question_id}` and 422.

---

## 8. Langfuse Wiring

Phase 2C.2 inherits the Phase 2A infrastructure: `get_openai_client()` returns an `instructor.AsyncInstructor` wrapping `langfuse.openai.AsyncOpenAI`, which auto-captures every `chat.completions.create` call as a Langfuse generation span. The factory's `_is_langfuse_cloud_host()` check rejects managed Langfuse cloud — candidate evaluation data must never flow to a third-party sub-processor.

The question-bank actors add three layers on top of the Phase 2A wiring:

1. **`@observe(name=...)` decorators on the inner helpers.** `_generate_one_bank` is decorated `@observe(name="question_bank_generate")` and `_regenerate_one_question` is decorated `@observe(name="question_bank_regenerate")`. The decorator wraps just the observable path (LLM call + validation + DB write) — *not* the Dramatiq actor entry point, session bootstrap, or audit-log writes. This mirrors the `jd/actors.py::_run_extraction` split and keeps the trace scope focused on the LLM interaction.
2. **`langfuse_context.update_current_trace(...)` at the top of each inner helper.** Attaches `session_id=str(bank.id)`, `tags=["question_bank_generate", f"stage_type:{stage.stage_type}"]`, and a metadata dict containing `bank_id`, `stage_id`, `stage_type`, `tenant_id`, `job_posting_id`, `model`, `reasoning_effort`, and `prompt_version`. `session_id=bank.id` is the load-bearing one: all retries of the same bank (Dramatiq retries + recruiter-initiated regenerates) group under one Langfuse session id, so searching a dashboard by bank ID shows the full history.
3. **Per-call `metadata` on `chat.completions.create(...)`.** Each LLM call also passes a redundant `metadata={...}` with bank/stage/tenant/job ids and `prompt_version`. This is nested at the generation-span level (one level below the trace), so filtering Langfuse by, say, `metadata.bank_id = X` surfaces the LLM generation spans directly even when the trace-level session_id is already set. The per-call `name="question_bank_generate_call1"` / `"question_bank_regenerate_call1"` is the span label.

The `@observe` decorators import from `langfuse.decorators` — the standard wiring for Langfuse's async Python SDK. The commit comment that landed this (inline as "B13 wiring" in the source) notes it matches the `jd/actors.py` pattern.

`AIConfig.question_bank_model` and `AIConfig.question_bank_effort` are env-driven via `settings.openai_question_bank_model` / `settings.openai_question_bank_effort`. Swapping to a different OpenAI model for question generation is a `.env` change and a worker restart — no code change.

---

## 9. SSE Progress Stream

`GET /api/jobs/{job_id}/pipeline/questions/status-stream` is the question-bank SSE endpoint. Implementation lives in `sse.py::stream_question_bank_status` and `router.py::questions_status_stream`.

### Server behavior

The router handler is a thin wrapper:

```python
@router.get("/jobs/{job_id}/pipeline/questions/status-stream")
async def questions_status_stream(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> StreamingResponse:
    _instance, job = await require_pipeline_access(db, job_id, user, "view")
    return StreamingResponse(
        stream_question_bank_status(
            request=request,
            tenant_id=job.tenant_id,
            job_id=job_id,
        ),
        media_type="text/event-stream",
    )
```

`request` is injected specifically so the generator can call `await request.is_disconnected()` on every poll iteration. Without that check, an orphaned browser tab holds the stream open until the 10-minute idle timeout, pinning DB connections per poll iteration. 15–20 orphaned streams exhaust the pool. Matches the Phase 2A pattern in `jd/sse.py`.

The async generator (`stream_question_bank_status`) runs an infinite loop with:

- **Poll interval:** 500 ms (`POLL_INTERVAL_SEC`).
- **Idle timeout:** 600 s (`IDLE_TIMEOUT_SEC`). Closes the stream after 10 minutes without any state change.
- **Dedup:** `last_snapshots: dict[UUID, dict]` keyed by `bank_id`. Each bank's snapshot is `{status, question_count, total_minutes, error}`. Events fire only when the current state differs from the cached state.
- **Termination:** stream closes when all banks have reached a terminal status (`reviewing`, `confirmed`, or `failed`), or on `should_terminate` (no pipeline instance), or on client disconnect, or on idle timeout.

Every iteration:

1. Call `request.is_disconnected()`; if true, `return` (closes the stream, releases the DB connection).
2. Open `async with get_tenant_session(safe_tenant_id) as db:` — **not** a raw session. `get_tenant_session` wraps the `SET LOCAL ROLE nexus_app` + `SET LOCAL app.current_tenant = '<uuid>'` dance in one explicit transaction. Opening the session raw via `async_session_factory()` would skip the role switch and silently bypass RLS on the streaming path (a Batch F lesson).
3. Inside the session, load the pipeline instance, every stage, and every bank for those stages, plus the questions for each bank. Compute `question_count` and `total_minutes` per bank.
4. For each bank, build `current_state` and compare against `last_snapshots[bank_id]`. On change, append a `bank.status_changed` event and update the cache.
5. Exit the `with` block. The session is released and the DB connection goes back to the pool **before** yielding. Holding the connection across `yield` would pin a pool slot while the client processes events.
6. `for ev in events_to_emit: yield ev`.
7. Termination checks: if `should_terminate`, return. If `any_change`, reset `idle_since`. If idle > 10 minutes, return. If all banks are terminal and every bank has a cached snapshot (`len(last_snapshots) == num_stages`), emit a final `pipeline.generation_complete` event with `{succeeded, failed, total}` and return.
8. `await asyncio.sleep(POLL_INTERVAL_SEC)`.

### Event shapes

```
event: bank.status_changed
data: {"stage_id": "<uuid>", "status": "generating", "question_count": 0, "total_minutes": 0.0}

event: bank.status_changed
data: {"stage_id": "<uuid>", "status": "reviewing", "question_count": 8, "total_minutes": 42.5}

event: bank.status_changed
data: {"stage_id": "<uuid>", "status": "failed", "question_count": 0, "total_minutes": 0.0, "error": "..."}

event: pipeline.generation_complete
data: {"succeeded": 2, "failed": 1, "total": 3}

event: error
data: {"error": "No pipeline for this job"}
```

For terminal reporting, `succeeded` counts banks in `confirmed` OR `reviewing`, and `failed` counts banks in `failed`. A stream open against a pipeline with no `generating` banks but with all in `draft` will never reach terminal — `draft` is not terminal — and will close via idle timeout instead.

### Frontend subscription — `useQuestionsStatusStream`

`frontend/app/lib/hooks/use-questions-status-stream.ts`. Uses `@microsoft/fetch-event-source` for EventSource-with-custom-headers (the standard `EventSource` API can't set an Authorization header).

Signature:

```typescript
export function useQuestionsStatusStream(
  jobId: string,
  selectedStageId: string | null,
)
```

On every SSE message:

- **All events** invalidate `['banks', jobId]` (the sidebar overview query key).
- **`bank.status_changed` and `bank.question_updated`** additionally invalidate `['bank', jobId, selectedStageId]` **iff** a stage is currently selected — so the main pane re-fetches the currently-visible bank detail.

The handler reads `selectedStageId` from a ref, not from closure-captured state. That is the fix from commit `2dfa766`:

```typescript
const selectedStageIdRef = useRef(selectedStageId)
useEffect(() => {
  selectedStageIdRef.current = selectedStageId
})

useEffect(() => {
  if (!jobId) return
  // ... fetchEventSource with onmessage that reads selectedStageIdRef.current ...
}, [jobId, queryClient])
```

Without the ref mirror, `selectedStageId` would live in the `useEffect` dep array. Every time the recruiter clicked a different stage, the effect would tear down the SSE connection and reopen it, causing a brief gap in live updates. With the ref, the SSE connection's lifetime is bound purely to `jobId` — clicking around the sidebar doesn't touch it.

The hook uses the built-in auto-retry of `fetch-event-source` for transient errors. A more elaborate reconnect-with-fresh-token flow (like `useJobStatusStream`'s `MAX_TOTAL_RETRIES` cap) is explicitly deferred per an inline comment: *"can be added if token expiry during long-lived streams becomes a problem in practice."*

### UI handling of events

| Event | UI action |
|---|---|
| `bank.status_changed` (any stage) | Sidebar badge re-renders via `useBanksOverview` refetch |
| `bank.status_changed` (selected stage) | Main pane's `BankHeader` + `QuestionList` re-render via `useBankWithQuestions` refetch |
| `bank.question_updated` (selected stage) | Same as above (Section 10 API Reference — this event is emitted by the spec but not yet produced by the server; handler is future-proofed) |
| `pipeline.generation_complete` | Sidebar refetches once more; stream closes |
| `error` | Logged to console via `onerror` — stream closes via the server-side `should_terminate` |

The `BankStatusBadge` maps each status to a color + icon: draft gray, generating blue-with-spinner, reviewing amber-with-clock, confirmed emerald-with-lock, failed red-with-alert. The "EDITED" / "CUSTOM" / "REGENERATED" / "MANDATORY" per-question badges live on `QuestionCard` and are driven by `question.source` + `question.edited_by_recruiter` + `question.is_mandatory`.

---

## 10. API Reference

All 11 endpoints are registered under `/api` with the `question_bank` router tag and mounted via `app/main.py`. Auth is Bearer Supabase JWT. Every handler depends on `get_tenant_db` (for tenant-scoped DB access) and `get_current_user_roles` (for `UserContext`). Permission walks run through `require_bank_access_by_stage` / `require_question_access` / `require_pipeline_access` / `require_bank_access`. Super admin short-circuits every permission check.

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `GET` | `/api/jobs/{job_id}/pipeline/questions` | `jobs.view` in job's ancestry | **Read-idempotent** sidebar list — one entry per stage, `BankResponse` or `PlaceholderBankResponse` |
| `GET` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions` | `jobs.view` in job's ancestry | Full bank detail for the main pane. **Calls `ensure_bank_exists`** — creates a draft row on first open |
| `POST` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/generate` | `jobs.manage` in job's ancestry | Trigger single-stage generation. 202 with `bank_id`. 409 via `BankAlreadyGeneratingError` if bank is already generating |
| `POST` | `/api/jobs/{job_id}/pipeline/questions/generate-all` | `jobs.manage` in job's ancestry | Trigger sequential pipeline generation. 202 with `bank_id: null`. 409 if any bank in this pipeline is currently `generating` |
| `POST` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}/regenerate` | `jobs.manage` | Single-question regen; body `{replace_signal_values?: string[]}`. 202 with `bank_id` |
| `POST` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions` | `jobs.manage` | Create a `source='recruiter'` question. Body validated against pinned snapshot + stage type filter |
| `PATCH` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/reorder` | `jobs.manage` | Reorder questions — body `{question_ids: [...]}`. 400 on `ReorderMismatchError` / `ReorderDuplicateError` |
| `PATCH` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}` | `jobs.manage` | Partial question update; rejects empty `{}` body at schema layer |
| `DELETE` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}` | `jobs.manage` | Delete + re-pack; 204 no-content |
| `POST` | `/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/confirm` | `jobs.manage` | `reviewing → confirmed`. Runs `validate_knockout_coverage` + `validate_mandatory_fits_session`. 409 on any check failure |
| `GET` | `/api/jobs/{job_id}/pipeline/questions/status-stream` | `jobs.view` | SSE stream (see Section 9) |

### Error shapes

| Error class | HTTP | Body | Raised by |
|---|---|---|---|
| `BankAlreadyGeneratingError` | 409 | `{"detail": "Bank <uuid> is already in 'generating' state"}` | `transition_to_generating` — generate endpoints |
| `IllegalTransitionError` | 409 | `{"detail": "...", "from_state": "...", "to_state": "..."}` | `transition_to_generating` fallback |
| `BankNotInReviewingError` | 409 | `{"detail": "Cannot confirm bank <uuid>: current status is '...', expected 'reviewing'"}` | `confirm_bank` |
| `KnockoutUnprobedError` | 409 | `{"detail": "Cannot confirm: knockout signal '<value>' has no mandatory question", "signal_value": "<value>"}` | `validate_knockout_coverage` |
| `MandatoryOverrunError` | 409 | `{"detail": "Mandatory question time (... min) exceeds the stage's session duration (... min). ..."}` | `validate_mandatory_fits_session` |
| `SignalValueNotInSnapshotError` | 400 | `{"detail": "Signal value '<value>' does not exist in snapshot <uuid>"}` | Post-LLM validation and recruiter create/update |
| `SignalTypeNotAllowedError` | 400 | `{"detail": "Signal '<value>' has type '<type>' which is not in this stage's allowed types [...]"}` | Post-LLM validation and recruiter create/update |
| `ReorderMismatchError` | 400 | `{"detail": "Reorder list for bank <uuid> must contain exactly the existing question IDs"}` | `reorder_questions` |
| `ReorderDuplicateError` | 400 | `{"detail": "Reorder list for bank <uuid> contains duplicate question IDs"}` | `reorder_questions` |
| Dispatch failures | 503 | `{"detail": "Failed to enqueue ... job — please retry. ..."}` | `_safe_dispatch_*` wrappers |
| Missing bank / stage / question / pipeline | 404 | `{"detail": "..."}` | `require_*_access` helpers |
| Missing permission | 403 | `{"detail": "You do not have permission to ... questions for this job"}` | `require_*_access` ancestry walk |

### `GET /api/jobs/{job_id}/pipeline/questions` — response

```json
{
  "banks": [
    {
      "id": "11111111-...",
      "stage_id": "22222222-...",
      "job_posting_id": "33333333-...",
      "signal_snapshot_id": "44444444-...",
      "status": "reviewing",
      "prompt_version": "v1",
      "generation_error": null,
      "coverage_notes": "Allocated 3 mandatory to knockouts, 2 weight=2 probes, ...",
      "generated_at": "2026-04-14T13:22:05.123Z",
      "generated_by": "55555555-...",
      "confirmed_at": null,
      "confirmed_by": null,
      "question_count": 8,
      "total_minutes": 42.5,
      "is_stale": false,
      "created_at": "...",
      "updated_at": "..."
    },
    {
      "stage_id": "66666666-...",
      "status": "not_generated",
      "question_count": 0,
      "total_minutes": 0.0
    }
  ]
}
```

The second entry is a `PlaceholderBankResponse` — no bank row exists for that stage yet. The frontend discriminates on `status === 'not_generated'`.

### `POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions` — body

```json
{
  "text": "How would you design a multi-region failover for a stateful service?",
  "signal_values": ["Kubernetes production deployment"],
  "estimated_minutes": 7.5,
  "is_mandatory": false,
  "follow_ups": ["What's your RTO/RPO target?"],
  "positive_evidence": [
    "Names specific cluster topology",
    "Describes quorum loss handling",
    "Mentions failover drill cadence"
  ],
  "red_flags": [
    "Handwaves on state reconciliation",
    "Cannot describe quorum loss"
  ],
  "rubric": {
    "excellent": "...",
    "meets_bar": "...",
    "below_bar": "..."
  },
  "evaluation_hint": "Listen for cluster-specific operational signals",
  "position": null
}
```

`position: null` appends at the end of the bank. An integer position shifts existing questions down (via `create_recruiter_question`'s position handling). `source` is forced to `"recruiter"` server-side and not accepted in the body. All `signal_values` are validated against the pinned snapshot AND the stage's `include_types` before the row is inserted.

### `POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/confirm`

Empty body (`{}`). Runs `validate_knockout_coverage` then `validate_mandatory_fits_session` before `transition_to_confirmed(bank, user_id=user.user.id)`. On success: `200 BankResponse` with `status='confirmed'`, `confirmed_at` and `confirmed_by` set. On validation failure: 409 with the specific error (see the table above). Audit log row `question_bank.bank_confirmed` is written inside `confirm_bank` after the transition.

---

## 11. Frontend Architecture

### Surface shape — tab inside the pipeline editor, not a sibling route

The design spec described a dedicated `/jobs/[jobId]/questions` page with its own sidebar + main pane. What shipped is **one merged page at `/jobs/[jobId]/pipeline`** where `UnifiedPipelineView` hosts the `StageInspectorPanel` with two tabs: "Questions" (Phase 2C.2) and "Configuration" (Phase 2C.1). Selecting a stage in the flow column on the left drives the inspector panel on the right; the "Questions" tab renders `QuestionsMainPane` for the selected `(jobId, stageId)`.

The `/jobs/[jobId]/questions` route still exists but is a **single-file redirect**: it reads `useBanksOverview(jobId)`, picks the first bank's `stage_id`, and `router.replace`s to `/jobs/{id}/pipeline?stage=${stage_id}`. This preserves any spec-era links without forcing a renamed route.

### `UnifiedPipelineView` — shared owner of selection and SSE

The parent component that owns `selectedStageId`, subscribes to the SSE stream, and mounts both the flow column and the inspector panel. Pipeline-specific behavior is documented in Phase 2C.1 Section 8 (autosave, keyboard shortcuts, dnd-kit wiring); the Phase 2C.2 hooks added are:

- **`const { data: overview } = useBanksOverview(jobId)`** — sidebar data used for the bank status badges on each flow card, and for the "auto-select first un-confirmed bank on mount" effect (refactored in commit `73adb68`; see Phase 2C.1 Section 8 for the refactor detail).
- **`useQuestionsStatusStream(jobId, selectedStageId)`** — keeps the SSE connection open for the whole visit.
- **`StageInspectorPanel`** — receives `activeTab` and `onTabChange`. The "Questions" tab renders `<QuestionsMainPane jobId={jobId} stageId={stageId} />`; the "Configuration" tab renders `StageConfigurationTab` (the 2C.1 stage fields editor). Tab selection is URL-synced via `?tab=questions|config` on `UnifiedPipelineView`.

### `QuestionsMainPane`

The main content pane. Loads the bank detail via `useBankWithQuestions(jobId, stageId)` — note this **detail** query key is `['bank', jobId, stageId]`, distinct from the list key `['banks', jobId]` so invalidating one does not clobber the other. That separation matches the Phase 2C.1 / Batch G query-key discipline rule.

Internal state:

- `addDialogOpen: boolean` — controls `AddCustomQuestionDialog`.
- `confirmDialogOpen: boolean` — controls `ConfirmBankDialog`.

Renders:

- `BankHeader` with `isSaving`/`saveFailed` tied to the generate mutation.
- `QuestionList` driving a single-expanded accordion over `QuestionCard`s.
- Two dialogs, mounted conditionally on open.

### `BankHeader`

Shows `"${N} questions · ${X} min"` + `BankStatusBadge` on the left. On the right: an `aria-live="polite"` save indicator (`Saving…` / `All changes saved` / `Save failed`) tied to the `isSaving`/`saveFailed` props, and a variable button set keyed by `bank.status` and `bank.questions.length`:

- Empty draft: `Generate questions`
- Has questions: `Regenerate all` + `+ Add custom` + (only when `canConfirm = bank.status === 'reviewing'`) `Confirm bank`

Also renders the `is_stale` banner:

> Signals have changed since this bank was generated. Click Regenerate to pick up the latest.

And the `generation_error` red-text message under the title when `bank.status === 'failed'`.

### `BankStatusBadge`

Pure-render component. `STATUS_STYLES` maps each of the 5 statuses to `{bg, text, label}` Tailwind classes. Icons are `Clock` (reviewing), `Lock` (confirmed), `AlertCircle` (failed), `Loader2 animate-spin` (generating), `Check` (draft — yes, counter-intuitive, but matches the spec's visual). The `small` prop scales down padding / icon size for inline display next to each stage in the flow column.

### `QuestionCard`

Collapsed state: position number (`Q1`, `Q2`, …), `MANDATORY` / `CUSTOM` / `REGENERATED` / `EDITED` badges as applicable, `probes: ...` signal list, estimated minutes, one-line question text, and (when collapsed) a muted preview of the `evaluation_hint`.

Right side: 3-dot `MoreVertical` menu with **Regenerate** (`window.confirm` gate + `regenMutation.mutate({})`) and **Delete** (`window.confirm` gate + `deleteMutation.mutate(question.id)`). Below the menu, a chevron that rotates 180° on expand.

Expanded state:

```tsx
<QuestionEditForm
  key={question.id}
  jobId={jobId}
  stageId={stageId}
  question={question}
/>
<QuestionRubricExpanded question={question} />
```

The `key={question.id}` prop is the fix from commit `2d16c2f`. Without it, React keeps the same `QuestionEditForm` instance across a regeneration: the new `question.id` arrives via props, but the `useState` initializers in `QuestionEditForm` (`useState(question.text)`, `useState(question.evaluation_hint)`) only run on mount, so the old local text stays in the form. Then the 800 ms debounced autosave fires with the stale text and overwrites the freshly regenerated question. Adding `key={question.id}` forces a remount on identity change, which re-runs the initializers and clears the stale state.

### `QuestionEditForm` — 800 ms debounced autosave

Small: two `<textarea>`s (`text`, `evaluation_hint`). On every keystroke:

1. Update local state (`setText` / `setHint`).
2. Clear `saveTimerRef.current` if set.
3. `saveTimerRef.current = window.setTimeout(() => updateMutation.mutate(body), 800)`.

Cleanup effect clears the pending timeout on unmount. There is no unmount-flush (unlike `UnifiedPipelineView`'s pipeline autosave) — the in-flight debounced save is simply dropped if the user navigates away mid-keystroke. The 800 ms window is short enough that this is acceptable; the PATCH response refreshes the query on success and the cached value reflects the latest committed edit.

`useUpdateQuestion` (`use-save-question.ts`) invalidates both `['banks', jobId]` and `['bank', jobId, stageId]` on success. It does **not** toast-success — the aria-live save indicator in `BankHeader` carries that signal.

### `AddCustomQuestionDialog`

RHF + Zod form. The user fills in text, signal picker (signals drawn from the pinned snapshot, filtered by `stage.signal_filter.include_types` — server-side validation is the authoritative check), estimated minutes, rubric anchors, evidence items, red flags. On submit, calls `useCreateQuestion` which POSTs and invalidates both query keys. The created question is appended at the end of the list with a `CUSTOM` badge.

### `ConfirmBankDialog`

Pre-confirm coverage summary. Shows `bank.questions.length` total + `bank.total_minutes.toFixed(0)` min + `mandatory_count` mandatory. Renders a short explanation that confirmation locks the bank and that editing any question will auto-revert it to reviewing. `Escape` closes via a `document.addEventListener('keydown', ...)` effect. On confirm, calls `useConfirmBank` which POSTs and toasts success or surfaces the 409 error message via `toast.error`. If the server-side validators fail (uncovered knockout, mandatory overrun), the 409 response body bubbles up as the toast message.

### `useQuestionsStatusStream` — ref-mirrored stage selection

Covered in Section 9. The critical behavior: the SSE connection's effect depends only on `[jobId, queryClient]`, not on `selectedStageId`. `selectedStageId` is mirrored into a ref so the `onmessage` handler reads the latest value without closing and reopening the stream when the recruiter clicks a different stage.

### Query key discipline

`['banks', jobId]` for the list overview, `['bank', jobId, stageId]` for bank detail. Distinct shapes — invalidating one doesn't clobber the other via TanStack's prefix matching. Matches the Phase 2C.1 + Batch G key-shape rule documented in `frontend/app/CLAUDE.md` ("Query key discipline").

### Tests

Two vitest component test files ship with this phase:

- `tests/components/QuestionCard.test.tsx` — render, expand, inline edit triggers PATCH.
- `tests/components/BankStatusBadge.test.tsx` — renders all 5 states with correct colors/icons.

---

## 12. Known Gaps

- **Spec-drift RLS at creation time.** Migration 0006 creates both tables with `USING`-only `tenant_isolation` and the `service_role_bypass` alias. Migrations 0011 and 0012 repair both — today's runtime state is correct — but do not copy the 0006 policy DDL. See Section 2 and the hardening walkthrough.
- **Spec-drift questions route.** The questions surface ships as a tab inside `/jobs/[id]/pipeline`, not as a sibling `/jobs/[id]/questions` page. The route exists but is a redirect. Spec-era deep links still resolve; the design spec's separate sidebar + main pane model is effectively one view.
- **Spec-drift coverage_notes persistence.** The spec deliberately wanted coverage_notes in Langfuse traces only. What shipped writes them to `stage_question_banks.coverage_notes` (migration 0007) and exposes them on `BankResponse`. No component renders them yet — it's a future hook without a consumer.
- **`BankHeader` regenerate button currently reuses the single-stage generate mutation.** `onRegenerate={() => generateMutation.mutate()}` in `QuestionsMainPane` — there is no dedicated "Regenerate all questions in this bank" endpoint; the generate endpoint is idempotent for confirmed/reviewing banks (`LEGAL` permits both source states), so clicking Regenerate re-triggers the same single-stage flow. The distinction the spec drew between "Generate" and "Regenerate" is a button-label-only concern today.
- **No `bank.question_updated` SSE event emitted server-side.** `use-questions-status-stream.ts` already has a handler for the event shape (to future-proof Phase 3's live probe updates), but `sse.py` never emits it. Today the flow is: recruiter edits question → PATCH invalidates query → refetch reads the new state. SSE is only used for generate / regenerate progress.
- **No unmount-flush on `QuestionEditForm`.** A mid-keystroke navigate-away drops the pending debounced save. The window is 800 ms so the exposure is small; contrast with `UnifiedPipelineView`'s pipeline autosave, which flushes on unmount via `stagesRef.current`.
- **Single-question regeneration does not gate on concurrent regen.** The state machine does not block a second `regenerate_question.send(...)` against the same question while the first is in flight. In practice the UI disables the menu during the mutation, but a direct API call could double-fire. Deferred — Phase 3 will need explicit per-question lock state anyway.
- **`coverage_notes` is written before `write_generated_questions`** in `_generate_one_bank`. If the post-validation step raises after the coverage_notes write but before `write_generated_questions` completes, the bank could end up with stale questions and fresh coverage_notes on the outer actor's `failed` commit. The window is tiny (a synchronous `db.add(...)` loop + flush) and the common path on exception is `transition_to_failed` flipping status to `failed`, which the outer actor commits as the "only failed status gets committed" invariant (commit `1a0b847`). Still: a theoretical drift between `bank.coverage_notes` and `stage_questions` on a mid-write crash.
- **Generate-all 409 guard is per-pipeline, not per-bank.** A single in-flight bank anywhere in the pipeline blocks `generate-all`. The opposite case — firing per-stage generates for multiple stages in quick succession — is allowed; banks transition to `generating` independently.
- **`UpdateQuestionBody` rejects empty `{}` at the schema layer, but a PATCH that includes a field set to its current value still triggers `auto_revert_on_edit`.** The `update_question` service doesn't compute a diff; any field in `exclude_unset=True` output is a "change." A pedantic client could flip a confirmed bank to reviewing by PATCHing `text` to the same string. Low severity: the UI only sends fields that changed, and the user initiated the edit anyway.
- **Pipeline-wide actor has no per-stage cost accounting.** `log_event` with `{succeeded, failed, total}` is the only aggregate output. Per-stage LLM cost + latency lives in Langfuse traces, not in application audit logs.

---

## 13. Cross-references

- **Phase 2A walkthrough (`docs/phase-2a-implementation.md`)** — `app/ai/` provider-agnostic layer, `PromptLoader`, `instructor.AsyncInstructor` + Langfuse wrapping, Dramatiq actor pattern, `_run_extraction` / `@observe` split model (which `_generate_one_bank` and `_regenerate_one_question` mirror).
- **Phase 2B walkthrough (`docs/phase-2b-implementation.md`)** — `JobPostingSignalSnapshot` append-only versioning, `get_latest_confirmed_snapshot` pattern (origin), signal schema v2 fields (`value`, `type`, `priority`, `weight`, `knockout`, `stage`) consumed by this phase, company profile ancestry walk (`find_company_profile_in_ancestry`), prompt-context-ordering rule (context before document).
- **Phase 2C.1 walkthrough (`docs/phase-2c1-implementation.md`)** — `job_pipeline_stages` table that banks FK to, the diff-and-sync `update_job_pipeline_stages` that preserves stage UUIDs (load-bearing for question-bank survival across edits), `require_template_access` / `require_instance_access` ancestry walk pattern reused here via `require_bank_access*`, `UnifiedPipelineView` which hosts the Phase 2C.2 inspector panel, `StageInspectorPanel` tab bar.
- **Hardening walkthrough (`docs/phase-hardening-implementation.md`)** — migrations 0008–0012, startup RLS completeness check (`_assert_rls_completeness` with both new tables enumerated in `_TENANT_SCOPED_TABLES`), `nexus_app` role, the full story of how 0006's incomplete RLS pattern was repaired.
- **Root `CLAUDE.md`** — canonical RLS pattern, `NULLIF` requirement, `FOR SELECT USING` trap, Langfuse self-hosted-only rule.
- **`backend/nexus/CLAUDE.md`** — `question_bank` module responsibilities table; `app/ai/` provider-agnostic interface; RLS runtime role contract; startup RLS completeness check; Dramatiq task queue conventions.
- **`frontend/app/CLAUDE.md`** — query-key discipline, dialog focus management, `useQuestionsStatusStream` ref-mirroring rule, `TanStack Query` invalidation conventions.
- **Design spec (`docs/superpowers/specs/2026-04-12-phase-2c2-question-generation-design.md`)** — original scope, anti-lie invariant framing, structured output rationale, coverage_notes-in-trace decision (reverted by 0007).
- **Implementation plan (`docs/superpowers/plans/2026-04-12-phase-2c2-question-generation.md`)** — task-by-task breakdown, test matrix, migration sequencing.
