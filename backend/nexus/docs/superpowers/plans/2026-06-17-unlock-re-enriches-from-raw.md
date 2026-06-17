# Unlock → Re-enrich from Raw + Re-extract — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "Unlock" button re-enrich the JD from the raw JD (fidelity-first prompt) and then re-extract signals, as one combined action.

**Architecture:** Reuse the existing two-phase actor. `POST /re-extract-signals` stops skipping enrichment (`skip_enrichment=False`) and resets the enrichment idempotency guard (`enrichment_status="idle"`) so Phase 1 re-runs from raw. Frontend changes are copy-only.

**Tech Stack:** FastAPI (`app/modules/jd/router.py`), pytest (`tests/test_jd_router.py`); Next.js/React (`frontend/app`), Vitest.

## Global Constraints

- Reuse the existing `extract_and_enhance_jd` two-phase actor — no new actor, state, or schema.
- Do NOT change `/extract-signals` or `/enrich` — only `/re-extract-signals`.
- Do NOT rename the endpoint, the `useReExtractSignals` hook, or `jobsApi.reExtractSignals`.
- Source-state guard stays: `_REEXTRACT_SOURCE_STATES = {"signals_extracted", "signals_confirmed", "pipeline_built", "active"}`; other states still 409.
- Backend spec: `docs/superpowers/specs/2026-06-17-unlock-re-enriches-from-raw-design.md`.

---

### Task 1: Backend — `/re-extract-signals` re-enriches from raw

**Files:**
- Modify: `app/modules/jd/router.py` (`re_extract_signals`, ~lines 773-837)
- Test: `tests/test_jd_router.py` (4 happy-path re-extract tests)

**Interfaces:**
- Consumes: `reset_banks_for_job`, `transition`, `_safe_dispatch_extraction(job_posting_id, tenant_id, correlation_id, skip_enrichment)` — all already imported/defined in the module.
- Produces: no new public symbols. The endpoint now dispatches with `skip_enrichment=False` and leaves `job.enrichment_status == "idle"` at dispatch time.

- [ ] **Step 1: Update the four happy-path tests to the new contract (RED)**

In `tests/test_jd_router.py`, find the four happy-path re-extract tests (re-extract from `signals_extracted`, `signals_confirmed`, `pipeline_built`, `active`). Each currently asserts the dispatched kwarg `skip_enrichment is True`. For each, change that assertion to `is False` and add an assertion that the job's enrichment guard was reset. Concretely, in each test:

Replace the existing assertion line:
```python
    assert extract_call["kwargs"].get("skip_enrichment") is True
```
with:
```python
    assert extract_call["kwargs"].get("skip_enrichment") is False
```

And after the response-status assertion in each of the four tests, add a DB re-read assertion that the enrichment guard was cleared (use the same async session/job-reload pattern already used elsewhere in the file; if the test already holds a `db`/session fixture and the `job` row, re-fetch and assert):
```python
    await db.refresh(job)
    assert job.enrichment_status == "idle"
    assert job.enrichment_error is None
```
> Note for the implementer: match the exact DB-reload idiom already used in this test module (it may be `await session.refresh(job)`, a fresh `select(JobPosting)`, or reading from the captured dispatch). Do not invent a new fixture. The behavioral assertions to land are: `skip_enrichment is False`, `enrichment_status == "idle"`, `enrichment_error is None`. Leave the 409 guard tests (`signals_extraction_failed`, `draft`, `archived`) untouched.

- [ ] **Step 2: Run the updated tests to verify they FAIL**

Run: `docker compose run --rm nexus pytest tests/test_jd_router.py -k "re_extract" -v`
Expected: the 4 happy-path tests FAIL (endpoint still dispatches `skip_enrichment=True` and does not reset `enrichment_status`); the 409 guard tests still PASS.

- [ ] **Step 3: Implement the endpoint change**

In `app/modules/jd/router.py`, in `re_extract_signals`, the block currently reads:
```python
    # Clear the now-invalid banks + regress to signal review, in this transaction.
    await reset_banks_for_job(db, job_id=job.id)
    await transition(
        db, job,
        to_state="signals_extracting",
        actor_id=user.user.id,
        correlation_id=correlation_id,
    )
    await db.flush()

    background_tasks.add_task(
        _safe_dispatch_extraction,
        job_posting_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id=correlation_id,
        skip_enrichment=True,
    )
```
Change it to:
```python
    # Clear the now-invalid banks + regress to signal review, in this transaction.
    await reset_banks_for_job(db, job_id=job.id)
    # Reset the enrichment idempotency guard so Phase 1 re-runs from the RAW JD
    # (otherwise _run_enrichment short-circuits on enrichment_status=='completed').
    job.enrichment_status = "idle"
    job.enrichment_error = None
    await transition(
        db, job,
        to_state="signals_extracting",
        actor_id=user.user.id,
        correlation_id=correlation_id,
    )
    await db.flush()

    # skip_enrichment=False → the two-phase actor re-enriches from the raw JD
    # (jd_enrichment prompt) THEN re-extracts signals on the fresh enriched JD.
    background_tasks.add_task(
        _safe_dispatch_extraction,
        job_posting_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id=correlation_id,
        skip_enrichment=False,
    )
```

