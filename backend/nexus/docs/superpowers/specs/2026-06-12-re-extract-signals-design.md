# Unlock & Re-run Signal Extraction (+ purpose read-only display)

- **Date:** 2026-06-12
- **Branch:** `feat/followups-governed-dimensions`
- **Status:** Design — approved direction, pending spec review
- **Builds on:** `2026-06-12-ai-screening-skills-test-design.md` (the `purpose` field this surfaces)

---

## 1. Why

Once a JD's signals are confirmed, there is **no way to re-run signal extraction** — the only
extraction endpoint (`POST /{job_id}/extract-signals`) is **draft-only** (`if job.status !=
"draft" → 409`), and the state machine has no transition back to `signals_extracting` from any
confirmed/downstream state. This blocks iterating on signal quality (e.g. after the new v2
extraction prompt) on any job past draft — including the live `active` test job.

Separately, the new `purpose` field (skill vs eligibility) is only visible in the *editable*
signal panel; the read-only signal views don't show it.

### Goal

Let a recruiter **unlock a confirmed/active job and re-run signal extraction**, producing a
fresh signal set and clearing the now-invalid question banks; and surface `purpose` in the
read-only signal views.

### Non-goals

- Blocking re-extraction when active candidate sessions exist (the user chose warn-and-allow;
  a session guard is a noted future hardening, not built here).
- Changing the draft-first `/extract-signals` flow or the extraction actor itself.
- Preserving recruiter-authored custom questions across a re-extract (banks are cleared).

---

## 2. What "locked" means + the regression model

A confirmed job moves through `signals_confirmed → pipeline_built → active`; all three are
"locked" (signals read-only, `is_confirmed=true`). Re-extraction must be allowed from any of
them. Because extraction inherently drives status (`signals_extracting → signals_extracted`),
re-extracting **regresses the job lifecycle back to `signals_extracting`** and then
`signals_extracted` (unlocked, unconfirmed) for re-review. The recruiter edits the new
signals, re-confirms (idempotent `ensure_minimal_pipeline_for_job` preserves the pipeline),
and regenerates the (now-cleared) banks. This regression is powerful and explicit — the
warning dialog (§5) states it plainly.

Extraction is **non-destructive to history**: it inserts a NEW snapshot at `MAX(version)+1`
(the old confirmed snapshot remains as a prior version).

---

## 3. Backend — state machine

Add transitions so re-extraction is legal + audited (`app/modules/jd/state_machine.py`,
`LEGAL_TRANSITIONS`):

```python
"signals_extracted":  {"signals_confirmed", "signals_extracting"},          # + re-extract
"signals_confirmed":  {"signals_extracted", "pipeline_built", "signals_extracting"},  # + unlock & re-extract
"pipeline_built":     {"active", "signals_extracting"},                      # + unlock & re-extract
"active":             {"signals_extracting"},                               # + unlock & re-extract
```

(`signals_extracting`, `signals_extraction_failed`, `draft`, `archived` rows unchanged;
`archived` stays terminal — no re-extract.)

---

## 4. Backend — `POST /{job_id}/re-extract-signals`

A dedicated endpoint (isolates the destructive unlock+clear from the draft-first
`/extract-signals`):

- **Guards:** `require_job_access(..., "manage")`; source `status ∈ {signals_extracted,
  signals_confirmed, pipeline_built, active}` (else 409 with a clear code); `description_raw`
  non-empty (`empty_raw_jd`); company profile present (`company_profile_incomplete`) — same
  422s as `/extract-signals`.
- **Action, in the request transaction (atomic before dispatch):**
  1. `reset_banks_for_job(db, job_id)` — clear the job's question banks (§4.1).
  2. `transition(db, job, to_state="signals_extracting", actor_id, correlation_id)` — audited.
  3. `db.flush()`.
  4. `background_tasks.add_task(_safe_dispatch_extraction, ..., skip_enrichment=True)` — the
     existing `extract_and_enhance_jd` actor (creates the new snapshot version).
  5. publish `JD_STATUS_CHANGED` (so SSE/polling shows the extracting view).
- **Response:** `202 {"status": "accepted"}` (mirrors `/extract-signals`).
- **Rate limit:** declare the same per-route limit class as `/extract-signals`.

