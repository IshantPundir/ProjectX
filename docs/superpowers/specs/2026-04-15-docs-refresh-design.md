# Docs Refresh — Bring Implementation Walkthroughs Current

**Date:** 2026-04-15
**Status:** Draft (awaiting user review)
**Owner:** Ishant
**Scope:** `docs/` — extend per-phase implementation walkthroughs to cover all work shipped through 2026-04-15

---

## Goal

Make the `docs/` tree a complete, current, and thorough record of everything shipped so far, matching the depth and style of `phase-1-implementation.md` (which is the existing quality bar at 947 lines, thorough to the level of DB schema tables, API reference, frontend architecture, and known gaps).

Today, `docs/` covers Phase 1 and a thin Phase 2A. Since the Phase 2A walkthrough was written (2026-04-09), the codebase has shipped Phase 2B, Phase 2C.1, Phase 2C.2, and three rounds of hardening (Batches D/F/G), all documented only via specs/plans and commits. This spec defines a plan to close that gap.

## Non-goals

- Restructuring the docs tree into a module-oriented reference (considered and declined — see "Alternatives considered")
- Writing an onboarding guide or a gap/audit report (different deliverables; can come later if wanted)
- Archiving or deleting existing phase docs
- Adding inline code listings for things already documented in CLAUDE.md files
- Generating API docs from OpenAPI schema (out of scope; API reference is hand-written in each phase doc to cover request/response shape and error cases)

## Success criteria

1. A reader who reads all of `docs/phase-*-implementation.md` in order understands:
   - Every feature shipped, end-to-end
   - The data model and RLS policies at each phase
   - Every API endpoint and its error cases
   - How the frontend renders each feature
   - Known gaps and deferred work

2. Each new phase doc is self-contained. It cross-references its spec and plan, but a reader does not need to open them to understand what shipped.

3. Each new phase doc describes the **delivered** code, not the **intended** design. Spec drift is called out explicitly where it happened.

4. After the refresh, every tenant-scoped feature in the codebase is mentioned in at least one doc file, and every Alembic migration (0001–0012) is referenced in a doc.

5. Each new phase doc passes a spec-vs-code reality check: if a phase doc says "X uses pattern Y", the file that implements X actually uses pattern Y as of the commit the doc is written against.

## Background

### Current docs state

```
docs/
├── phase-1-implementation.md          ← 947 lines, current through 2026-04-07
├── phase-2a-implementation.md         ← 124 lines, thin (data flow + troubleshooting only)
├── tech-stack.md                      ← 346 lines, last touched 2026-04-02 — predates Phase 2A
└── superpowers/
    ├── specs/                         ← 11 design specs
    └── plans/                         ← 13 implementation plans
```

### What has shipped but is not documented

| Phase / Effort | Scope | Docs state |
|---|---|---|
| Phase 2B | Signal editing with snapshot versioning + FOR UPDATE row-lock version conflict detection; company profile ancestry walk in practice | Spec + plan only |
| Phase 2C.1 | Pipeline builder: templates, per-job instances, stages, drag-to-reorder, starter pipelines, auto-apply on confirmation | Spec + plan only |
| Phase 2C.2 | Question bank generation: per-stage LLM call, adaptive coverage, mandatory demotion, bundling, SSE progress stream, coverage audit trail | Spec + plan only |
| Hardening Batches D/F/G | RLS runtime role (`nexus_app`), NULLIF cast fix, Phase 1 full-command policies, audit_log INSERT fix, `service_bypass` rename, startup RLS assertion, JWT ES256 pinning, JWT issuer check, CORS-on-401 fix, SSE routed through `get_tenant_session`, SSE reconnect ceiling, security headers, `FRONTEND_BASE_URL` config, correlation-ID validation, query-key discipline, a11y focus management | Commits only |

### Why now

`backend/nexus/CLAUDE.md` has absorbed a lot of the hardening detail inline, but CLAUDE.md files are navigational guides, not walkthroughs. New engineers currently have to read commits to learn what Phase 2B/2C.1/2C.2 shipped. That's a workable fallback for now but does not scale past Phase 3.

## Approach

### Decision: per-phase walkthrough files (match existing pattern)

Extend the `phase-N-implementation.md` series. Each new file follows the Phase 1 doc's table-of-contents shape (architecture, schema, per-subsystem walkthrough, API reference, frontend architecture, known gaps). Hardening gets its own phase doc (`phase-hardening-implementation.md`) because it cuts across all earlier phases — documenting it per-phase would fragment the RLS / auth / SSE hardening story.

### Alternatives considered