Also update the function docstring: it currently says it dispatches the extraction actor with `skip_enrichment=True` and inserts a new snapshot. Change it to state that it re-enriches from the raw JD (jd_enrichment prompt) and then re-extracts signals on the fresh enriched JD, clearing banks and regressing to signal review. Keep the line about the 422 guards (empty raw JD, missing profile).

- [ ] **Step 4: Run the re-extract tests to verify they PASS**

Run: `docker compose run --rm nexus pytest tests/test_jd_router.py -k "re_extract" -v`
Expected: all re-extract tests PASS (4 happy-path + the 409 guards).

- [ ] **Step 5: Run the broader JD router + actor suite for regressions**

Run: `docker compose run --rm nexus pytest tests/test_jd_router.py tests/test_jd_actor.py tests/test_jd_state_machine.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/jd/router.py tests/test_jd_router.py
git commit -m "feat(jd): unlock re-enriches from raw JD then re-extracts signals

re-extract-signals now dispatches the two-phase actor with skip_enrichment=False
and resets enrichment_status to idle so Phase 1 re-runs the fidelity-first
jd_enrichment prompt from the raw JD before signal re-extraction.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Frontend — unlock button + dialog copy

**Files:**
- Modify: `frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx` (~lines 232-235)
- Modify: `frontend/app/components/dashboard/jd-panels/components/TabStrip.tsx` (~line 106)
- Modify: `frontend/app/lib/hooks/use-re-extract-signals.ts` (success toast)
- Test: any existing Vitest that asserts this copy (search before editing)

**Interfaces:**
- Consumes: nothing new. Copy-only; the mutation contract (`useReExtractSignals` → `POST /re-extract-signals`) is unchanged.
- Produces: no new symbols.

- [ ] **Step 1: Check for existing copy tests**

Run (from `frontend/app`): `grep -rn "Unlock & re-run\|Re-run signal extraction\|Re-extracting\|Re-running signal extraction" components lib tests`
Note any test files that assert these strings — they must be updated in Step 5.

- [ ] **Step 2: Update the confirmation dialog copy**

In `frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx`, the confirm dialog currently reads:
```tsx
        title="Re-run signal extraction?"
        description="This unlocks these live signals, replaces them with a fresh AI extraction, and clears the question banks generated from them. You'll review the new signals and regenerate the banks. The job resets to signal review."
        confirmLabel="Unlock & re-run"
```
Change to:
```tsx
        title="Re-enrich & re-extract?"
        description="This regenerates the enriched JD from the raw JD, then re-extracts fresh signals from it and clears the question banks generated from the old signals. You'll review the new signals and regenerate the banks. The job resets to signal review."
        confirmLabel="Unlock & re-enrich"
```

- [ ] **Step 3: Update the button label**

In `frontend/app/components/dashboard/jd-panels/components/TabStrip.tsx`, the button text currently reads:
```tsx
              {reExtracting ? 'Re-extracting...' : 'Unlock & re-run extraction'}
```
Change to:
```tsx
              {reExtracting ? 'Re-enriching...' : 'Unlock & re-enrich'}
```

- [ ] **Step 4: Update the success toast**

In `frontend/app/lib/hooks/use-re-extract-signals.ts`, change:
```ts
      toast.success('Re-running signal extraction')
```
to:
```ts
      toast.success('Re-enriching JD & re-extracting signals')
```

- [ ] **Step 5: Update any copy tests found in Step 1**

For each test file flagged in Step 1, update the asserted strings to the new copy from Steps 2-4. If none were found, skip.

- [ ] **Step 6: Lint + type-check + test**

Run (from `frontend/app`):
```bash
npm run lint && npm run test -- --run
```
Expected: PASS. (If `npm run test` watches by default, the `-- --run` flag forces a single run; adjust to the repo's actual one-shot invocation if different.)

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx frontend/app/components/dashboard/jd-panels/components/TabStrip.tsx frontend/app/lib/hooks/use-re-extract-signals.ts
git commit -m "feat(app): unlock button reads as re-enrich & re-extract

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** Endpoint behavior change (`skip_enrichment=False` + `enrichment_status` reset) → Task 1 Step 3. Docstring → Task 1 Step 3. Backend test updates (4 happy-path; 409 guards untouched) → Task 1 Steps 1-2,4. Frontend copy (dialog, button, toast) → Task 2 Steps 2-4. Copy-test update → Task 2 Steps 1,5. Non-goals (no rename, no actor/state change, only `/re-extract-signals`) → Global Constraints. Full coverage.

**Placeholder scan:** No TBD/TODO. Concrete file:line targets and exact before/after code for every change. The one flexible point (Task 1 Step 1 DB-reload idiom) names the exact assertions that must land and tells the implementer to match the module's existing idiom rather than inventing one — not a placeholder.

**Type consistency:** No new types/signatures. `skip_enrichment` is the existing actor kwarg (bool). `enrichment_status`/`enrichment_error` are existing `JobPosting` columns. Hook/API names unchanged.
