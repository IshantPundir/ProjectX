# Docs Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `docs/` current by writing thorough per-phase implementation walkthroughs for every piece of work shipped from Phase 2B through Hardening Batch G, and refreshing the existing Phase 1, Phase 2A, and tech-stack docs to match.

**Architecture:** Seven sequential tasks, one per doc file, in a dependency-aware order. Each task researches the delivered code (delegated to an explore subagent for breadth + direct reads for depth), synthesizes findings into a single markdown walkthrough at phase-1-implementation.md depth, self-reviews against the spec, and commits. A mid-flight checkpoint after Task 1 (phase-2b) catches depth/style mismatch before sinking work into the rest.

**Tech Stack:** Markdown docs only. No code changes. Research tools: Grep, Glob, Read, Bash (git log), Agent (Explore/code-explorer). Writing tool: Write / Edit. All files land under `docs/`.

**Spec:** `docs/superpowers/specs/2026-04-15-docs-refresh-design.md`

---

## Ground rules for every task

These apply to every task below — do not re-read the spec file every task, but keep these in mind:

1. **Document delivered code, not spec intent.** If the delivered code diverges from the spec (e.g., renamed function, skipped validation, inlined something), describe what shipped and add a "Spec drift" sub-note in the relevant section.
2. **Research first, write second.** Never start writing prose until the research notes exist. The failure mode this avoids is documenting CLAUDE.md instead of the actual code.
3. **Match `phase-1-implementation.md` style.** Numbered sections, tables for schemas / API reference / troubleshooting, code blocks only where they carry information prose can't. Table of contents at the top.
4. **Cross-reference the spec and plan** at the top of each new file, matching the `phase-2a-implementation.md` pattern.
5. **Line budgets are guidance, not ceilings.** A 1000-line Phase 2C.2 walkthrough is fine if the story needs it. A 400-line Phase 2B walkthrough is also fine.
6. **Commit hashes** go inline only where a specific fix is load-bearing for the narrative. Don't enumerate every commit.
7. **No new architectural commentary.** Design rationale lives in the spec files. Phase docs describe what shipped.
8. **Verify before asserting.** If the doc says "X uses Y pattern", Grep/Read the file to confirm.

---

## Task 1: Write `phase-2b-implementation.md`

**Files:**
- Create: `docs/phase-2b-implementation.md`

**Target length:** ~500 lines (guidance)

**Scope (from spec):** Signal editing with snapshot versioning, FOR UPDATE row-lock version conflict detection, Call 2 re-enrichment dispatch, company profile ancestry walk in practice.

- [ ] **Step 1: Read the design spec and implementation plan**

Read in order:
- `docs/superpowers/specs/2026-04-10-phase-2b-signal-editing-design.md`
- `docs/superpowers/specs/2026-04-11-signal-schema-v2-job-metadata-design.md` (overlapping scope)
- `docs/superpowers/plans/2026-04-10-phase-2b-signal-editing.md`
- `docs/superpowers/plans/2026-04-11-signal-schema-v2-job-metadata.md`

Take notes on: intended shape, success criteria, any places the spec flagged as deferred.

- [ ] **Step 2: Read the migrations in scope**

Read:
- `backend/nexus/migrations/versions/0001_phase_2b_columns.py`
- `backend/nexus/migrations/versions/0002_add_updated_by_to_job_postings.py`
- `backend/nexus/migrations/versions/0003_signal_schema_v2.py`

Capture: new columns on `job_posting_signal_snapshots` and `job_postings`, default values, constraints, index additions.

- [ ] **Step 3: Read the JD service + schemas + errors**

Read in full:
- `backend/nexus/app/modules/jd/service.py`
- `backend/nexus/app/modules/jd/schemas.py`
- `backend/nexus/app/modules/jd/errors.py`
- `backend/nexus/app/modules/jd/state_machine.py`
- `backend/nexus/app/modules/jd/authz.py`
- `backend/nexus/app/modules/jd/router.py`
- `backend/nexus/app/modules/jd/actors.py`

Capture:
- `save_signals` flow including the `SELECT … FOR UPDATE` lock and `MAX(version)` check
- `VersionConflictError` shape and where it's raised
- `confirm_signals` flow including idempotent `PipelineAlreadyExistsError` handling
- `reenrich_jd` Dramatiq actor: what it reads, what it writes, how it differs from `extract_and_enhance_jd`
- State machine transitions touched by 2B (likely `signals_extracted → signals_confirmed`, confirmation audit)
- Any 2B-specific endpoints (`PATCH /api/jobs/{id}/signals`, `POST /api/jobs/{id}/confirm-signals`, `POST /api/jobs/{id}/reenrich`) — exact paths and request/response models

