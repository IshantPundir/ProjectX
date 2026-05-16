# Job activation gate — single source of truth, consistent vocabulary

**Date:** 2026-05-15
**Status:** Draft for later implementation
**Scope:** Backend — `app/modules/jd/`, `app/modules/pipelines/`. Frontend — `frontend/app/app/(dashboard)/jobs/[jobId]/`, `frontend/app/app/(dashboard)/jobs/page.tsx`, `frontend/app/app/(dashboard)/tracker/`, `frontend/app/components/dashboard/`, `frontend/app/lib/pipelines/activation.ts`, `frontend/app/lib/hooks/use-activate-job.ts`.
**Amends:** None. Builds on `2026-05-14-unified-job-creation-flow-design.md` (lifecycle states are unchanged; vocabulary + activation gates are tightened).
**Supersedes:** None outright. Retires the unwired `auto_apply_pipeline_on_confirmation` template-resolution path.

---

## TL;DR

Three things wrong with the job-status surface today:

1. **The "is this job live?" question has four different answers** across four UI surfaces for the same `status='pipeline_built'` row — JD detail shows "Almost ready", JD canvas (same page) shows "live · accepting candidates", Jobs list shows "live", Tracker shows "pipeline ready" *on a page titled "Live boards"*. The vocabulary drifted; the page-level layout and an inner canvas component both render their own status chip, and they disagree.
2. **The Activate button silently fails.** The mutation has no `onError` handler. Server returns 422 on `activation_predicates_failed` → mutation enters error state → button text flips back from "Activating…" to "Activate" → no toast, no inline error, nothing.
3. **The lifecycle has a phantom state.** `signals_confirmed` is documented in code (`layout.tsx:68-71`) as "transient — auto-transitions to `pipeline_built` when the pipeline is applied". The function that should fire that transition (`auto_apply_pipeline_on_confirmation`, `pipelines/service.py:1116-1200`) is never called from anywhere. So `signals_confirmed` is in fact the steady state until the recruiter manually creates a pipeline.

The fix is three coordinated changes:

- **Backend:** wire `ensure_minimal_pipeline_for_job` into `confirm_signals` so the pipeline (Intake + Debrief) is created atomically with signal confirmation and the job advances to `pipeline_built` in the same transaction. Tighten the activation gate so a stage's question bank must be in `confirmed` status (not `reviewing`). Delete the dead `auto_apply_pipeline_on_confirmation` template-resolution function plus its tests.
- **Frontend status vocabulary:** one canonical label per `JobStatus`, applied uniformly across every chip-rendering surface. Remove the duplicate chip on `CanvasHeader`. Mirror the tightened activation gate in `lib/pipelines/activation.ts`.
- **Frontend activation banner:** wire `onError` toast on `useActivateJob`. Hide the Activate button when failures or in-flight generation block activation — render an informational pill in its place. Show the green button only when the gate is actually open.

After these changes, the lifecycle a recruiter sees is:

```
Draft → Reading JD → Review signals → In review → Live
```

with "In review" covering both the (transient) `signals_confirmed` and the (steady-state) `pipeline_built`, and "Live" reserved for `active`.

**Codebase posture:** pure development stage, no live tenants. One PR. No feature flags. No backward-compatibility shims. The handful of existing `signals_confirmed` rows without a pipeline get a one-shot data migration that creates the minimal pipeline and advances them.

---

## Motivation

### Problem 1 — four labels for the same row

For a job in `status='pipeline_built'` today, the four UI surfaces tell different stories:

| Surface | File:line | Label |
|---|---|---|
| Job page header (every tab) | `frontend/app/app/(dashboard)/jobs/[jobId]/layout.tsx:72-79` | "Almost ready" (soft gray) |
| JD canvas (inside `?tab=jd`) | `frontend/app/components/dashboard/jd-panels/components/CanvasHeader.tsx:50-55` | "live · accepting candidates" (green) |
| Jobs list page | `frontend/app/app/(dashboard)/jobs/page.tsx:46-53` | "live" (green) — `pipeline_built` falls through to default branch |
| Tracker landing | `frontend/app/lib/hooks/use-tracker-jobs.ts:7-16` calls them `LIVE_STATUSES`; header reads "Live boards"; card pill reads "pipeline ready" (blue) | "Live boards" / "pipeline ready" |