| Option | Why not |
|---|---|
| Module-oriented reference tree (`docs/modules/auth.md`, `docs/modules/jd.md`, …) | Evergreen benefit, but a bigger restructure that loses the historical phase narrative. The phase series is already the project's mental model — don't break it. |
| Hybrid (keep phase docs as history, add `docs/reference/` for evergreen module docs) | Most work; readers have to pick which tree to read; not justified without a clearer "what problem is this solving" answer. |
| Thin docs (Phase 2A-style, just data flow + troubleshooting) | Rejected explicitly by the user — "I want the docs to have everything". Thin docs rot within weeks. |
| Auto-generated from OpenAPI / code comments | Code comments don't exist at the required density, and OpenAPI misses everything interesting (state machines, data flow, frontend). |

## Deliverables

### New files

| File | Est. lines | Scope |
|---|---|---|
| `docs/phase-2b-implementation.md` | ~500 | Signal editing, version conflict detection, Call 2 re-enrichment, company profile ancestry in practice |
| `docs/phase-2c1-implementation.md` | ~700 | Pipeline builder: templates, instances, stages, drag-to-reorder, auto-apply |
| `docs/phase-2c2-implementation.md` | ~800 | Question bank generation, adaptive coverage, bundling, SSE, Langfuse wiring |
| `docs/phase-hardening-implementation.md` | ~700 | Batches D/F/G: RLS runtime role, NULLIF, policy fixes, startup assertion, JWT hardening, SSE RLS fix, security headers, `FRONTEND_BASE_URL`, misc fixes |

### Updated files

| File | Change |
|---|---|
| `docs/phase-2a-implementation.md` | Expand 124 → ~700 lines. Add: architecture overview, DB schema, auth & permissions, JD state machine, company profile gate, Call 1 extraction walkthrough, `app/ai/` layer, API reference, frontend architecture. Current content becomes Sections 10–12. |
| `docs/phase-1-implementation.md` | Add Section 11 "Post-Phase-1 Amendments (Hardening Batches)" cross-linking to `phase-hardening-implementation.md`. Fix stale lines: "Alembic `versions/` is empty" → "Alembic has 12 revisions (0001 through 0012)". Note that `_assert_rls_completeness` now enforces the RLS pattern at boot. Surgical edits only — no rewrites. |
| `docs/tech-stack.md` | Refresh Phase 2+ stack additions: Dramatiq + Redis, `app/ai/` layer, shadcn/ui v4 (Base UI), TanStack Query v5, React Hook Form + Zod, @dnd-kit, @xyflow/react, fetch-event-source, sonner, Vitest, nexus_app runtime role, Alembic head 0012. |

## Per-file outlines

### `phase-2b-implementation.md`

1. Architecture Overview — what 2B built on top of 2A
2. Database Schema — snapshot versioning columns, migration 0001
3. Signal Editing Flow — `save_signals` with `SELECT … FOR UPDATE` row lock + `MAX(version)` check, `VersionConflictError`
4. Confirmation Flow — `confirm_signals`, state transition `signals_extracted → signals_confirmed`, idempotent `PipelineAlreadyExistsError` handling (fix from commit `a7ba2ea`)
5. Call 2 Re-enrichment — dispatch, `reenrich_jd` actor, what it does differently from Call 1
6. Company Profile Ancestry Walk — `find_company_profile_in_ancestry`, usage in gates beyond 2A
7. API Reference — `PATCH /api/jobs/{id}/signals`, `POST /api/jobs/{id}/confirm-signals`, `POST /api/jobs/{id}/reenrich`
8. Frontend Architecture — SignalsPanel edit mode, `EditableChipRow` (stable key fix from commit `9dac616`), optimistic updates, version conflict toast, confirm button flow
9. Known Gaps
10. Cross-references — design spec + implementation plan

### `phase-2c1-implementation.md`

1. Architecture Overview — template library vs. per-job instance model
2. Database Schema — `pipeline_templates`, `pipeline_template_stages`, `job_pipelines`, `job_pipeline_stages` (migration 0004); signal filter flattening (migration 0005)
3. Template Library — tenant scoping, starter pipelines, `require_template_access` ancestry walk
4. Per-Job Pipeline Instance — create from template, stage CRUD, drag-to-reorder, template swap, reset-to-source
5. Stage Configuration — name, type, duration, difficulty, signal filter, pass criteria, advance behavior
6. Auto-Apply on Signal Confirmation — `auto_apply_pipeline_on_confirmation`, idempotent on `PipelineAlreadyExistsError`
7. API Reference — `/api/pipelines/*` (templates) + `/api/jobs/{id}/pipeline` (instance)
8. Frontend Architecture — `PipelineFlowColumn`, `PipelineFunnel` (stable key fix from `475df30`), `StageConfigDrawer` (focus management from `dd2f528`), `TemplatePickerDialog`, dnd-kit wiring with `KeyboardSensor`, auto-select effect refactor (`73adb68`)
9. Known Gaps
10. Cross-references