- [ ] **Step 4: Read the company profile ancestry helper**

Read:
- `backend/nexus/app/modules/org_units/service.py` (the `find_company_profile_in_ancestry` function specifically)
- `backend/nexus/app/modules/org_units/schemas.py` (the `CompanyProfile` strict schema)

Capture: how the helper is called from 2B code beyond 2A (e.g., in re-enrichment gates).

- [ ] **Step 5: Read the frontend signal editing surface**

Read:
- `frontend/app/components/dashboard/jd-panels/SignalsPanel.tsx` (or equivalent)
- `frontend/app/components/dashboard/jd-panels/EditableChipRow.tsx` (if it exists as a named component)
- Any hook like `use-save-signals.ts` / `use-confirm-job.ts` / `use-reenrich-job.ts` in `frontend/app/lib/hooks/`
- `frontend/app/lib/api/jobs.ts` — the save/confirm/reenrich API functions
- `frontend/app/app/(dashboard)/jobs/[jobId]/review/page.tsx`

Capture: edit mode affordances, optimistic updates, version conflict handling, confirm button flow.

- [ ] **Step 6: Git log 2B-scoped commits**

Run: `git log --oneline --since=2026-04-10 --until=2026-04-12 -- backend/nexus/app/modules/jd frontend/app/components/dashboard/jd-panels`

Capture: any fixes not mentioned in CLAUDE.md (e.g., the stable-key fix in `EditableChipRow` — commit `9dac616`; the idempotency fix in `confirm_signals` — commit `a7ba2ea`).

- [ ] **Step 7: Dispatch verification agent (optional but recommended)**

Dispatch `feature-dev:code-explorer` with this tightly-scoped prompt (paste verbatim, adapt file list as needed):

> Trace the Phase 2B signal-editing flow end-to-end in the ProjectX backend at `/home/ishant/Projects/ProjectX/backend/nexus`. Starting from `PATCH /api/jobs/{id}/signals` in `app/modules/jd/router.py`, follow into `service.save_signals()`. List: every DB query executed (including the row lock and version check), every error class that can be raised, the exact state machine transition, the audit_log rows written, and whether any Dramatiq actor is dispatched. Then do the same for `POST /api/jobs/{id}/confirm-signals` and `POST /api/jobs/{id}/reenrich`. Report in under 600 words. Do not speculate — only describe what the code does. Cite file:line for each claim.

Use the agent's notes as a reality check against your own reading, not as a replacement for it. Synthesize yourself — never delegate understanding.

- [ ] **Step 8: Write `docs/phase-2b-implementation.md`**

Follow this section skeleton (from the approved spec):

```
# Phase 2B Implementation — Developer Documentation

**Scope:** Signal editing, version conflict detection, Call 2 re-enrichment, company profile ancestry in practice
**Status:** Complete and functional
**Last updated:** 2026-04-15

See also:
- Design spec: `docs/superpowers/specs/2026-04-10-phase-2b-signal-editing-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-10-phase-2b-signal-editing.md`

## Table of Contents
1. Architecture Overview
2. Database Schema (migrations 0001–0003)
3. Signal Editing Flow
4. Confirmation Flow
5. Call 2 Re-enrichment
6. Company Profile Ancestry Walk
7. API Reference
8. Frontend Architecture
9. Known Gaps
```

Write each section using the notes from Steps 1–7. Use tables for the schema section, bullet-numbered steps for the flow sections, and a table for the API reference.

- [ ] **Step 9: Self-review against spec**

Open `docs/superpowers/specs/2026-04-15-docs-refresh-design.md` alongside the new doc. For each outline item under `phase-2b-implementation.md`, confirm the doc covers it. Fix gaps inline.

Also scan for: TBD, TODO, "fill in later", any unexplained jargon. Fix inline.

- [ ] **Step 10: Commit**

```bash
git add docs/phase-2b-implementation.md
git commit -m "docs: add phase 2B implementation walkthrough"
```

- [ ] **Step 11: USER CHECKPOINT**