The CanvasHeader chip reads `is_confirmed` (snapshot.confirmed_at) instead of `job.status`. For `pipeline_built`, `is_confirmed` is *always* true (you cannot enter `pipeline_built` without first transitioning through `signals_confirmed`, which sets confirmed_at). So `pipeline_built` always produces the green "live · accepting candidates" chip in the canvas — directly contradicting the layout's gray "Almost ready" chip on the same page.

### Problem 2 — silent activation failure

`frontend/app/lib/hooks/use-activate-job.ts:8-20` has no `onError`. The backend `POST /jobs/{id}/activate` (`backend/nexus/app/modules/jd/router.py:767-810`) returns 422 with `code: "activation_predicates_failed"` when server-side predicates fail. The client mirror in `frontend/app/lib/pipelines/activation.ts` can drift from the server's check (e.g. a bank's state changed between the last `useBanksOverview` refetch and the click). When that happens, the button is enabled on the client, the POST returns 422, the mutation goes to error state, the button text flips back — and the user sees nothing.

This is the most likely root cause of "I click Activate and nothing happens". The other root cause is "the button is disabled at 40% opacity and the user didn't notice" — addressed by Problem 3's banner redesign.

### Problem 3 — the auto-apply hook is dead code

`pipelines/__init__.py:4` documents: *"Called from `jd.confirm_signals()` via `auto_apply_pipeline_on_confirmation()`"*. The function exists (`pipelines/service.py:1116-1200`) and resolves the pipeline via a template chain (last-used → org default → system starter). **No code ever calls it.** `confirm_signals` (`jd/service.py:550-593`) just transitions `signals_extracted → signals_confirmed` and returns.

`layout.tsx:68-71` then collapses `signals_confirmed` and `pipeline_built` into one "Almost ready" chip on the rationale that `signals_confirmed` is "a transient state". It isn't — without the hook, `signals_confirmed` is steady state until the recruiter manually `POST /api/jobs/{id}/pipeline`.

Two ways out of this. We could wire the existing template-resolution function and accept "the recruiter's first job auto-applies the system fallback starter, subsequent jobs auto-apply the last-used template". Or we can use the simpler `ensure_minimal_pipeline_for_job` helper that already exists (`pipelines/service.py:1052-1108`) and just creates Intake → Debrief — letting the recruiter add the middle stage(s) themselves, which is what the activation gate already requires via the `no_middle_stage` predicate.

The simpler path matches the user's expressed intent: *"we auto creates a pipeline with intake & debrief, but in order to make it go live, it must have one middle stage as well."* It also makes the failure mode obvious — a freshly-confirmed job lands in `pipeline_built` with a visible "Add a screening stage" predicate failure, exactly where the recruiter's next action is.

The template-resolution path is deleted, not kept-but-unwired. Carrying functions that the codebase claims-but-doesn't-use rots the docs and confuses future readers.

---

## Goals

- **One canonical label per `JobStatus`** across the recruiter dashboard. The layout chip is the source of truth; other surfaces (jobs list pill, tracker card pill, tracker filter chips) read from the same vocabulary table.
- **Atomic confirm-and-build.** `confirm_signals` creates the minimal pipeline and advances to `pipeline_built` in the same DB transaction. `signals_confirmed` becomes truly transient — observable only inside the transaction.
- **Strict activation gate.** A stage's question bank must be in `confirmed` status for activation. `reviewing` is the post-generation pre-approval state — the recruiter has not yet clicked "Confirm bank", so the bank is not ready. Server and client predicates agree on this.
- **No silent activate failures.** Every error path on `POST /jobs/{id}/activate` surfaces a toast. The activation banner shows the predicate-failure list as before; the toast confirms the click was acknowledged.
- **Disabled-button affordance.** When the gate is not open, the Activate button is hidden and replaced by a non-interactive informational pill ("Fix 3 items above to activate" / "Generating questions…"). The button only renders when clicking it would succeed.
- **Dead code deleted.** `auto_apply_pipeline_on_confirmation` and its test file are removed. Module docstrings stop referencing the unwired hook.

## Non-goals