### `phase-2c2-implementation.md`

1. Architecture Overview — per-stage LLM call model, why not one-shot
2. Database Schema — `question_banks`, `questions`, `coverage_notes` column (migrations 0006, 0007)
3. Generation Flow — `generate_question_bank_stage` actor, bundling discipline, per-stage-type prompts
4. Adaptive Coverage — mandatory-fits-session validation, auto-demotion auto-correction, duration as session time limit (not generation budget), coverage notes audit trail
5. Read-Idempotent `list_banks` GET — returns placeholder entries for stages without banks, does NOT create drafts on poll (Batch G fix from `23e78bc`)
6. Bulk Load — `get_banks_for_pipeline` uses 4 constant queries instead of 1+2N
7. State Machine — `IllegalTransitionError`, `ReorderMismatchError`, failure commit scoping (fix from `1a0b847`)
8. Langfuse Wiring — `@observe` decorators on actors, trace metadata
9. SSE Progress Stream — `useQuestionsStatusStream` stability across stage changes (fix from `2dfa766`), routed through `get_tenant_session` (Batch F)
10. API Reference — `/api/jobs/{id}/banks/*` endpoints
11. Frontend Architecture — question bank panels, `QuestionEditForm` remount on `question.id` change (`2d16c2f`), SSE hook
12. Known Gaps
13. Cross-references

### `phase-hardening-implementation.md`