Report to the user:
- Link to the new doc
- Line count and a one-paragraph summary of what's inside
- Any spec drift found
- Ask: "Does this match the depth and style you want? If you want shorter / longer / differently organized, now's the time to say so before I write the next four files."

Wait for user response. Do not start Task 2 until the user confirms.

---

## Task 2: Write `phase-2c1-implementation.md`

**Files:**
- Create: `docs/phase-2c1-implementation.md`

**Target length:** ~700 lines (guidance)

**Scope (from spec):** Pipeline builder — template library, per-job instances, stages, drag-to-reorder, auto-apply on signal confirmation.

- [ ] **Step 1: Read the design spec and plan**

Read:
- `docs/superpowers/specs/2026-04-12-phase-2c1-pipeline-builder-design.md`
- `docs/superpowers/plans/2026-04-12-phase-2c1-pipeline-builder.md`

- [ ] **Step 2: Read the migrations**

Read:
- `backend/nexus/migrations/versions/0004_pipeline_builder.py`
- `backend/nexus/migrations/versions/0005_simplify_signal_filter.py`

Capture: the four new tables (`pipeline_templates`, `pipeline_template_stages`, `job_pipelines`, `job_pipeline_stages`), their columns, FKs, RLS policies (which must include the canonical `tenant_isolation` + `service_bypass` pair — verify this), and the signal filter schema flattening in 0005.

- [ ] **Step 3: Read the pipelines module in full**

Read:
- `backend/nexus/app/modules/pipelines/router.py`
- `backend/nexus/app/modules/pipelines/service.py`
- `backend/nexus/app/modules/pipelines/authz.py`
- `backend/nexus/app/modules/pipelines/errors.py`
- `backend/nexus/app/modules/pipelines/schemas.py` (if it exists)

Capture:
- Template library CRUD and tenant scoping
- `require_template_access` ancestry walk details
- Per-job pipeline instance creation, stage CRUD, reorder, template swap, reset-to-source
- `auto_apply_pipeline_on_confirmation` and the idempotent `PipelineAlreadyExistsError` handling
- Starter pipelines seeding (if any)

- [ ] **Step 4: Read the frontend pipeline surface**

Read:
- `frontend/app/components/dashboard/pipeline/PipelineFlowColumn.tsx` (or equivalent)
- `frontend/app/components/dashboard/pipeline/PipelineFunnel.tsx`
- `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`
- `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx`
- Any pipeline-related hook in `frontend/app/lib/hooks/`
- `frontend/app/lib/api/pipelines.ts` (or equivalent)

Capture: the dnd-kit wiring (`KeyboardSensor` for a11y), the stage config drawer focus management pattern (ref + useEffect), stable keys in PipelineFunnel.

- [ ] **Step 5: Git log 2C.1 scope**

Run: `git log --oneline --since=2026-04-12 --until=2026-04-13 -- backend/nexus/app/modules/pipelines frontend/app/components/dashboard/pipeline`

Also look for specific commits: `475df30` (PipelineFunnel stable keys), `dd2f528` (focus management), `73adb68` (auto-select effect refactor).

- [ ] **Step 6: Dispatch verification agent (optional)**

Prompt:

> Trace the Phase 2C.1 pipeline builder in the ProjectX backend at `/home/ishant/Projects/ProjectX/backend/nexus/app/modules/pipelines`. Starting from the router, enumerate: every endpoint, what service function it calls, the DB tables touched, the authz guard invoked, and any errors raised. Then trace `auto_apply_pipeline_on_confirmation` — what triggers it, what it reads, what it writes, what error cases it handles. Report in under 600 words. Cite file:line for each claim.

- [ ] **Step 7: Write `docs/phase-2c1-implementation.md`**

Section skeleton (from spec):

```
1. Architecture Overview
2. Database Schema (migrations 0004, 0005)
3. Template Library
4. Per-Job Pipeline Instance
5. Stage Configuration
6. Auto-Apply on Signal Confirmation
7. API Reference
8. Frontend Architecture
9. Known Gaps
10. Cross-references
```

Write each section from the notes. Tables for schema and API reference. Numbered flows for create-from-template, reorder, and auto-apply.

- [ ] **Step 8: Self-review**

Scan against the spec outline. Fix placeholders and drift inline.

- [ ] **Step 9: Commit**

```bash
git add docs/phase-2c1-implementation.md
git commit -m "docs: add phase 2C.1 pipeline builder walkthrough"
```