- **A new `JobStatus` value.** The state machine stays at the 8 values defined in `backend/nexus/app/modules/jd/state_machine.py:24-33`. No migration alters the CHECK constraint.
- **Reworking the activation predicate codes.** The predicate `code` strings (`no_middle_stage`, `missing_bank`, `missing_interviewer`, `missing_reviewer`, `empty_stage_name`, `positions_not_sequential`) stay as-is. Only the `missing_bank` predicate's *message* gains a "Confirm" variant when the bank exists in `reviewing` (vs the existing "Generate" message when no bank exists at all).
- **Auto-confirming question banks.** The recruiter's explicit "Confirm bank" click stays required. Auto-confirming would defeat the point of the recruiter-approval gate. This spec only changes the activation predicate to *require* `confirmed` instead of accepting either `reviewing` or `confirmed`.
- **Auto-adding a middle stage.** The auto-applied pipeline is bookends only (Intake + Debrief). The recruiter must add at least one middle stage. The activation gate's `no_middle_stage` predicate enforces this — already implemented, no change.
- **Updating the candidate session app.** `frontend/session/` does not render job status. No changes there.
- **Migrating Tracker page UX.** The `2026-05-15-tracker-page-design.md` spec defines the tracker. This spec only updates the labels that the tracker renders (subhead, filter chips, card pill) — the layout and behavior are unchanged.
- **A `redirect-to-pipeline` rework.** The redirect in `app/(dashboard)/jobs/[jobId]/page.tsx:29-39` (status ≥ `signals_confirmed` and `?tab` not set → push to `/pipeline`) stays as-is. The auto-apply makes `signals_confirmed` transient, so the redirect lands on `pipeline_built` jobs only — which is the intended behavior.

---

## Background — current state

### The lifecycle (unchanged by this spec)

`backend/nexus/app/modules/jd/state_machine.py:24-33`:

```python
LEGAL_TRANSITIONS = {
    "draft":                        {"signals_extracting"},
    "signals_extracting":           {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed":    {"signals_extracting"},   # retry
    "signals_extracted":            {"signals_confirmed"},
    "signals_confirmed":            {"signals_extracted", "pipeline_built"},
    "pipeline_built":               {"active"},
    "active":                       set(),                     # terminal
    "archived":                     set(),                     # terminal
}
```

Enforced by:
- DB CHECK constraint (`migrations/versions/0018_pipeline_versioning_and_pause.py:78-88`)
- The state machine dict above
- Pydantic schema in `backend/nexus/app/modules/jd/schemas.py:26-31`

### Where status is mutated

| Endpoint | Service call | Transition |
|---|---|---|
| `POST /jobs/{id}/extract-signals` | `extract_and_enhance_jd` actor | `draft → signals_extracting → signals_extracted` |
| `POST /jobs/{id}/signals/confirm` | `confirm_signals` | `signals_extracted → signals_confirmed` |
| `POST /jobs/{id}/pipeline` | `create_job_pipeline_*` | `signals_confirmed → pipeline_built` |
| `POST /jobs/{id}/activate` | `activate_job` | `pipeline_built → active` |

### Where status is read

- `GET /api/jobs/{id}` (`jd/router.py:415-440`) — raw `job.status` + derived `is_confirmed`.
- `GET /api/jobs` (`jd/router.py:394-412`) — raw `job.status` for filtering.
- `GET /api/jobs/{id}/status/stream` (`jd/router.py:494-509` + `jd/sse.py:63-193`) — SSE stream of `(status, enrichment_status)` pair.
- `GET /api/jobs/{id}/candidates/kanban` (`candidates/service.py:507-607`) — does NOT read `job.status`; derives from `JobPipelineInstance` + `JobPipelineStage` rows. Gated on `job.status='active'` for the assignment-write paths (`candidates/service.py:302`).

### The activation gate (today)

`backend/nexus/app/modules/jd/service.py:626-748` (`evaluate_activation_predicates`):

| # | Code | Condition |
|---|---|---|
| 1 | `no_pipeline` | No `JobPipelineInstance` exists |
| 2 | `no_middle_stage` | Pipeline has zero stages whose type is in `{phone_screen, ai_screening, human_interview}` |
| 3 | `missing_interviewer` | A human-led stage (`phone_screen`, `human_interview`) has no participant with `role='interviewer'` |
| 4 | `missing_reviewer` | `debrief` stage has no participant with `role='reviewer'` |
| 5 | `missing_bank` | A bank-eligible stage's `StageQuestionBank.status` is not in `('reviewing', 'confirmed')` |
| 6 | `empty_stage_name` | A stage's `name` is null or whitespace-only |
| 7 | `positions_not_sequential` | Stage positions aren't `0..N-1` |

