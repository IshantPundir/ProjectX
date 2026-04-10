# Phase 2B — Signal Editing, Re-enrichment & Confirmation

> **Status:** Approved design — ready for implementation planning
> **Date:** 2026-04-10
> **Depends on:** Phase 2A (JD Pipeline & Signal Extraction) — shipped

---

## What This Phase Delivers

Phase 2A is a **read-only pipeline**: upload JD → Call 1 extracts signals → display read-only review with provenance chips. Phase 2B adds **editing, re-enrichment, and confirmation**:

1. **Signal chip editing** — recruiters can add, edit, and delete signal chips in the Signals Panel
2. **Re-enrichment (Call 2)** — regenerate the enriched JD prose to reflect edited signals
3. **Signal confirmation** — explicit recruiter sign-off on a snapshot before Phase 2C (question bank generation) can consume it

---

## Scope Boundaries

### In Scope
- Chip editing UI (hybrid toggle: read-only default, edit mode on toggle)
- Save signals as new snapshot version (instant, separate from re-enrichment)
- Call 2 re-enrichment via Dramatiq actor (full replace with progress indicator)
- Signal confirmation workflow (soft approval, auto-cleared on subsequent edit)
- State machine extension (+2 transitions)
- Alembic migration for new columns
- Vitest setup + frontend component tests
- Backend endpoint tests

### Out of Scope (Deferred)
- **Session configuration UI** — deferred to 2C (no dependency on editing)
- **Token streaming / Redis Streams** — deferred; can add as polish later, built properly in Phase 3
- **PDF/DOCX JD upload** — post-MVP
- **Zustand cross-route state** — editing buffer is scoped to a single job page

---

## Key Design Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Chip editing model | Hybrid toggle | Read-only default for reviewers (HMs); explicit "Edit Signals" toggle for recruiters with `jobs.manage` permission |
| 2 | Save vs re-enrich | Separate actions | Save is instant (new snapshot). Re-enrich is explicit button. Lets recruiter batch edits before spending tokens. |
| 3 | Confirmation model | Soft approval with auto-clear | `confirmed_by/at` set on confirm. Auto-cleared when recruiter saves new edits. 2C checks `confirmed_at IS NOT NULL`. |
| 4 | Re-enrichment delivery | Full replace with progress indicator | Dramatiq actor writes `description_enriched` to DB. Same pattern as Call 1. No streaming infrastructure. |
| 5 | State machine approach | Thin extension | Add `signals_confirmed` state + `enrichment_status` side-field. Keeps state machine small (6 states); transient enrichment state doesn't pollute the job lifecycle. |
| 6 | Frontend state | Zustand for edit buffer, TanStack Query for server state | Zustand holds the transient editing buffer (unsaved chip changes). TanStack Query remains source of truth for snapshots, job status, enriched JD. |
| 7 | Session config UI | Deferred to 2C | No dependency on editing; keeps 2B scope tight. |
| 8 | Vitest | Part of 2B | Set up alongside editing components; first truly interactive frontend surface justifies test infrastructure. |

---

## State Machine

### Current (Phase 2A — 4 states)

```
draft → signals_extracting
signals_extracting → signals_extracted | signals_extraction_failed
signals_extraction_failed → signals_extracting  (retry)
signals_extracted → (terminal in 2A)
```

### Phase 2B additions (+2 transitions, 1 new state)

```
signals_extracted → signals_confirmed       (recruiter confirms)
signals_confirmed → signals_extracted       (auto: recruiter edits chips post-confirmation)
```

Total: **5 unique states**, 6 transitions. `signals_confirmed` becomes the new terminal state for 2B. Phase 2C will later add `signals_confirmed → template_generating`.

### Enrichment status (side-field, not state machine)

New column `enrichment_status` on `job_postings`:

```
idle → streaming       (recruiter clicks "Re-enrich JD")
streaming → completed  (actor finishes successfully)
streaming → failed     (actor exhausts retries)
failed → streaming     (recruiter retries)
completed → streaming  (recruiter re-enriches again after more edits)
```