---

## Task 3: Write `phase-2c2-implementation.md`

**Files:**
- Create: `docs/phase-2c2-implementation.md`

**Target length:** ~800 lines (guidance)

**Scope (from spec):** Question bank generation with adaptive coverage, bundling, SSE progress stream, Langfuse wiring.

- [ ] **Step 1: Read the design spec and plan**

Read:
- `docs/superpowers/specs/2026-04-12-phase-2c2-question-generation-design.md`
- `docs/superpowers/plans/2026-04-12-phase-2c2-question-generation.md`

- [ ] **Step 2: Read the migrations**

Read:
- `backend/nexus/migrations/versions/0006_question_banks.py`
- `backend/nexus/migrations/versions/0007_add_coverage_notes.py`

Capture: `question_banks` and `questions` table shapes, the `coverage_notes` column addition, FKs to `job_pipeline_stages`, RLS policies.

- [ ] **Step 3: Read the question_bank module in full**

Read:
- `backend/nexus/app/modules/question_bank/router.py`
- `backend/nexus/app/modules/question_bank/service.py`
- `backend/nexus/app/modules/question_bank/authz.py`
- `backend/nexus/app/modules/question_bank/state_machine.py`
- `backend/nexus/app/modules/question_bank/actors.py`
- `backend/nexus/app/modules/question_bank/sse.py`
- `backend/nexus/app/modules/question_bank/schemas.py`
- `backend/nexus/app/modules/question_bank/errors.py` (if separate)

Capture:
- `generate_question_bank_stage` actor: what it does per stage, bundling logic, prompt loading
- Adaptive coverage: mandatory-fits-session validation, auto-demotion, duration as session time limit
- Read-idempotent `list_banks` GET (placeholder entries, no draft creation on poll)
- Bulk load `get_banks_for_pipeline`: the 4-query pattern
- State machine: `IllegalTransitionError`, `ReorderMismatchError`, failure commit scoping
- Coverage notes persistence

- [ ] **Step 4: Read the prompts**