The client mirror (`frontend/app/lib/pipelines/activation.ts:10-73`) implements the same predicates *except* predicate 1 and 7 (those are server-only — the client already has the pipeline loaded if it's running this code) and using the loaded pipeline + bank data already in the React Query cache.

### Dead code

`backend/nexus/app/modules/pipelines/service.py:1116-1200` — `auto_apply_pipeline_on_confirmation`. Function exists; never called. Tests at `backend/nexus/tests/test_pipelines_auto_apply.py` (318 lines) verify the resolution chain.

Three references to be cleaned up:
- `backend/nexus/app/modules/pipelines/__init__.py:4` — docstring mentions it
- `backend/nexus/app/modules/pipelines/__init__.py:22,32` — imports + `__all__`
- `backend/nexus/app/modules/pipelines/service.py:4` — module docstring mentions it
- `backend/nexus/app/modules/pipelines/starter_pack.py:6` — comment mentions it

---

## Design

### 1. Canonical status vocabulary

Single source of truth — every chip-rendering surface reads from this table:

| `JobStatus` | Label | Tone | Rationale |
|---|---|---|---|
| `draft` | "Draft" | soft (gray) | Pre-extraction; recruiter is still composing |
| `signals_extracting` | "Reading JD" | accent pulse | Phase-2 actor is running |
| `signals_extraction_failed` | "Extraction failed" | danger (red) | Phase-2 actor errored; recruiter can retry |
| `signals_extracted` | "Review signals" | caution (amber) | Signals returned; recruiter needs to confirm or edit |
| `signals_confirmed` | "In review" | soft (gray) | Transient under the new design (auto-advances to `pipeline_built`); shown only if observed mid-transaction |
| `pipeline_built` | "In review" | soft (gray) | Pipeline exists; recruiter is finalizing staffing + banks. **Not** "live". |
| `active` | "Live · accepting candidates" | ok (green) | Activated; ready to onboard candidates. The only state that says "Live". |
| `archived` | "Archived" | soft (gray) | Terminal |

**Color story:** gray = work in flight, amber = needs your attention, green = running, red = error.

The chip lives in `frontend/app/app/(dashboard)/jobs/[jobId]/layout.tsx::JobStatusChips`. Other surfaces:

- **Jobs list (`app/(dashboard)/jobs/page.tsx`)**: `statusKind()` (lines 46-53) maps each `JobStatus` to a `StatusKind`. Replace the existing 5-value `StatusKind` with one entry per canonical label:
  - `'draft'` → `'draft'`
  - `'signals_extracting'` → `'reading'`
  - `'signals_extracted'` → `'review_signals'`
  - `'signals_extraction_failed'` → `'failed'`
  - `'signals_confirmed' | 'pipeline_built'` → `'in_review'`
  - `'active'` → `'live'`
  - `'archived'` → `'archived'`
  - `profile_ready=false` (any status) → `'blocked'` (existing behavior, kept)
- **Tracker landing (`app/(dashboard)/tracker/ClientTrackerLandingPage.tsx`)**:
  - Subhead: "Live boards. Pick a role to see candidates and move them through stages." → "Roles in flight. Pick a role to see candidates and move them through stages."
  - Filter chips: "All / Active / In setup" → "All / Live / In review"
  - Filter logic unchanged (still filters by `active` for "Live", `signals_confirmed | pipeline_built` for "In review").
- **Tracker card (`components/dashboard/tracker/TrackerJobCard.tsx`)**: `statusPillStyle()` (lines 25-31):
  - `'active'` → `{ label: 'Live', bg, fg: emerald }`
  - default (`pipeline_built`, `signals_confirmed`) → `{ label: 'In review', bg, fg: gray }` (not blue)
- **`lib/hooks/use-tracker-jobs.ts`**: rename `LIVE_STATUSES` → `IN_FLIGHT_STATUSES` and update the docstring. The set membership stays the same — tracker still shows `signals_confirmed | pipeline_built | active`.

### 2. Remove the duplicate canvas chip

`frontend/app/components/dashboard/jd-panels/components/CanvasHeader.tsx:50-73` renders a chip based on `isConfirmed`. Delete that block entirely — the layout chip above the canvas already carries the canonical label. Keep the title ("What we found"), the meta line (org / location / comp / seniority), and the props plumbing (`needsReviewCount` is still used by the SignalsCanvas headline counter — verify before removing the prop). The chip rendering is the only thing deleted.

This eliminates the "two chips disagree on the same page" failure mode at the source.

### 3. Atomic confirm-and-build in `confirm_signals`

`backend/nexus/app/modules/jd/service.py::confirm_signals` (current: lines 550-593) becomes:

```python
async def confirm_signals(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
    correlation_id: str,
) -> JobPosting:
    """Confirm the latest snapshot, auto-create the bookend Intake → Debrief
    pipeline, and transition the job to pipeline_built — all in one
    transaction.

    signals_confirmed is therefore transient: observable inside this
    transaction but never as steady state. The recruiter lands directly
    on pipeline_built ("In review") with one click. They must add a
    middle stage and confirm each stage's question bank before the
    activation gate opens.

    The minimal pipeline creation is idempotent (no-ops if a pipeline
    already exists), making this safe under any race the state machine
    might allow.
    """
    snap_result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(JobPostingSignalSnapshot.job_posting_id == job.id)
        .order_by(desc(JobPostingSignalSnapshot.version))
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    if snapshot is None:
        raise ValueError("No snapshot to confirm")

    snapshot.confirmed_by = actor_id
    snapshot.confirmed_at = datetime.now(UTC)
    job.updated_by = actor_id

    await transition(
        db, job,
        to_state="signals_confirmed",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    await db.flush()

    await ensure_minimal_pipeline_for_job(db, job=job)

    await transition(
        db, job,
        to_state="pipeline_built",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    await db.flush()

    logger.info(
        "jd.service.signals_confirmed_and_pipeline_built",
        job_posting_id=str(job.id),
        snapshot_version=snapshot.version,
        correlation_id=correlation_id,
    )
    return job
```

Add `ensure_minimal_pipeline_for_job` to the existing import block at the top of `jd/service.py`:

```python
from app.modules.pipelines import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    bank_eligible_stage_types,
    ensure_minimal_pipeline_for_job,   # <- new
    human_led_stage_types,
    middle_stage_types_for_activation,
)
```

The `_TENANT_SCOPED_TABLES` startup RLS check is unaffected. `transition()` already writes the audit row, so the two transitions land two `job_posting.status_changed` audit events under the same correlation_id — recoverable history for the (now-atomic) confirm flow.

### 4. Tighten the activation gate — bank must be `confirmed`

`backend/nexus/app/modules/jd/service.py:724-736` currently accepts `reviewing` OR `confirmed`. Tighten to `confirmed` only, with a precise message for each failure shape:

```python
if s.stage_type in bank_types:
    bank = banks_by_stage.get(s.id)
    if bank is None:
        failures.append(ActivationPredicateFailure(
            code="missing_bank",
            message=f"Generate a question bank for '{s.name}'.",
            stage_id=s.id,
        ))
    elif bank.status != "confirmed":
        failures.append(ActivationPredicateFailure(
            code="missing_bank",
            message=f"Confirm the question bank for '{s.name}'.",
            stage_id=s.id,
        ))
```

Same `code` (`missing_bank`) so the frontend's in-flight-generation suppression check still works (`JobActivationBanner.tsx:135-143`: `f.code === 'missing_bank' && generatingSet.has(f.stage_id)` → suppress while bank is generating). Different message based on state — the recruiter sees "Generate" if no bank exists, "Confirm" if a bank exists but isn't approved.

### 5. Mirror the tightened gate on the client

`frontend/app/lib/pipelines/activation.ts:57-69` becomes:

```ts
if (BANK_ELIGIBLE_TYPES.has(s.stage_type)) {
  const bank = banksByStage[s.id]
  if (!bank) {
    failures.push({
      code: 'missing_bank',
      message: `Generate a question bank for '${s.name}'.`,
      stage_id: s.id,
    })
  } else if (bank.status !== 'confirmed') {
    failures.push({
      code: 'missing_bank',
      message: `Confirm the question bank for '${s.name}'.`,
      stage_id: s.id,
    })
  }
}
```

Server is the source of truth; the client mirror exists for instant feedback (no roundtrip). Keeping them aligned prevents the "button enabled, POST returns 422" silent-failure path.

### 6. Wire `onError` toast on `useActivateJob`

`frontend/app/lib/hooks/use-activate-job.ts`:

```ts
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/lib/api/client'
import { pipelinesApi } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useActivateJob(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.activate(token, jobId)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs', jobId] })
      qc.invalidateQueries({ queryKey: ['jobs-list'] })
      toast.success('Job activated — ready to onboard candidates.')
    },
    onError: (err) => {
      // 422 means the server's predicate check failed. The banner
      // already shows the full failure list above the button; the
      // toast confirms the click was acknowledged so the user doesn't
      // think nothing happened. Other errors get a generic message
      // with the underlying error text.
      if (err instanceof ApiError && err.status === 422) {
        toast.error('Fix the items above before activating.')
        return
      }
      toast.error(
        err instanceof Error
          ? `Activation failed: ${err.message}`
          : 'Activation failed. Please try again.',
      )
    },
  })
}
```

Success toast is also added — the existing `onSuccess` only invalidates caches, which is silent. A success toast closes the loop.

### 7. Activation banner — hide the button when blocked

`frontend/app/components/dashboard/job/JobActivationBanner.tsx::PipelineReviewBanner` currently renders the Activate button at 40% opacity with `cursor: not-allowed` when blocked. Replace that with conditional rendering: only show the button when `visibleReady` is true. When blocked, render an informational pill in its slot.

Pseudocode for the right-side button slot:

```tsx
{isGenerating ? (
  <span className="rounded bg-sky-100 px-4 py-2 text-sm font-medium text-sky-900">
    Generating questions…
  </span>
) : visibleReady ? (
  <button
    type="button"
    disabled={activating}
    onClick={onActivate}
    className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
  >
    {activating ? 'Activating…' : 'Activate'}
  </button>
) : (
  <span className="rounded bg-amber-100 px-4 py-2 text-sm font-medium text-amber-900">
    {visibleFailures.length} to fix
  </span>
)}
```

The bulleted failure list above the slot is unchanged — that's where the recruiter sees *what* to fix. The right-side slot just signals *is this clickable*. When the gate is open and the recruiter clicks, success or `onError` toast both fire — no silent failure.

Behavior summary:

| State | Headline | Right slot |
|---|---|---|
| Failures exist (and no generation) | "⚠ N things needed before you can activate this job:" + list | "N to fix" pill (amber) |
| Generation in flight | "⏳ Generating questions for N stages…" | "Generating questions…" pill (sky) |
| Ready | "✓ Ready to activate this job…" | "Activate" button (emerald) |

### 8. Delete the dead auto-apply path

- Remove `auto_apply_pipeline_on_confirmation` from `backend/nexus/app/modules/pipelines/service.py` (current lines 1116-1200).
- Remove the import + `__all__` entry from `backend/nexus/app/modules/pipelines/__init__.py` (lines 22, 32). Update the module docstring (line 4) to point at `confirm_signals` calling `ensure_minimal_pipeline_for_job` directly.
- Update `backend/nexus/app/modules/pipelines/service.py:4` module docstring to drop the auto-apply reference.
- Update `backend/nexus/app/modules/pipelines/starter_pack.py:6` comment to drop the auto-apply reference (the `SYSTEM_FALLBACK_STARTER` constant becomes unused in code; keep the file for the starter-pack browser if it remains a user-facing concept, otherwise delete the constant too).
- Delete `backend/nexus/tests/test_pipelines_auto_apply.py` (318 lines).

If `SYSTEM_FALLBACK_STARTER` becomes truly unreferenced after this, delete it too in the same commit. (Verify via `grep -rn SYSTEM_FALLBACK_STARTER backend/` before deletion.)

### 9. Data migration for existing `signals_confirmed` rows

In the same PR, add a one-shot data migration:

```python
# migrations/versions/00XX_advance_signals_confirmed_to_pipeline_built.py
"""Backfill: every signals_confirmed job gets a bookend pipeline and advances."""

def upgrade() -> None:
    # 1) Insert a JobPipelineInstance for every signals_confirmed job that
    #    doesn't already have one.
    op.execute("""
        INSERT INTO job_pipeline_instances (id, tenant_id, job_posting_id, source_template_id, pipeline_version, created_at, updated_at)
        SELECT gen_random_uuid(), j.tenant_id, j.id, NULL, 1, NOW(), NOW()
        FROM job_postings j
        LEFT JOIN job_pipeline_instances i ON i.job_posting_id = j.id
        WHERE j.status = 'signals_confirmed'
          AND i.id IS NULL
    """)

    # 2) Insert Intake + Debrief stages for those new instances.
    #    (Hand-write the SQL — _seed_bookends is Python; for backfill we
    #    inline the same field values it produces.)
    op.execute("""
        INSERT INTO job_pipeline_stages (...)
        SELECT ...
        FROM job_pipeline_instances i
        JOIN job_postings j ON j.id = i.job_posting_id
        WHERE j.status = 'signals_confirmed'
          AND NOT EXISTS (
            SELECT 1 FROM job_pipeline_stages WHERE instance_id = i.id
          )
    """)

    # 3) Advance every signals_confirmed job to pipeline_built.
    op.execute("UPDATE job_postings SET status = 'pipeline_built' WHERE status = 'signals_confirmed'")

def downgrade() -> None:
    # No-op. Reverting status would require dropping the bookend stages,
    # which is destructive. If we need to revert, do it manually with
    # the recruiter in the loop.
    pass
```

Codebase posture is pre-production (no live tenants per [[user_solo_dev]]), so the migration has minimal blast radius. The exact stage column values come from `_seed_bookends` in `pipelines/service.py` — write them out by hand in the SQL or call into Python in the migration's `op.bulk_insert` form.

If `job_postings.status = 'signals_confirmed'` is empty at migration time, all three statements are no-ops. Safe to run unconditionally.

---

## Implementation order (one PR)

1. **Backend service + tests**
   - `jd/service.py::confirm_signals` — atomic confirm-and-build.
   - `jd/service.py::evaluate_activation_predicates` — bank must be `confirmed`.
   - Add a unit test for `confirm_signals` proving: after one call, `job.status == 'pipeline_built'`, a `JobPipelineInstance` exists for the job, and that instance has exactly 2 stages (`intake` + `debrief`).
   - Update any existing test that asserted `status == 'signals_confirmed'` after `confirm_signals` (grep for `'signals_confirmed'` in `backend/nexus/tests/`).
2. **Backend cleanup**
   - Delete `auto_apply_pipeline_on_confirmation` from `pipelines/service.py`.
   - Delete `tests/test_pipelines_auto_apply.py`.
   - Update `pipelines/__init__.py` exports + docstring.
   - Update `pipelines/service.py` and `pipelines/starter_pack.py` docstrings.
3. **Data migration**
   - New Alembic revision advancing existing `signals_confirmed` rows (Section 9).
4. **Frontend predicate mirror + status types**
   - `lib/pipelines/activation.ts` — strict `confirmed` check.
5. **Frontend hook**
   - `lib/hooks/use-activate-job.ts` — `onError` + success toast.
6. **Frontend status vocabulary**
   - `app/(dashboard)/jobs/[jobId]/layout.tsx` — canonical chip labels.
   - `components/dashboard/jd-panels/components/CanvasHeader.tsx` — delete chip block.
   - `app/(dashboard)/jobs/page.tsx` — `statusKind()` rewrite + `StatusKind` type.
   - `app/(dashboard)/tracker/ClientTrackerLandingPage.tsx` — filter labels + subhead.
   - `components/dashboard/tracker/TrackerJobCard.tsx` — pill labels.
   - `lib/hooks/use-tracker-jobs.ts` — rename `LIVE_STATUSES` → `IN_FLIGHT_STATUSES` + docstring.
7. **Frontend activation banner**
   - `components/dashboard/job/JobActivationBanner.tsx::PipelineReviewBanner` — hide button when blocked; render pill in its slot.
8. **Manual smoke test** — walk through the full flow once before merging.

---

## Testing

### Backend unit tests (mandatory — see root CLAUDE.md "Test Coverage Gates")

Tests touching `jd/service.py` and the activation predicates require deltas:

- `test_confirm_signals_creates_minimal_pipeline_and_advances` — happy path; asserts `job.status == 'pipeline_built'` + 2 bookend stages after one call.
- `test_confirm_signals_idempotent_when_pipeline_exists` — start with an existing pipeline for the job, call `confirm_signals`, assert status advances to `pipeline_built` and the existing pipeline is preserved (no extra stages, no new instance).
- `test_confirm_signals_atomic_on_pipeline_failure` — monkeypatch `ensure_minimal_pipeline_for_job` to raise; assert `job.status` rolls back to `signals_extracted` (the whole request transaction unwinds).
- `test_activation_predicate_bank_must_be_confirmed` — bank in `reviewing` state → `missing_bank` failure with message "Confirm the question bank for '<name>'.". Bank in `confirmed` state → no failure.

### Manual smoke test

Walk a single job through the entire pipeline:

1. Create a JD → status pill shows "Draft" everywhere (layout chip, jobs list pill).
2. Extract signals → pill shows "Reading JD" during extraction, then "Review signals".
3. Confirm signals → page reloads, layout shows "In review", pipeline tab is now navigable, pipeline has Intake + Debrief auto-created. Tracker landing shows the job under the "In review" filter chip; card pill reads "In review".
4. Add an `ai_screening` middle stage with a name, click into Questions, click "Generate questions" → bank moves to `generating` → `reviewing`. Banner shows "⚠ N things needed" with "Confirm the question bank for '<name>'." as one of the items.
5. Click "Confirm bank" on the bank's drawer → bank moves to `confirmed`. Banner shows "✓ Ready to activate this job…" with green Activate button on the right.
6. Click Activate → success toast appears, status chip updates to "Live · accepting candidates", tracker landing card pill flips to "Live".
7. Negative case: navigate away from the bank before confirming, then click Activate via the banner (force-enabled in DevTools). 422 returns; error toast appears; banner continues to show the predicate failure.

### Regression checks

- The `?tab=jd` redirect (`app/(dashboard)/jobs/[jobId]/page.tsx:29-39`) — confirm `pipeline_built` jobs visiting `/jobs/{id}` (no `?tab`) still redirect to `/pipeline`. `signals_confirmed` jobs (which now should never exist in steady state) get the same redirect — still correct.
- The kanban board (`/api/jobs/{id}/candidates/kanban`) still returns 200 for jobs in `pipeline_built` (it derives from the pipeline rows, not from `job.status`). Candidate-assignment writes still gated on `job.status='active'` — unchanged.
- The SSE status stream (`/api/jobs/{id}/status/stream`) sees two transitions in quick succession during confirm. Verify the stream client doesn't drop the `pipeline_built` event because it was indistinguishable from `signals_confirmed` in the same poll window. (`useJobStatusStream` compares by tuple `(status, enrichment_status)` — two changes within one 1-2s poll fire as two events back-to-back.)

---

## Open questions

- **Should the auto-applied bookend pipeline get a default reviewer?** The `debrief` stage requires `missing_reviewer` to be satisfied before activation. With no default reviewer, every newly-confirmed job shows that predicate failure immediately. Two options:
  - (a) Auto-assign the JD's `created_by` as the debrief reviewer. Predictable, no extra recruiter friction.
  - (b) Leave it unassigned, let the predicate failure surface in the banner. More explicit, matches the "recruiter chooses everyone in the funnel" UX.
  - Recommendation: (b) for now (matches existing design where the recruiter wires up participants explicitly). Revisit if the friction shows up in feedback.
- **Should `signals_confirmed` be removed from the state machine entirely?** It becomes unreachable in steady state. Removing it means a new migration to update the CHECK constraint and the `LEGAL_TRANSITIONS` dict. Recommendation: keep it. It's a legal intermediate inside the `confirm_signals` transaction (the state machine sees it for ~1ms), and removing it would require `transition()` to support multi-hop transitions, which is a meaningful refactor. The cost of keeping it is one unused-in-practice enum value; the benefit is a clean two-step transition path.

---

## Notes for the implementer

- The `ensure_minimal_pipeline_for_job` helper (`pipelines/service.py:1052-1108`) returns `None` when an instance already exists. The atomic confirm path doesn't need that return value — we always issue the `pipeline_built` transition. The idempotency is for the (rare) race where a pipeline was created some other way between snapshot confirm and the auto-apply call within the same transaction.
- The audit log will land two `job_posting.status_changed` rows per confirm — one for `signals_confirmed`, one for `pipeline_built`. This matches existing audit semantics (`transition()` writes one row per call) and is intentional.
- The frontend layout's `tabs[].disabled` predicate (lines 117-124) requires `signals_confirmed | pipeline_built | active` to enable pipeline + questions tabs. With confirm now atomically advancing to `pipeline_built`, the `signals_confirmed` clause is dead in practice but keep it as a safety net — defensive programming against future state-machine changes is cheap here.
- The `redirect-to-pipeline` effect (`app/(dashboard)/jobs/[jobId]/page.tsx:29-39`) needs no change. After this spec, the user creating a JD lands in `draft` → extracts → reviews → confirms → lands on `pipeline_built` → next visit to `/jobs/{id}` (no `?tab`) redirects to `/pipeline`. Same UX as today, just one fewer manual "build pipeline" click.