This tracks Call 2 independently from the job lifecycle. A job can be in `signals_extracted` with `enrichment_status = streaming` — that's the recruiter watching the center panel update.

---

## Data Model Changes

### Alembic Migration (one file)

**`job_postings` — new columns:**

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `enrichment_status` | `TEXT` | `'idle'` | Tracks Call 2 lifecycle: idle, streaming, completed, failed |
| `enrichment_error` | `TEXT` | `NULL` | Sanitized error message when enrichment_status = failed |

**`job_posting_signal_snapshots` — new column:**

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `prompt_version` | `TEXT` | `NULL` | Which prompt produced this snapshot (e.g. "v1"). Deferred from 2A. |

**No new tables.** Existing `job_postings` + `job_posting_signal_snapshots` + `audit_log` schema handles everything.

### Existing columns activated in 2B

| Table | Column | 2A state | 2B state |
|-------|--------|----------|----------|
| `job_postings` | `enriched_manually_edited` | Always `false` | Set to `true` when Call 2 completes |
| `job_posting_signal_snapshots` | `confirmed_by` | Always `NULL` | Set to `user.id` on confirm |
| `job_posting_signal_snapshots` | `confirmed_at` | Always `NULL` | Set to `now()` on confirm |

### Snapshot versioning

- Editing chips writes a new snapshot at `version = max(existing) + 1` (already fixed in 2A hardening)
- Each snapshot is immutable once written — editing creates a new version, never mutates
- `source = 'recruiter'` on manually added/edited chips (slot already exists in `SignalItem` schema)
- Confirmation is per-snapshot: `confirmed_by/at` are set on the latest snapshot row
- Saving a new snapshot after confirmation NULLs `confirmed_by/at` on the new snapshot (auto-clear)

---

## Backend API

### New endpoints (3)

#### `PATCH /api/jobs/{id}/signals` — Save edited signals

- **Body:** `{ required_skills, preferred_skills, must_haves, good_to_haves, min_experience_years, seniority_level, role_summary }`
- Each skill item: `{ value: str, source: "ai_extracted" | "ai_inferred" | "recruiter", inference_basis: str | null }`
- Validates via Pydantic (`SignalItem` schema, already allows `source = "recruiter"`)
- Writes new snapshot at `version = max + 1`
- If job was `signals_confirmed` → auto-transitions back to `signals_extracted` + clears `confirmed_by/at`
- **Returns:** New snapshot
- **Permission:** `jobs.manage` in ancestry

#### `POST /api/jobs/{id}/signals/confirm` — Confirm current snapshot

- **Body:** None
- Sets `confirmed_by = caller.id`, `confirmed_at = now()` on the latest snapshot
- Transitions job `signals_extracted → signals_confirmed`
- **Returns:** Updated job summary
- **Permission:** `jobs.manage` in ancestry

#### `POST /api/jobs/{id}/enrich` — Trigger re-enrichment

- **Body:** None — uses the latest snapshot's signals as input to Call 2
- Requires `enrichment_status` is `idle` or `completed` or `failed` (rejects if `streaming` → 409)
- Sets `enrichment_status = streaming`
- Enqueues `reenrich_jd` Dramatiq actor via `_safe_dispatch_extraction` pattern (handles Redis failure)
- **Returns:** `202 Accepted`
- **Permission:** `jobs.manage` in ancestry

### Existing endpoint changes

#### `GET /api/jobs/{id}` — response additions

`JobPostingWithSnapshot` response adds:
- `enrichment_status: str` (idle | streaming | completed | failed)
- `enrichment_error: str | null`
- `is_confirmed: bool` (derived: `latest_snapshot.confirmed_at IS NOT NULL`)

#### `GET /api/jobs/{id}/status/stream` — SSE additions

`JobStatusEvent` adds:
- `enrichment_status: str`
- `is_confirmed: bool`

The existing SSE polling pattern picks up enrichment_status changes — no new SSE endpoint needed.

---