Read (skim, don't include verbatim in the doc):
- `backend/nexus/prompts/v1/question_bank_common.txt`
- `backend/nexus/prompts/v1/question_bank_<stage_type>.txt` (list them)

Capture the structure and how stage type drives prompt selection.

- [ ] **Step 5: Read the Langfuse wiring**

Grep for `@observe` in `backend/nexus/app/modules/question_bank/` and `backend/nexus/app/modules/jd/`. Read the `app/ai/client.py` Langfuse factory and the `_is_langfuse_cloud_host` guard.

Capture: what traces get captured, what metadata is attached.

- [ ] **Step 6: Read the frontend question bank surface**

Read:
- Question bank panel components in `frontend/app/components/dashboard/` (likely under `question-bank/` or `pipeline/`)
- `QuestionEditForm` specifically (for the remount-on-id-change pattern from `2d16c2f`)
- `frontend/app/lib/hooks/use-questions-status-stream.ts`
- `frontend/app/lib/api/question-bank.ts` (or equivalent)

Capture: the SSE hook's stable-connection pattern across stage changes (`2dfa766`), question editing UI.

- [ ] **Step 7: Git log 2C.2 scope**

Run: `git log --oneline --since=2026-04-12 -- backend/nexus/app/modules/question_bank frontend/app/components/dashboard/question-bank frontend/app/lib/hooks/use-questions-status-stream.ts`

Specific commits of interest: `23e78bc` (read-idempotent GET), `1a0b847` (failure commit scoping), `2dfa766` (SSE stability), `2d16c2f` (QuestionEditForm remount).

- [ ] **Step 8: Dispatch verification agent (optional)**

Prompt:

> Trace the Phase 2C.2 question-bank flow in ProjectX at `/home/ishant/Projects/ProjectX/backend/nexus/app/modules/question_bank`. Starting from the generate endpoint, follow into the Dramatiq actor `generate_question_bank_stage`. List: every LLM call made per stage, the prompt file loaded, the adaptive coverage validation steps, the mandatory-demotion logic, what gets written to `question_banks` and `questions`, and all error classes raised. Separately trace the `list_banks` GET and confirm it is read-idempotent (i.e. does NOT write a draft row on poll). Report in under 700 words. Cite file:line.

- [ ] **Step 9: Write `docs/phase-2c2-implementation.md`**

Section skeleton (from spec):

```
1. Architecture Overview
2. Database Schema (migrations 0006, 0007)
3. Generation Flow
4. Adaptive Coverage
5. Read-Idempotent list_banks GET
6. Bulk Load
7. State Machine
8. Langfuse Wiring
9. SSE Progress Stream
10. API Reference
11. Frontend Architecture
12. Known Gaps
13. Cross-references
```

- [ ] **Step 10: Self-review**

Scan against spec outline. Fix inline.

- [ ] **Step 11: Commit**

```bash
git add docs/phase-2c2-implementation.md
git commit -m "docs: add phase 2C.2 question bank walkthrough"
```

---

## Task 4: Write `phase-hardening-implementation.md`

**Files:**
- Create: `docs/phase-hardening-implementation.md`

**Target length:** ~700 lines (guidance)

**Scope (from spec):** Batches D/F/G — RLS runtime role, NULLIF, policy fixes, startup assertion, JWT hardening, SSE RLS fix, security headers, `FRONTEND_BASE_URL`, misc fixes. Cross-cuts all earlier phases.

- [ ] **Step 1: Read the migrations 0008–0012 in full**

Read:
- `backend/nexus/migrations/versions/0008_audit_log_tenant_insert.py`
- `backend/nexus/migrations/versions/0009_phase1_rls_full_command.py`
- `backend/nexus/migrations/versions/0010_create_nexus_app_role.py`
- `backend/nexus/migrations/versions/0011_rls_nullif_tenant.py`
- `backend/nexus/migrations/versions/0012_rename_service_role_bypass.py`

Capture: the SQL actually executed, the tables touched, the policy text before/after.

- [ ] **Step 2: Read the startup assertion + database module**

Read:
- `backend/nexus/app/main.py` (in full — focus on `_assert_rls_completeness`, the enumerated table list, skip conditions)
- `backend/nexus/app/database.py` (in full — `get_tenant_db`, `get_bypass_db`, the `SET LOCAL ROLE` pattern, the `DB_RUNTIME_ROLE` env var wiring)

Capture: how the role switch happens, what GUCs get set, what the skip conditions are.

- [ ] **Step 3: Read the JWT verification module**

Read:
- `backend/nexus/app/modules/auth/service.py` (in full — `verify_access_token`, `verify_candidate_token`, the algorithm pinning, audience check, issuer check)
- `backend/nexus/app/middleware/auth.py` (the candidate JWT path, the single-use TODO)

Capture: the exact checks enforced and in what order, the allowed algorithms, the candidate-JWT replay gap.

- [ ] **Step 4: Read the SSE hardening points**

Read:
- `backend/nexus/app/modules/jd/sse.py`
- `backend/nexus/app/modules/question_bank/sse.py`
- `frontend/app/lib/hooks/use-job-status-stream.ts`
- `frontend/app/lib/hooks/use-questions-status-stream.ts`

Capture: `get_tenant_session` routing, `MAX_TOTAL_RETRIES`, the ref-mirroring stability pattern.

- [ ] **Step 5: Read the security header config**

Read:
- `frontend/app/next.config.ts`
- `frontend/admin/next.config.ts`

Capture: exact headers set, per-surface differences (dashboard gets `Permissions-Policy`).

- [ ] **Step 6: Read the FRONTEND_BASE_URL fix**

Read:
- `backend/nexus/app/config.py` (look for `frontend_base_url`)
- `backend/nexus/app/modules/notifications/service.py` or wherever invite link URLs are built
- Grep for any remaining `settings.debug` branching in URL building

Capture: old pattern, new pattern, why the change mattered.

- [ ] **Step 7: Git log the hardening commits**

Run: `git log --oneline 380fbf2~1..HEAD -- backend/nexus frontend/app frontend/admin`

Collect every commit from `380fbf2` (JWT tightening) forward. Read each commit message. The ones load-bearing for the hardening narrative:
- `380fbf2` — JWT ES256 + audience/issuer
- `3e38981` — nexus_app role (RLS enforcement)
- `f6cd25e` — NULLIF cast
- `5414bf5` — Phase 1 full-command policies
- `bd4b6bb` — SSE routed through get_tenant_session
- `f9dc628` — security headers
- `07cf0b6` — FRONTEND_BASE_URL
- `5aa27ef` — correlation-ID validation
- `bd83cf7` — startup RLS check
- `72689b9` — service_bypass rename
- `c79682d` — JWT regression fixes + CORS-on-401
- `1369b42` — query key discipline fix
- `dd2f528` — focus management
- `475df30`, `9dac616` — stable keys
- `a7ba2ea` — confirm_signals idempotency
- `1a0b847` — question_bank failure commit scoping
- `23e78bc` — read-idempotent list_banks
- `4dc26b9`, `2dfa766` — SSE stability

- [ ] **Step 8: Dispatch verification agent (recommended — largest scope)**

Prompt:

> In the ProjectX repo at `/home/ishant/Projects/ProjectX`, verify the current state of the following hardening claims by reading the actual code (cite file:line for each):
>
> 1. `app/main.py::_assert_rls_completeness` exists and enumerates tenant-scoped tables. What is the exact enumerated list?
> 2. `app/database.py` runs `SET LOCAL ROLE nexus_app` at the top of `get_tenant_db` and `get_bypass_db`. What env var controls this? What are the skip conditions?
> 3. `app/modules/auth/service.py::verify_access_token` pins `algorithms=["ES256"]`, checks `aud="authenticated"`, and checks `iss` against `{supabase_url}/auth/v1`. Quote the relevant lines.
> 4. `app/modules/jd/sse.py` and `app/modules/question_bank/sse.py` both route polling through `get_tenant_session`. Quote the relevant lines.
> 5. `frontend/app/next.config.ts` and `frontend/admin/next.config.ts` set what security headers? List them exactly.
> 6. `app/middleware/auth.py` has a candidate-JWT single-use TODO. Where exactly? Quote the line.
>
> Report in under 800 words. Do not speculate — only describe what the code does.

- [ ] **Step 9: Write `docs/phase-hardening-implementation.md`**

Section skeleton (from spec):

```
1. What This Covers (Batches D/F/G)
2. RLS Runtime Role (Migration 0010)
3. RLS NULLIF Cast (Migration 0011)
4. Phase 1 Full-Command Policies (Migration 0009)
5. Audit Log INSERT Fix (Migration 0008)
6. Policy Rename (Migration 0012)
7. Startup RLS Completeness Check
8. JWT Hardening
9. CORS-on-401 Fix
10. SSE RLS Fix
11. Security Headers
12. Configurable FRONTEND_BASE_URL
13. Correlation-ID Header Validation
14. Misc Fixes
15. Remaining Pre-Phase-3 Hardening (candidate-JWT single-use TODO)
16. Cross-references
```

For each RLS section, include the before/after policy SQL in a code block if it changed behavior. Reference commit hashes inline.

- [ ] **Step 10: Self-review**

Every migration 0008–0012 mentioned? Every bullet in the spec outline covered? No "TODO" or vague language? Fix inline.

- [ ] **Step 11: Commit**

```bash
git add docs/phase-hardening-implementation.md
git commit -m "docs: add post-2C hardening walkthrough (batches D/F/G)"
```

---

## Task 5: Expand `phase-2a-implementation.md`

**Files:**
- Modify: `docs/phase-2a-implementation.md` (124 → ~700 lines)

**Scope (from spec):** Add sections 1–9 before the existing data flow / troubleshooting / known gaps sections.

- [ ] **Step 1: Re-read the existing phase-2a doc**

Read `docs/phase-2a-implementation.md` in full. Identify what's already covered so the expansion doesn't duplicate.

- [ ] **Step 2: Read the design spec and plan**

Read:
- `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md`
- `docs/superpowers/plans/2026-04-09-phase-2a-implementation.md`

- [ ] **Step 3: Read the 2A-scoped migrations and modules**

Much of this is already loaded from Task 1 (phase-2b). Re-read only:
- The initial Phase 2A migrations (everything before 0001 if Phase 2A pre-dates Alembic, or whichever subset applies — check `migrations/versions/`)
- `backend/nexus/app/ai/{config,client,prompts,schemas}.py` in full
- `backend/nexus/app/modules/jd/actors.py` — specifically `extract_and_enhance_jd` and `_run_extraction`
- `backend/nexus/prompts/v1/jd_enhancement.txt` (skim, don't include verbatim)

- [ ] **Step 4: Read the frontend JD review shell**

Read:
- `frontend/app/app/(dashboard)/jobs/page.tsx`
- `frontend/app/app/(dashboard)/jobs/[jobId]/review/page.tsx`
- `frontend/app/components/dashboard/jd-panels/*.tsx` (OriginalJdPanel, EnrichedJdPanel, SignalsPanel, LoadingSkeleton, ErrorBanner, SignalChip)
- `frontend/app/lib/hooks/use-job.ts`
- `frontend/app/lib/hooks/use-job-status-stream.ts`
- `frontend/app/lib/api/jobs.ts`
- `frontend/app/components/dashboard/providers.tsx`

- [ ] **Step 5: Edit the existing file to insert new sections**

Use the Edit tool (not Write — preserve the existing content) to add sections 1–9 before the current Data Flow section. The new structure:

```
# Phase 2A Implementation — Developer Documentation

(existing preamble + cross-references)

## Table of Contents
1. Architecture Overview
2. Database Schema
3. Auth & Permissions
4. JD State Machine
5. Company Profile Gate
6. Call 1 Extraction
7. app/ai/ Layer
8. API Reference
9. Frontend Architecture
10. Data Flow (existing)
11. Troubleshooting (existing)
12. Known Gaps (existing)
```

Write sections 1–9 using the notes from Steps 2–4. Renumber the existing content if needed.

- [ ] **Step 6: Self-review**

Spec outline under `phase-2a-implementation.md expansion` covered? Fix inline.

- [ ] **Step 7: Commit**

```bash
git add docs/phase-2a-implementation.md
git commit -m "docs: expand phase 2A walkthrough to match phase-1 depth"
```

---

## Task 6: Amend `phase-1-implementation.md`

**Files:**
- Modify: `docs/phase-1-implementation.md`

**Scope (from spec):** Surgical amendments only. New Section 11 cross-linking hardening. Stale-line fixes.

- [ ] **Step 1: Re-read phase-1 to find stale lines**

Read `docs/phase-1-implementation.md` in full. Hunt for:
- "Alembic `versions/` is empty" (known stale — now 12 revisions)
- Any mention of the RLS pattern that doesn't include `NULLIF(...)` or `nexus_app` role
- Any mention of `FOR SELECT USING` on the Phase 1 tables (should now be full-command)
- Any other drift from the current backend/nexus/CLAUDE.md

- [ ] **Step 2: Add Section 11 "Post-Phase-1 Amendments (Hardening Batches)"**

Insert before the existing "10. Known Gaps & Technical Debt". One paragraph summary + cross-link to `phase-hardening-implementation.md`. Keep it short — the full story lives in the hardening doc.

Example text (adapt to actual structure):

```markdown
## 11. Post-Phase-1 Amendments (Hardening Batches)

After this document was first written, migrations 0008 through 0012 touched
Phase 1 tables and the RLS enforcement model:

- Migration 0008 added a `FOR INSERT WITH CHECK` policy to `audit_log` that
  had been silently dropping tenant-scoped writes.
- Migration 0009 replaced the Phase 1 `FOR SELECT USING(...)` policies with
  the canonical full-command `tenant_isolation` (USING + WITH CHECK) pair
  on `clients`, `users`, `organizational_units`, `user_role_assignments`,
  and `user_invites`.
- Migration 0010 created the `nexus_app` role with `NOBYPASSRLS` and
  switched `get_tenant_db` / `get_bypass_db` to run `SET LOCAL ROLE
  nexus_app` at the top of every transaction. Without this, every policy
  was a runtime no-op because the default `postgres` role has
  `rolbypassrls=true`.
- Migration 0011 wrapped `current_setting('app.current_tenant', true)::uuid`
  in `NULLIF(..., '')::uuid` across every tenant_isolation policy, fixing
  a PG quirk where `SET LOCAL` restores empty-string instead of NULL on a
  custom GUC at transaction end.
- Migration 0012 renamed `service_role_bypass` → `service_bypass` for
  consistency with the canonical name used elsewhere.

A startup assertion in `app/main.py::_assert_rls_completeness` now verifies
that every tenant-scoped table has both `tenant_isolation` (with non-NULL
WITH CHECK) and `service_bypass` at boot — the app aborts with a CRITICAL
log if any are missing.

See `docs/phase-hardening-implementation.md` for the full story.
```

- [ ] **Step 3: Fix stale lines**

Replace:
- "Alembic is configured (`backend/nexus/migrations/env.py`) but `versions/` is empty" → "Alembic is configured at `backend/nexus/migrations/env.py` and had 12 revisions (0001 through 0012) as of 2026-04-15."
- Any other drift found in Step 1 — describe behavior as of 2026-04-15 and cross-link hardening where relevant.

- [ ] **Step 4: Commit**

```bash
git add docs/phase-1-implementation.md
git commit -m "docs(phase-1): amend with post-phase-1 hardening cross-reference"
```

---

## Task 7: Refresh `tech-stack.md`

**Files:**
- Modify: `docs/tech-stack.md`

**Scope (from spec):** Refresh Phase 2+ additions across backend and frontend.

- [ ] **Step 1: Read the current tech-stack.md**

Read `docs/tech-stack.md` in full. Identify what's already there and what needs updating vs. adding.

- [ ] **Step 2: Read the CLAUDE.md files for ground truth**

Read (or re-read):
- `backend/nexus/CLAUDE.md`
- `frontend/app/CLAUDE.md`
- `frontend/admin/CLAUDE.md`

Extract the current tech stack from each.

- [ ] **Step 3: Read the package manifests**

Read:
- `backend/nexus/pyproject.toml`
- `frontend/app/package.json`
- `frontend/admin/package.json`

Cross-check versions. The tech-stack doc should cite major versions correctly.

- [ ] **Step 4: Edit tech-stack.md**

Use the Edit tool. Add / refresh sections for:

**Backend additions:**
- Dramatiq + Redis (task queue, worker entrypoint at `app/worker.py`)
- `app/ai/` layer (instructor + langfuse.openai + OpenAI, provider-agnostic)
- `nexus_app` runtime role (`NOBYPASSRLS`, created by migration 0010)
- Alembic head: `0012_rename_service_role_bypass`

**Frontend additions:**
- shadcn/ui v4 (Base UI, not Radix — note the ecosystem gotchas from `frontend/app/CLAUDE.md`)
- TanStack Query v5 + devtools
- React Hook Form + Zod (`@hookform/resolvers/zod`)
- @dnd-kit (core + sortable + KeyboardSensor for a11y)
- @xyflow/react v12 + dagre
- @microsoft/fetch-event-source (SSE client)
- sonner (toasts)
- Vitest + @testing-library/react + jsdom

Keep the existing structure intact. Add new entries where they belong; do not rewrite untouched sections.

- [ ] **Step 5: Self-review**

Every version number verified against `package.json` / `pyproject.toml`? No stale references to deprecated libs? Fix inline.

- [ ] **Step 6: Commit**

```bash
git add docs/tech-stack.md
git commit -m "docs(tech-stack): refresh for phase 2+ stack additions"
```

---

## Task 8: Final verification pass

**Files:** none (read-only pass)

- [ ] **Step 1: Read each new doc end-to-end**

In order: `phase-2a-implementation.md`, `phase-2b-implementation.md`, `phase-2c1-implementation.md`, `phase-2c2-implementation.md`, `phase-hardening-implementation.md`. Look for:
- Unexplained jargon
- Broken cross-references
- Duplicated content that should have been cross-linked instead
- Any placeholder or "TODO" that slipped through

- [ ] **Step 2: Spec-coverage check**

Open `docs/superpowers/specs/2026-04-15-docs-refresh-design.md`. For each outline item and each success criterion, confirm it's covered somewhere. Success criteria 4 especially: **every Alembic migration 0001–0012 must be referenced in a doc**.

- [ ] **Step 3: Report to user**

Summarize what was written, total line count added, any spec drift found and called out, and any follow-up work the research surfaced that wasn't in the original scope.

No commit — this task is read-only.

---

## Self-review checklist (writer, before handoff)

- [x] Every spec deliverable has a task
- [x] Every task has concrete steps (file paths, commands, section skeletons)
- [x] No "TODO" / "TBD" / "fill in later" placeholders
- [x] Task 1 ends with a user checkpoint
- [x] Hardening task dispatches a verification agent (largest scope)
- [x] Migration references match the actual `migrations/versions/` directory
- [x] Task 6 (phase-1 amendments) is surgical, not a rewrite
- [x] Task 8 is a read-only final verification

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-15-docs-refresh.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best when tasks are independent and main context should stay small. For this plan, research-heavy tasks benefit the most.

**2. Inline Execution** — Execute tasks in this session using executing-plans, with a batch checkpoint after Task 1 (the mandatory user review) and another after Task 4 (hardening) before tackling the amendments. Main context grows but review is tighter.

Which approach?
