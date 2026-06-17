# Unlock → Re-enrich from Raw + Re-extract

**Date:** 2026-06-17
**Status:** Design — approved, pending implementation
**Branch:** `feat/jd-enrichment-fidelity` (companion to the fidelity-first enrichment prompt rewrite)
**Scope:** One backend endpoint behavior change + its tests; frontend copy only.

---

## Problem

After the fidelity-first enrichment prompt rewrite
(`2026-06-17-jd-enrichment-fidelity-design.md`), there is **no way to re-run enrichment
on an existing job** from the recruiter UI. The only control is the "Unlock & re-run
extraction" button (`POST /api/jobs/{id}/re-extract-signals`), which dispatches the
two-phase actor with **`skip_enrichment=True`** — it re-extracts signals but reuses the
*old* enriched JD. So the new prompt never runs on a job that was enriched before the
rewrite, and recruiters cannot regenerate a bad enriched JD.

### Root cause

Two guards keep Phase 1 (enrichment) from running on re-extract:
1. `re_extract_signals` dispatches with `skip_enrichment=True` (`jd/router.py:824`).
2. Even with `skip_enrichment=False`, `_run_enrichment` short-circuits when
   `enrichment_status == "completed"` (`jd/actors.py:175`) — its idempotency guard.

The full re-enrich→re-extract path already exists and is tested: `extract_and_enhance_jd`
with `skip_enrichment=False` runs Phase 1 from the **raw JD** via the `jd_enrichment`
prompt (writing a fresh `description_enriched`), then Phase 2 signal extraction on that
fresh enriched JD. The fix is to use it.

---

## Goal

The "Unlock" button performs one combined action: **re-enrich the JD from the raw JD (new
prompt) → re-extract signals on the new enriched JD → clear banks → regress to signal
review.** The recruiter then reviews fresh signals, re-confirms, and regenerates banks
(unchanged downstream flow).

This reuses the existing, tested two-phase actor path — no new actor, no new state, no hack.

---

## Design

### Backend — `re_extract_signals` (`app/modules/jd/router.py`)

The only substantive change. Inside the existing transaction, before dispatch:

1. `reset_banks_for_job(db, job_id=job.id)` — unchanged (banks from the old snapshot are
   now invalid).
2. **NEW:** reset the enrichment idempotency guard so Phase 1 re-runs from raw:
   ```python
   job.enrichment_status = "idle"
   job.enrichment_error = None
   ```
3. `transition(... to_state="signals_extracting" ...)` — unchanged.
4. `await db.flush()` — unchanged.
5. Dispatch the extraction actor with **`skip_enrichment=False`** (was `True`):
   ```python
   background_tasks.add_task(
       _safe_dispatch_extraction,
       job_posting_id=str(job.id),
       tenant_id=str(job.tenant_id),
       correlation_id=correlation_id,
       skip_enrichment=False,
   )
   ```
6. Update the docstring to describe the re-enrich→re-extract flow.

**Why this is sufficient / correct:** with `enrichment_status="idle"` and
`status="signals_extracting"`, the actor's Phase-1 pre-mark flips `enrichment_status` to
`"streaming"` (`actors.py:501-508`), `_run_enrichment` runs (guard at 175 no longer trips),
re-enriches from `description_raw` via the `jd_enrichment` prompt, sets
`enrichment_status="completed"`; Phase 2 then reads the fresh enriched JD
(`actors.py:326-327`). Retry idempotency is preserved: on a retry after Phase 1 succeeded,
`enrichment_status` is already `"completed"` so Phase 1 is skipped — exactly the existing
behavior.

**Source-state guard unchanged:** `_REEXTRACT_SOURCE_STATES =
{"signals_extracted", "signals_confirmed", "pipeline_built", "active"}`;
`signals_extraction_failed`/`draft`/`archived` still 409.

**No other endpoint changes.** `/extract-signals` (draft) and `/enrich` (separate
recruiter enrichment / snapshot-aware re-enrich) keep `skip_enrichment=True` /
their current behavior. This change is scoped to the unlock path only.

### Frontend — copy only (behavior is server-side)

The SSE status flow already renders the enrichment `streaming → completed` phase (same
states `/enrich` uses), so progress UI works unchanged. Only user-facing copy needs to
match the new behavior:

- **Confirmation dialog** (`components/dashboard/jd-panels/JDReviewShell.tsx:232-235`):
  - title → `Re-enrich & re-extract?`
  - description → explain it **regenerates the enriched JD from the raw JD**, then
    re-extracts signals, clears the question banks, and resets the job to signal review.
  - confirmLabel → `Unlock & re-enrich`
- **Button label** (`components/dashboard/jd-panels/components/TabStrip.tsx:106`):
  `Unlock & re-run extraction` → `Unlock & re-enrich`; pending text
  `Re-extracting...` → `Re-enriching...`
- **Success toast** (`lib/hooks/use-re-extract-signals.ts`): `Re-running signal
  extraction` → `Re-enriching JD & re-extracting signals`.

The hook/API function names (`useReExtractSignals`, `jobsApi.reExtractSignals`) and the
endpoint path stay as-is — renaming them is churn for no behavior gain.

---

## Tests

### Backend (`tests/test_jd_router.py`)

The four happy-path re-extract tests (from `signals_extracted`, `signals_confirmed`,
`pipeline_built`, `active`) currently assert `skip_enrichment is True`. Update each to:
- assert the dispatch kwarg `skip_enrichment is False`;
- assert the job row's `enrichment_status == "idle"` after the call (guard reset).

The 409 guard tests (`signals_extraction_failed`, `draft`, `archived`) are unchanged.

### Frontend

If a test asserts the dialog/button copy, update it to the new strings. (No behavior test
changes — the mutation contract is unchanged.)

---

## Non-goals

- No change to the actor, state machine, schema, or the `jd_enrichment` prompt (done in the
  companion spec).
- No rename of the endpoint / hook / API method.
- No two-step "review enriched JD before extracting" flow (explicitly chosen: one combined
  action).

## Operational notes

- Backend (`nexus` API) hot-reloads; the actor runs in `nexus-worker` (already restarted
  for the prompt change). No extra restart needed for the router change beyond the API
  reload.
- Validates the companion prompt rewrite end-to-end: after this lands, clicking "Unlock &
  re-enrich" on the EMM job regenerates the enriched JD with the fidelity-first prompt.