## Call 2 — Re-enrichment Actor

### Actor: `reenrich_jd`

Same infrastructure pattern as Call 1 (`extract_and_enhance_jd`):
- Dramatiq actor with `@observe()` Langfuse tracing
- `get_bypass_session()` + `SET LOCAL app.current_tenant`
- `max_retries=1` (user-initiated action, fast feedback preferred)
- Permanent vs transient error classification (same `_PERMANENT_EXCEPTIONS` tuple)
- `flush_langfuse` via `asyncio.to_thread` in finally block

### Prompt: `prompts/v1/jd_reenrichment.txt`

**Input context (context-before-document ordering):**

1. Company profile (4 fields)
2. Original raw JD
3. Current enriched JD (from Call 1 or previous Call 2)
4. Current signal snapshot (the recruiter's edited version)
5. Delta summary: what changed (chips added/removed/edited vs prior snapshot)

**Output:** A single `enriched_jd` string — the full regenerated prose JD.

**Prompt instructions:**
- Preserve tone and structure of existing enriched JD where signals haven't changed
- Integrate new/modified signals naturally into the prose
- Don't invent new signals — only reflect what's in the snapshot
- If a signal was removed, remove the corresponding prose

### Actor lifecycle

```
POST /api/jobs/{id}/enrich
  → sets enrichment_status = 'streaming'
  → enqueues reenrich_jd actor (via _safe_dispatch_extraction pattern)
  → returns 202

reenrich_jd actor:
  → reads latest snapshot + company profile + raw JD + current enriched JD
  → calls OpenAI (non-streaming, full response)
  → writes new description_enriched to DB
  → sets enrichment_status = 'completed', enriched_manually_edited = true
  → on error: sets enrichment_status = 'failed', enrichment_error = sanitized message
```

---

## Frontend Architecture

### State management

**Zustand store** (`stores/job-edit.ts`):
- `isEditing: boolean` — toggle state
- `draftSignals: SnapshotData` — working copy of chips being edited
- `isDirty: boolean` — unsaved changes flag
- Initialized from latest snapshot on "Edit Signals" click
- Cleared on successful save or discard

**TanStack Query** remains source of truth for:
- Job data (`useJob` hook)
- Job status stream (`useJobStatusStream` hook)
- Snapshot data (fetched via `GET /api/jobs/{id}`)

### New components

| Component | Location | Purpose |
|-----------|----------|---------|
| `EditableSignalsPanel.tsx` | `components/dashboard/jd-panels/` | Edit-mode panel — chip CRUD, add inputs, textarea for role summary, form fields for experience/seniority |
| `SignalsPanelWrapper.tsx` | `components/dashboard/jd-panels/` | Orchestrates view↔edit toggle. Renders `SignalsPanel` or `EditableSignalsPanel` based on Zustand `isEditing`. Only shows "Edit Signals" toggle for users with `jobs.manage`. |
| `ConfirmBar.tsx` | `components/dashboard/jd-panels/` | Bottom bar: "Confirm Signals" (view mode, green) or "Save Signals" (edit mode, blue) |
| `StaleBanner.tsx` | `components/dashboard/jd-panels/` | Amber banner on center panel: "Signals were updated since this JD was generated" + "Re-enrich JD" button |

### New hooks

| Hook | Location | Purpose |
|------|----------|---------|
| `use-save-signals.ts` | `lib/hooks/` | TanStack mutation for `PATCH /api/jobs/{id}/signals` |
| `use-confirm-signals.ts` | `lib/hooks/` | TanStack mutation for `POST /api/jobs/{id}/signals/confirm` |
| `use-trigger-enrich.ts` | `lib/hooks/` | TanStack mutation for `POST /api/jobs/{id}/enrich` |

### Updated page (`/jobs/[jobId]/page.tsx`)

- Replaces `SignalsPanel` with `SignalsPanelWrapper`
- Adds `StaleBanner` to `EnrichedJdPanel` when signals are newer than last enrichment
- `EnrichedJdPanel` shows progress pill when `enrichment_status = streaming`
- "Re-enrich JD" button in center panel header triggers `use-trigger-enrich` mutation

### Signals Panel UX

**View mode (default):**
- Read-only chips with provenance colors (blue=extracted, amber=inferred, green=recruiter)
- "Edit Signals" button in header (only for users with `jobs.manage`)
- "Confirm Signals" green button at bottom
- After confirmation: green "Confirmed" badge replaces confirm button

**Edit mode (toggled):**
- Chips get ✕ delete buttons
- "+ Add" input field per section (required_skills, preferred_skills, must_haves, good_to_haves)
- New chips appear in green (`source: recruiter`)
- Role summary becomes textarea
- Experience → number input, Seniority → select dropdown
- "Save Signals" blue button at bottom
- "Done Editing" in header — confirms discard if dirty

### Center panel UX flow

1. **Signals saved** → amber "stale" banner: *"Signals were updated since this JD was generated."* + "Re-enrich JD" button
2. **Click "Re-enrich JD"** → button disabled, blue progress pill: *"Re-enriching based on updated signals..."*
3. **Re-enrichment completes** → banner disappears, fresh enriched JD displayed
4. **Re-enrichment fails** → red error banner with "Retry" button, previous enriched JD preserved

---

## Permissions

All new endpoints use existing permission infrastructure:

| Endpoint | Permission | Enforcement |
|----------|------------|-------------|
| `PATCH /api/jobs/{id}/signals` | `jobs.manage` | `require_job_access(db, job_id, user, "manage")` |
| `POST /api/jobs/{id}/signals/confirm` | `jobs.manage` | `require_job_access(db, job_id, user, "manage")` |
| `POST /api/jobs/{id}/enrich` | `jobs.manage` | `require_job_access(db, job_id, user, "manage")` |

Frontend "Edit Signals" toggle is only rendered for users with `jobs.manage` in the job's org unit ancestry. Hiring Managers with only `jobs.view` see the read-only panel with no toggle.

---

## Testing

### Vitest setup (frontend)

- Install: `vitest`, `@testing-library/react`, `@testing-library/user-event`, `jsdom`
- Config: `vitest.config.ts` with `jsdom` environment and path aliases matching `tsconfig.json`
- Script: `npm run test` added to `package.json`

### Frontend tests

| Test | What it validates |
|------|-------------------|
| `EditableSignalsPanel` | Add chip, remove chip, edit role summary, save triggers mutation |
| `SignalsPanelWrapper` | Toggle view↔edit, dirty state confirmation dialog on discard |
| `StaleBanner` | Renders when signals newer than enrichment, hides after re-enrich completes |

### Backend tests

| Test | What it validates |
|------|-------------------|
| `test_save_signals` | PATCH creates new snapshot version with correct data |
| `test_save_signals_clears_confirmation` | Save after confirm clears confirmed_by/at, transitions signals_confirmed → signals_extracted |
| `test_confirm_signals` | POST sets confirmed_by/at, transitions signals_extracted → signals_confirmed |
| `test_confirm_requires_extracted_state` | 409 if job not in signals_extracted |
| `test_reenrich_enqueues_actor` | POST /enrich sets enrichment_status=streaming, returns 202 |
| `test_reenrich_rejects_while_streaming` | 409 if enrichment_status=streaming |
| `test_reenrich_actor_writes_enriched_jd` | Actor writes description_enriched, sets enrichment_status=completed |
| `test_reenrich_actor_failure` | Actor sets enrichment_status=failed, enrichment_error=sanitized message |

---

## Non-Goals (explicitly deferred)

- **Token streaming / Redis Streams** — can add as polish later; built properly in Phase 3 for live sessions
- **Session configuration UI** — deferred to 2C (consumed by question bank generation)
- **PDF/DOCX upload** — post-MVP
- **Cross-route Zustand state** — editing buffer scoped to single job page
- **Chip drag-and-drop reordering** — not needed for signal accuracy
- **Diff view between snapshot versions** — useful but not essential for 2B