1. What This Covers — Batches D/F/G, motivated by Round 2 audit findings
2. RLS Runtime Role (Migration 0010) — the `postgres` `rolbypassrls=true` problem, `nexus_app` as the fix, `SET LOCAL ROLE` pattern, `DB_RUNTIME_ROLE` env var, commit `3e38981`
3. RLS NULLIF Cast (Migration 0011) — `SET LOCAL` + empty-string-GUC PG quirk, policy template update, commit `f6cd25e`
4. Phase 1 Full-Command Policies (Migration 0009) — the `FOR SELECT USING` trap, retrofit, commit `5414bf5`
5. Audit Log INSERT Fix (Migration 0008) — silently-dropped tenant writes
6. Policy Rename (Migration 0012) — `service_role_bypass` → `service_bypass`, commit `72689b9`
7. Startup RLS Completeness Check — `_assert_rls_completeness` in `app/main.py`, enumerated table list, skipped under `ENVIRONMENT=test` or when `DB_RUNTIME_ROLE` is unset, commit `bd83cf7`
8. JWT Hardening — ES256-only pinning (RS256 removed to close algorithm-confusion surface), `aud=authenticated` check, issuer check against `{supabase_url}/auth/v1`, commits `380fbf2` + `c79682d`
9. CORS-on-401 Fix — frontend couldn't read 401 error detail until CORS headers shipped on all error paths, commit `c79682d`
10. SSE RLS Fix — routing every poll through `get_tenant_session` in both `jd.sse` and `question_bank.sse`, commit `bd4b6bb`; SSE connection stability across stage changes, commits `2dfa766` + `4dc26b9`
11. Security Headers — both frontends: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`; dashboard adds `Permissions-Policy: camera=(self), microphone=(self), geolocation=()`; CSP deferred. Commit `f9dc628`.
12. Configurable `FRONTEND_BASE_URL` — removed `settings.debug` branching that sent staging invites to prod, commit `07cf0b6`
13. Correlation-ID Header Validation — `x-correlation-id` validated before propagating to logs, commit `5aa27ef`
14. Misc Fixes — query-key discipline (`1369b42`), a11y focus management on dialogs (`dd2f528`), stable keys in PipelineFunnel / EditableChipRow (`475df30`, `9dac616`), `confirm_signals` idempotency (`a7ba2ea`), question_bank failed-state commit scoping (`1a0b847`), same-origin redirect allowlist (`b23f6df`)
15. Remaining Pre-Phase-3 Hardening — candidate-JWT single-use enforcement (still a TODO in `middleware/auth.py`, must land before any Phase 3 session endpoint ships)
16. Cross-references — links back to each phase doc for where each fix lands

### `phase-2a-implementation.md` expansion

Insert these sections before the current content (which becomes Sections 10–12):

1. Architecture Overview
2. Database Schema — `job_postings`, `job_posting_signal_snapshots`, state machine transitions (initial Phase 2A migrations)
3. Auth & Permissions — new `jobs.view` permission, how it threads into system roles
4. JD State Machine — legal transitions, `IllegalTransitionError`, audit trail writes
5. Company Profile Gate — `find_company_profile_in_ancestry`, 422 response shape with `org_unit_id` for deep-linking
6. Call 1 Extraction — Dramatiq dispatch, `_run_extraction`, prompt ordering (company → JD → scope), provenance validators, retry + error mapping via `sanitize_error_for_user`
7. `app/ai/` Layer — `AIConfig`, `PromptLoader`, `get_openai_client()` factory, self-hosted Langfuse guard (`_is_langfuse_cloud_host`)
8. API Reference — 5 endpoints under `/api/jobs`
9. Frontend Architecture — jobs list, three-panel JD review shell, `SignalChip` with provenance tooltip, `OriginalJdPanel` / `EnrichedJdPanel` / `SignalsPanel`, `LoadingSkeleton` with SSE status pill, `ErrorBanner`, `useJobStatusStream` hook, TanStack Query wiring in `DashboardProviders`

Existing content (Sections 10–12): Data Flow, Troubleshooting, Known Gaps.

### `phase-1-implementation.md` amendments

New Section 11 added before the current "Known Gaps & Technical Debt":

- **11. Post-Phase-1 Amendments (Hardening Batches)**
  - One-paragraph summary: migrations 0008/0009/0010/0011/0012 touched Phase 1 tables. The canonical RLS pattern evolved — policies now use `NULLIF(current_setting(...), '')::uuid` and run under the `nexus_app` role, and the startup assertion in `app/main.py` verifies the pattern at boot.
  - Cross-link to `phase-hardening-implementation.md` for the full story.

Stale-line fixes (surgical):
- "Alembic is configured (`backend/nexus/migrations/env.py`) but `versions/` is empty" → "Alembic has 12 revisions (0001 through 0012) as of 2026-04-15"
- Any other drift found during the research pass

### `tech-stack.md` refresh

Add / update sections for:
- **Backend:** Dramatiq + Redis, `app/ai/` layer (instructor + langfuse.openai + OpenAI), `nexus_app` runtime role, Alembic head `0012_rename_service_role_bypass`
- **Frontend:** shadcn/ui v4 (Base UI, not Radix — note the gotchas from `frontend/app/CLAUDE.md`), TanStack Query v5 + devtools, React Hook Form + Zod, @dnd-kit (core + sortable + KeyboardSensor), @xyflow/react v12 + dagre, @microsoft/fetch-event-source, sonner, Vitest + testing-library/react + jsdom

## Research strategy

### Per phase

1. **Read the spec + plan** — `docs/superpowers/specs/<topic>-design.md` and `docs/superpowers/plans/<topic>.md`. Gives intended shape, not delivered shape.
2. **Read the migrations** — `backend/nexus/migrations/versions/*.py` for every migration in scope. Ground truth for schema and RLS.
3. **Read the module code in full** — `backend/nexus/app/modules/<module>/`: `router.py`, `service.py`, `schemas.py`, `authz.py`, `actors.py`, `state_machine.py`, `sse.py`, `errors.py`
4. **Read the AI layer where relevant** — `app/ai/{config,client,prompts,schemas}.py`
5. **Read the frontend surface** — `frontend/app/app/(dashboard)/<route>/`, `frontend/app/components/<area>/`, `frontend/app/lib/api/<module>.ts`, `frontend/app/lib/hooks/use-<thing>.ts`
6. **Git log the module** — `git log --oneline -- backend/nexus/app/modules/<module> frontend/app/components/<area>` to catch fixes not mentioned in CLAUDE.md

### For hardening specifically

- `app/main.py` — `_assert_rls_completeness`, startup, middleware wiring
- `app/modules/auth/service.py` — JWT verification (ES256 pinning, issuer check)
- `app/middleware/auth.py` — candidate JWT single-use TODO
- Migrations 0008–0012 in full
- Both frontend `next.config.ts` files for security headers
- Both frontend `proxy.ts` files for middleware auth
- Every commit from `380fbf2` (JWT tightening) onwards that touched RLS, SSE, auth, headers, or CORS — use `git log --oneline` scoped by path

### Verification pass

For each phase, dispatch a `feature-dev:code-explorer` (or general-purpose Explore) agent with a tightly-scoped question: "Trace execution of {X} through {module}. List every file touched, every DB query, every state transition, every error class raised, every frontend component rendered." This protects main context from hundreds of file reads and gives me a consolidated reality-check before writing.

The delegated agent result is a notes artifact, not prose. I synthesize it myself — never the agent — into the doc text. This is the "never delegate understanding" rule.

## Writing order

Load-bearing: the order is deliberate. Each step depends on context from the previous steps.

1. **Research + write `phase-2b-implementation.md`** (smallest new phase, fastest validation that the outline and depth match the user's expectations)
2. **Pause for user review** — confirm depth/style match. If the user wants the doc shorter, longer, differently organized, catch it here before sinking work into four more files. This is the only mid-flight checkpoint.
3. Research + write `phase-2c1-implementation.md`
4. Research + write `phase-2c2-implementation.md` (depends on 2C.1 context for pipelines/stages model)
5. Research + write `phase-hardening-implementation.md` (depends on understanding all earlier phases so I know what each fix touched)
6. Expand `phase-2a-implementation.md` (straightforward by this point — most context already loaded)
7. Amend `phase-1-implementation.md` (surgical: amendments section + stale-line fixes)
8. Refresh `tech-stack.md` (quick table updates)

## Style & conventions

- **Match `phase-1-implementation.md`.** It's the quality bar: table of contents at the top, numbered sections, tables for schemas / API reference / troubleshooting, code blocks for SQL / Python / TS snippets only where the snippet carries information the prose can't.
- **Document delivered code, not spec intent.** Where the delivered code diverges from the spec, call that out in the relevant section with a "Spec drift" sub-note.
- **Cross-reference specs and plans at the top of each file**, matching the existing `phase-2a-implementation.md` pattern.
- **No new commentary on architecture decisions** — that lives in the spec files and CLAUDE.md. The phase docs describe what shipped; they do not re-argue the design.
- **Known gaps sections** are factual (what's deferred, what's broken, what's a TODO). They are not a roadmap.
- **Commit hashes** are referenced inline where a specific fix is load-bearing for the narrative (e.g., "the idempotency fix from `a7ba2ea`"). Do not enumerate every commit in a section — only the ones that changed behavior materially.
- **Line budget** is guidance, not a ceiling. If Phase 2C.2 needs 1000 lines to tell the story properly, that's fine. If Phase 2B only needs 400, that's also fine.

## Risks

| Risk | Mitigation |
|---|---|
| Docs document the spec instead of the code (spec drift) | Verification pass via delegated exploration agent reads delivered code. Writer uses agent's notes, not the spec file, as the primary source. |
| Docs become a CLAUDE.md dump | Only include content that makes sense in linear prose form. If a fact belongs in a CLAUDE.md rule list, leave it there and cross-reference. |
| User wants different depth / organization than assumed | Mid-flight checkpoint after Phase 2B doc. If depth is wrong, we catch it after one file instead of five. |
| Research in main context blows up token budget | Delegate research to subagents per phase. Main context only holds the consolidated notes + the prose being written. |
| Docs duplicate each other (2B vs 2C.1 both mention the same auto-apply logic) | Cross-reference, don't duplicate. Each fact lives in one file and is linked from others. |
| Docs rot immediately because phase-hardening references specific commits | Commits are stable, no rot risk. The rot risk is if we reference "current behavior" of something that keeps changing — avoid that phrasing; describe behavior as of 2026-04-15. |

## Open questions

None as of 2026-04-15. The user has approved:
- Per-phase walkthrough structure (Option A)
- Phase-1-class depth (Option A)
- Cross-references to specs + plans at the top of each file
- The writing order with a pause after Phase 2B

## Out of scope (deferred)

- Evergreen module reference (`docs/modules/*.md`) — revisit if the phase-per-file pattern starts fragmenting
- Onboarding guide (`docs/onboarding.md`) — revisit post-Phase 3
- Gap/audit report — separate deliverable, not this refresh
- Mermaid / sequence diagrams — text and tables only for now; add diagrams only if a section genuinely needs one

---

## Cross-references

- `docs/phase-1-implementation.md` — existing style bar
- `docs/phase-2a-implementation.md` — existing thin walkthrough, to be expanded
- `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md`
- `docs/superpowers/specs/2026-04-10-phase-2b-signal-editing-design.md`
- `docs/superpowers/specs/2026-04-11-signal-schema-v2-job-metadata-design.md`
- `docs/superpowers/specs/2026-04-12-phase-2c1-pipeline-builder-design.md`
- `docs/superpowers/specs/2026-04-12-phase-2c2-question-generation-design.md`
- `docs/superpowers/plans/2026-04-09-phase-2a-implementation.md` … `2026-04-12-phase-2c2-question-generation.md`
- `backend/nexus/CLAUDE.md`, `frontend/app/CLAUDE.md`, `frontend/admin/CLAUDE.md` — current architectural rules
- `CLAUDE.md` (root) — product-level rules and load-bearing constraints