### 4.1 `reset_banks_for_job(db, job_id)` — question_bank public function

New function in the `question_bank` module (exposed via `__init__`/`__all__`; called by `jd`
through the public API per module-boundary rules). For every stage bank under the job's
pipeline instance:
- wipe its AI-generated questions (reuse the existing `wipe_ai_questions` helper), and
- reset the bank `status` to `draft` and clear `generated_at`/`generated_by`/`coverage_notes`/
  `confirmed_at`/`confirmed_by` (so the questions UI shows the "Generate" call-to-action).

Runs under the caller's tenant-scoped session; emits one audit row
(`question_bank.banks_reset_for_reextract`). Idempotent (no banks → no-op). (A job in
`signals_extracted` may have no pipeline/banks yet → no-op; the confirmed/active cases are
where banks exist.)

---

## 5. Frontend — the button + warning dialog

- `lib/api/jobs.ts`: `reExtractSignals(token, id)` → `POST /api/jobs/{id}/re-extract-signals`.
- `lib/hooks/use-re-extract-signals.ts`: a mutation; on success invalidate `['jobs', id]`,
  `['jobs-list']`, and `['banks', id]` (banks were cleared).
- In the JD review header (`components/dashboard/jd-panels/components/TabStrip.tsx`): a button
  shown when the job is locked (`isConfirmed`) labeled **"Unlock & re-run extraction"** (and a
  **"Re-run extraction"** variant when `status === 'signals_extracted'`, where there's nothing
  to unlock). Disabled while `status === 'signals_extracting'`.
- Clicking opens a **`DangerConfirmDialog`** (existing primitive) with copy:
  > Re-running extraction will **unlock these live signals**, replace them with a fresh AI
  > extraction, and **clear the question banks** generated from them — you'll review the new
  > signals and regenerate the banks. This resets the job to signal review.
  On confirm → fire `useReExtractSignals().mutate()`. SSE/polling then shows the extracting
  view and the new signals for review.

---

## 6. Frontend — `purpose` in the read-only views

- `components/dashboard/jd-panels/components/SignalRow.tsx`: a compact **`SKILL`/`ELIG`** badge
  alongside the existing MUST/knockout badge (read `(s.purpose ?? 'skill')`).
- `components/dashboard/jd-panels/SignalInspector.tsx`: show the purpose label in the signal's
  read-only metadata block (skill = "tested in the AI screen", eligibility = "recruiter
  pre-screened").
- Optionally `SignalChip.tsx` (read-only chip used in summaries) — only if it already shows
  type/weight badges; keep consistent.

---

## 7. Testing

- **Backend unit:** state-machine — each of the four locked states → `signals_extracting` is
  legal; `archived → signals_extracting` is NOT.
- **Backend endpoint:** `re-extract-signals` from each allowed source state transitions +
  dispatches (actor mocked) + clears banks; rejects `draft` and `archived` with 409; 422 on
  empty raw JD / missing profile; cross-tenant access denied.
- **`reset_banks_for_job`:** wipes questions + resets bank status to draft; tenant-scoped
  (cross-tenant returns 0/clears nothing); no-op when no banks; audit row written.
- **`is_confirmed` flips to false:** after re-extract the job is unlocked — confirm (in impl)
  how `is_confirmed` is derived on the job summary + `JobStatusEvent` (status-based vs latest-
  snapshot `confirmed_at`) and that it becomes `false` post-re-extract, since the UI lock keys
  off it. Add a test asserting it.
- **Frontend:** type-check + build; the dialog + button render and gate on status; `SignalRow`
  / `SignalInspector` render the purpose badge; mutation invalidates the right query keys.

---

## 8. Code-quality mandate

- `reset_banks_for_job` lives in the `question_bank` module and is called via its public API
  (no cross-module deep import from `jd`).
- The destructive reset is isolated in the dedicated `/re-extract-signals` endpoint — the
  draft `/extract-signals` is untouched.
- `(s.purpose ?? 'skill')` legacy guard on every read (snapshots predating `purpose`).
- Every new transition, endpoint branch, and the bank-reset ships with a test in the same change.
- The bank clear + the status transition happen in one request transaction (atomic) before the
  actor is dispatched — no half-reset state.
