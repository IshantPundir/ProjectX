# Unlock & Re-run Signal Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a recruiter unlock a confirmed/active JD and re-run signal extraction — producing a fresh signal snapshot, clearing the now-invalid question banks, and unlocking the signals for re-review — plus surface the `purpose` field in the read-only signal views.

**Architecture:** A dedicated `POST /{job_id}/re-extract-signals` endpoint (separate from the draft-only `/extract-signals`) that, in one request transaction, clears the job's question banks and transitions the job back to `signals_extracting`, then dispatches the existing extraction actor (which inserts a NEW snapshot version). New state-machine transitions allow re-extraction from any locked state. Frontend adds a warn-dialog button + read-only `purpose` badges.

**Tech Stack:** FastAPI, SQLAlchemy async, Dramatiq, Pydantic v2, pytest; Next.js 16 + React Query + Zustand + Vitest.

**Spec:** `docs/superpowers/specs/2026-06-12-re-extract-signals-design.md`

---

## Code-quality mandate (binding)
- `reset_banks_for_job` lives in `question_bank` and is called via its public API (no cross-module deep import from `jd`).
- The destructive reset is isolated in `/re-extract-signals`; the draft `/extract-signals` is untouched.
- Bank-clear + status transition happen in ONE request transaction before the actor dispatches (no half-reset).
- `(s.purpose ?? 'skill')` legacy guard on every frontend read.
- Every new transition / endpoint branch / the bank-reset ships with a test in the same task.

---

## File Structure
- `app/modules/jd/state_machine.py` — 4 new transitions into `signals_extracting`.
- `app/modules/question_bank/service.py` + `__init__.py` — `reset_banks_for_job` + export.
- `app/modules/jd/router.py` — `POST /{job_id}/re-extract-signals` (+ imports the public `reset_banks_for_job`).
- `frontend/app/lib/api/jobs.ts` — `reExtractSignals`.
- `frontend/app/lib/hooks/use-re-extract-signals.ts` (CREATE).
- `frontend/app/components/dashboard/jd-panels/components/TabStrip.tsx` + `JDReviewShell.tsx` (+ `SignalsCanvas.tsx` threading) — button + dialog.
- `frontend/app/components/dashboard/jd-panels/components/SignalRow.tsx` + `SignalInspector.tsx` — purpose badge.

---

## Task 1: State machine — allow re-extraction from locked states

**Files:**
- Modify: `app/modules/jd/state_machine.py:24-33` (`LEGAL_TRANSITIONS`)
- Test: `tests/test_jd_state_machine.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_jd_state_machine.py` (it tests `is_legal_transition`):
```python
import pytest
from app.modules.jd.state_machine import is_legal_transition


@pytest.mark.parametrize("src", ["signals_extracted", "signals_confirmed", "pipeline_built", "active"])
def test_reextract_allowed_from_locked_states(src):
    assert is_legal_transition(src, "signals_extracting") is True


def test_reextract_not_allowed_from_archived():
    assert is_legal_transition("archived", "signals_extracting") is False


def test_existing_transitions_unchanged():
    assert is_legal_transition("signals_confirmed", "pipeline_built") is True
    assert is_legal_transition("pipeline_built", "active") is True
    assert is_legal_transition("draft", "signals_extracting") is True
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/test_jd_state_machine.py -k reextract -q`
Expected: FAIL — `active`/`pipeline_built`/`signals_confirmed`/`signals_extracted` → `signals_extracting` not legal yet.

- [ ] **Step 3: Add the transitions**

In `app/modules/jd/state_machine.py`, update `LEGAL_TRANSITIONS` (add `"signals_extracting"` to four rows; leave the rest):
```python
LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},  # retry
    "signals_extracted": {"signals_confirmed", "signals_extracting"},  # + re-extract
    "signals_confirmed": {"signals_extracted", "pipeline_built", "signals_extracting"},  # + unlock & re-extract
    "pipeline_built": {"active", "signals_extracting"},  # + unlock & re-extract
    "active": {"signals_extracting"},  # + unlock & re-extract
    "archived": set(),
}
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/test_jd_state_machine.py -q`
Expected: PASS (the new tests + existing ones).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/jd/state_machine.py backend/nexus/tests/test_jd_state_machine.py
git commit -m "feat(jd): allow re-extraction transitions from locked states"
```

---

## Task 2: `reset_banks_for_job` (question_bank module)

**Files:**
- Modify: `app/modules/question_bank/service.py` (add function near `wipe_ai_questions` ~line 658), `app/modules/question_bank/__init__.py` (export).
- Test: `tests/question_bank/test_reset_banks_for_job.py` (CREATE)

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/question_bank/test_reset_banks_for_job.py`. Model the DB setup on the existing bank-service tests (read `tests/test_question_banks_service.py` for the tenant/job/pipeline/bank fixture helpers and reuse them). The test must: create a job with a pipeline + a `StageQuestionBank` in status `confirmed`/`reviewing` with ≥1 `StageQuestion`; call `reset_banks_for_job`; assert the bank's questions are wiped (0 remain) and its status is `draft` with `generated_at`/`coverage_notes` cleared; assert the return count == number of banks; and a job with NO pipeline returns 0 (no-op).
```python
# tests/question_bank/test_reset_banks_for_job.py
import pytest
from app.modules.question_bank import reset_banks_for_job
# ... import the SAME fixtures/builders used by tests/test_question_banks_service.py
# (a helper that seeds tenant + job + pipeline instance + stage + bank + questions)

pytestmark = pytest.mark.asyncio


async def test_reset_wipes_questions_and_resets_status(db_session, seeded_bank_with_questions):
    bank, job_id = seeded_bank_with_questions  # bank in 'confirmed'/'reviewing' with questions
    n = await reset_banks_for_job(db_session, job_id=job_id)
    assert n >= 1
    await db_session.refresh(bank)
    assert bank.status == "draft"
    assert bank.generated_at is None
    assert bank.coverage_notes is None
    # questions gone
    from sqlalchemy import select, func
    from app.modules.question_bank.models import StageQuestion
    remaining = (await db_session.execute(
        select(func.count()).select_from(StageQuestion).where(StageQuestion.bank_id == bank.id)
    )).scalar_one()
    assert remaining == 0


async def test_reset_is_noop_for_job_without_banks(db_session, seeded_job_without_pipeline):
    job_id = seeded_job_without_pipeline
    n = await reset_banks_for_job(db_session, job_id=job_id)
    assert n == 0
```
(ADAPT the fixture names/imports to whatever `tests/test_question_banks_service.py` actually provides — read it first. If it uses inline builders rather than fixtures, build the rows inline the same way.)

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_reset_banks_for_job.py -q`
Expected: FAIL — `reset_banks_for_job` not importable.

- [ ] **Step 3: Implement `reset_banks_for_job` in `service.py`**

`StageQuestionBank` has a `job_posting_id` column, so query banks by job directly (no pipeline join). Add near `wipe_ai_questions`:
```python
async def reset_banks_for_job(db: AsyncSession, *, job_id: UUID) -> int:
    """Clear ALL question banks for a job — used by JD re-extraction, which invalidates
    every bank generated from the prior signal snapshot. Wipes each bank's AI questions and
    HARD-RESETS the bank to 'draft' (cleared timestamps/notes) so the questions UI shows the
    'Generate' call-to-action. This is a deliberate bulk reset, not a per-bank lifecycle
    transition (hence a direct status set rather than the state-machine helpers). Returns the
    number of banks reset; 0 if the job has none (e.g. no pipeline yet)."""
    banks = list((await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.job_posting_id == job_id)
    )).scalars().all())
    for bank in banks:
        await wipe_ai_questions(db, bank=bank)
        bank.status = "draft"
        bank.generated_at = None
        bank.generated_by = None
        bank.coverage_notes = None
        bank.confirmed_at = None
        bank.confirmed_by = None
    await db.flush()
    return len(banks)
```
(Confirm `select`, `UUID`, `StageQuestionBank` are imported at the top of `service.py` — they are, since other functions use them.)

- [ ] **Step 4: Export it from the module public API**

In `app/modules/question_bank/__init__.py`:
```python
from app.modules.question_bank.service import recompute_and_persist_stale, reset_banks_for_job

__all__ = ["StageQuestion", "StageQuestionBank", "recompute_and_persist_stale", "reset_banks_for_job"]
```

- [ ] **Step 5: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_reset_banks_for_job.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/question_bank/service.py backend/nexus/app/modules/question_bank/__init__.py backend/nexus/tests/question_bank/test_reset_banks_for_job.py
git commit -m "feat(question_bank): reset_banks_for_job — clear+draft a job's banks (re-extract reset)"
```

---

## Task 3: `POST /{job_id}/re-extract-signals` endpoint

**Files:**
- Modify: `app/modules/jd/router.py` (add endpoint after the existing `/extract-signals` ~line 764; import `reset_banks_for_job`).
- Test: `tests/test_jd_router.py` (extend)

- [ ] **Step 1: Write the failing test**

Read `tests/test_jd_router.py` for the existing `/extract-signals` test pattern (client fixture, how it mocks the dispatch + seeds a job in a given status). Add tests for `re-extract-signals`. They must assert: (a) from `signals_confirmed` (and `active`) → 202, the job transitions to `signals_extracting`, the extraction dispatch is called, and `reset_banks_for_job` ran (banks cleared); (b) from `draft` → 409; from `archived` → 409; (c) 422 on empty raw JD / missing profile (reuse the existing guard-test pattern). Mock `_safe_dispatch_extraction` (or whatever the existing extract test mocks) and `reset_banks_for_job` (or assert via DB that banks were cleared). Name them `test_reextract_*`.

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/test_jd_router.py -k reextract -q`
Expected: FAIL — route 404 (not defined).

- [ ] **Step 3: Implement the endpoint**

Add to `app/modules/jd/router.py` (mirror the existing `/extract-signals` handler at ~line 698-764). Import the public reset at the top with the other question_bank imports (if none exist, add `from app.modules.question_bank import reset_banks_for_job`):
```python
_REEXTRACT_SOURCE_STATES = {"signals_extracted", "signals_confirmed", "pipeline_built", "active"}


@router.post("/{job_id}/re-extract-signals", status_code=202)
async def re_extract_signals(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    """Unlock a confirmed/active job and RE-RUN signal extraction.

    Clears the job's question banks (they were generated from the prior snapshot and are now
    invalid), regresses the job to ``signals_extracting``, and dispatches the extraction actor
    (``skip_enrichment=True``) which inserts a NEW snapshot version. The recruiter reviews the
    fresh signals, re-confirms, and regenerates the banks. Distinct from the draft-only
    ``/extract-signals``. Same 422 guards (empty raw JD, missing profile)."""
    job = await require_job_access(db, job_id, user, "manage")
    if job.status not in _REEXTRACT_SOURCE_STATES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "job_not_re_extractable",
                "message": (
                    f"Cannot re-extract signals from a job in status '{job.status}'. "
                    "Re-extraction is available on extracted/confirmed/active jobs."
                ),
            },
        )
    if not (job.description_raw or "").strip():
        raise EmptyRawJDError(job_id)
    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        raise CompanyProfileIncompleteError(job.org_unit_id)

    correlation_id = _get_correlation_id(request)

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

    status_event = await get_job_status(db, job_id)
    if status_event is not None:
        background_tasks.add_task(
            pubsub.publish,
            pubsub.job_channel(job_id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
            correlation_id=correlation_id,
        )

    return {"status": "accepted"}
```
(Match the EXACT names the existing `/extract-signals` uses: `_safe_dispatch_extraction`, `EmptyRawJDError`, `CompanyProfileIncompleteError`, `find_company_profile_in_ancestry`, `transition`, `get_job_status`, `_get_correlation_id`, `pubsub` — they're all already imported in this file. Declare the route's rate limit the same way `/extract-signals` does, if it declares one.)

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/test_jd_router.py -k reextract -q`
Expected: PASS. Then the full router file: `docker compose run --rm nexus pytest tests/test_jd_router.py -m "not prompt_quality" -q 2>&1 | tail -6`.

- [ ] **Step 5: Confirm `is_confirmed` derivation (no code change expected)**

`is_confirmed` is derived as `snapshot.confirmed_at is not None` (`jd/service.py:419`, `jd/router.py:158`) off the LATEST snapshot. After the actor creates a new unconfirmed snapshot, `is_confirmed` becomes `false` automatically (the UI unlocks). Add a focused unit test (in `tests/test_jd_router.py` or `tests/test_jd_service_create.py`) asserting the job-summary builder returns `is_confirmed=False` when the latest snapshot has `confirmed_at=None`. If such a test already exists, reference it and skip.

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_router.py
git commit -m "feat(jd): POST /re-extract-signals — unlock + clear banks + re-run extraction"
```

---

## Task 4: Frontend — API fn + hook

**Files:**
- Modify: `frontend/app/lib/api/jobs.ts` (add `reExtractSignals` near `extractSignals`/`triggerEnrich`).
- Create: `frontend/app/lib/hooks/use-re-extract-signals.ts`

- [ ] **Step 1: Add the API function**

In `frontend/app/lib/api/jobs.ts`, near `triggerEnrich`/`extractSignals`:
```typescript
reExtractSignals: (token: string, id: string): Promise<{ status: string }> =>
  apiFetch<{ status: string }>(`/api/jobs/${id}/re-extract-signals`, {
    token,
    method: 'POST',
  }),
```

- [ ] **Step 2: Create the hook (mirror `use-trigger-enrich.ts`)**

Read `frontend/app/lib/hooks/use-trigger-enrich.ts` and create `use-re-extract-signals.ts` matching its shape:
```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { jobsApi } from '@/lib/api/jobs'

export function useReExtractSignals(jobId: string) {
  const queryClient = useQueryClient()
  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.reExtractSignals(token, jobId)
    },
    onSuccess: () => {
      toast.success('Re-running signal extraction')
      void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
      void queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
      void queryClient.invalidateQueries({ queryKey: ['banks', jobId] })
    },
    onError: (error) => {
      toast.error(error.message || 'Failed to re-run extraction')
    },
  })
}
```
(Match the exact import paths + style of the sibling `use-trigger-enrich.ts` — read it first; e.g. `jobsApi` may be imported as `* as jobsApi` or a named object.)

- [ ] **Step 3: Type-check**

Run: `cd /home/ishant/Projects/ProjectX/frontend/app && npx tsc --noEmit 2>&1 | tail -8`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/lib/api/jobs.ts frontend/app/lib/hooks/use-re-extract-signals.ts
git commit -m "feat(app): reExtractSignals api + useReExtractSignals hook"
```

---

## Task 5: Frontend — the button + warning dialog

**Files:**
- Modify: `frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx` (wire the hook + dialog state, pass a handler down), `components/dashboard/jd-panels/components/TabStrip.tsx` (the button), and the threading component between them (`SignalsCanvas.tsx` if `TabStrip` is rendered through it — confirm by reading how `onReEnrich` is threaded).

- [ ] **Step 1: Read the existing `onReEnrich` threading**

`onReEnrich` flows `JDReviewShell` → (SignalsCanvas) → `TabStrip`. Read those three files to see exactly how the prop is passed, and how `DangerConfirmDialog` (`@/components/px`) is used elsewhere (props: open/title/body/confirmLabel/onConfirm/onCancel — read `components/px/DangerConfirmDialog.tsx`).

- [ ] **Step 2: Add the button in `TabStrip.tsx`**

Add a button shown when the job is locked. `TabStrip` already receives `isConfirmed` (or derive from a status prop — match what it has). Add a new prop `onReExtract: () => void` and `reExtracting: boolean`, and render between "Ask Copilot again" and the confirm buttons:
```tsx
{isConfirmed && (
  <button
    type="button"
    className="px-btn outline sm"
    onClick={onReExtract}
    disabled={reExtracting}
  >
    <I d={REFRESH_ICON} size={11} />
    {reExtracting ? 'Re-extracting…' : 'Unlock & re-run extraction'}
  </button>
)}
```
(If `TabStrip` knows the raw status, also show a `Re-run extraction` variant when `status === 'signals_extracted'`. Keep it to the `isConfirmed` case if `TabStrip` only has that flag — the confirmed case is the primary requirement.)

- [ ] **Step 3: Wire the hook + dialog in `JDReviewShell.tsx`**

In `JDReviewShell.tsx`: add `const reExtract = useReExtractSignals(job.id)`, a `const [confirmReExtract, setConfirmReExtract] = useState(false)`, pass `onReExtract={() => setConfirmReExtract(true)}` and `reExtracting={reExtract.isPending}` down to where `onReEnrich` is passed, and render a `DangerConfirmDialog`:
```tsx
<DangerConfirmDialog
  open={confirmReExtract}
  title="Re-run signal extraction?"
  body={
    "This unlocks these live signals, replaces them with a fresh AI extraction, and clears " +
    "the question banks generated from them. You'll review the new signals and regenerate the " +
    "banks. The job resets to signal review."
  }
  confirmLabel="Unlock & re-run"
  onConfirm={() => { setConfirmReExtract(false); reExtract.mutate() }}
  onCancel={() => setConfirmReExtract(false)}
/>
```
(Match `DangerConfirmDialog`'s ACTUAL prop names from `components/px/DangerConfirmDialog.tsx` — adapt `body`/`title`/`confirmLabel`/`onConfirm`/`onCancel` to its real API.)

- [ ] **Step 4: Type-check + build + smart-quote check**

Run:
```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit 2>&1 | tail -8
grep -rnP "[\x{2018}\x{2019}\x{201C}\x{201D}]" "components/dashboard/jd-panels/JDReviewShell.tsx" "components/dashboard/jd-panels/components/TabStrip.tsx" && echo "FIX SMART QUOTES" || echo "ASCII OK"
npm run build 2>&1 | tail -6
```
Expected: type-check clean, ASCII OK, build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add "frontend/app/components/dashboard/jd-panels/"
git commit -m "feat(app): unlock & re-run extraction button + warning dialog"
```

---

## Task 6: Frontend — `purpose` in the read-only views

**Files:**
- Modify: `frontend/app/components/dashboard/jd-panels/components/SignalRow.tsx`, `frontend/app/components/dashboard/jd-panels/SignalInspector.tsx`

- [ ] **Step 1: Add a purpose badge to `SignalRow.tsx`**

`SignalRow` renders a badge grid (source / knockout / value / confidence). Read its layout, then add a compact `SKILL`/`ELIG` chip near the knockout badge, reading `(s.purpose ?? 'skill')`:
```tsx
{(s.purpose ?? 'skill') === 'eligibility' ? (
  <span className="rounded px-1 text-[8.5px] font-semibold" style={{ background: 'var(--px-zinc-100)', color: 'var(--px-zinc-600)' }}>ELIG</span>
) : (
  <span className="rounded px-1 text-[8.5px] font-semibold" style={{ background: 'var(--px-accent-tint)', color: 'var(--px-accent)' }}>SKILL</span>
)}
```
(Match the exact badge sizing/tokens used by the sibling knockout/source badges in that file — read first; the grid may need the new chip placed in an existing column rather than adding a column. Keep it compact.)

- [ ] **Step 2: Add purpose to `SignalInspector.tsx` read-only metadata**

In `SignalInspector.tsx`, in the read-only metadata block (where source/priority are shown), add a line showing purpose with a short explanation:
```tsx
<div className="text-[11px]">
  <span className="font-medium">{(signal.purpose ?? 'skill') === 'eligibility' ? 'Eligibility' : 'Skill'}</span>
  <span className="text-zinc-500"> · {(signal.purpose ?? 'skill') === 'eligibility' ? 'recruiter pre-screened' : 'tested in the AI screen'}</span>
</div>
```
(Match the inspector's existing metadata-row styling — read first.)

- [ ] **Step 3: Type-check + build + test**

Run:
```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit 2>&1 | tail -8
npm run build 2>&1 | tail -5
npm run test 2>&1 | tail -15
```
Expected: type-check clean, build succeeds, tests pass (the 5 pre-existing OrgUnitNode/TrackerJobCard/useTrackerJobs failures are unrelated — confirm no new failures; update any SignalRow/SignalInspector test fixture that constructs a `SignalItem` to include `purpose` if tsc requires it).

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add "frontend/app/components/dashboard/jd-panels/" frontend/app/tests
git commit -m "feat(app): show signal purpose (skill/eligibility) in read-only views"
```

---

## Task 7: Full-suite verification + live smoke

**Files:** none (verification).

- [ ] **Step 1: Backend gate**

Run: `docker compose run --rm nexus pytest tests/test_jd_state_machine.py tests/test_jd_router.py tests/test_jd_service_create.py tests/question_bank tests/test_question_banks_actors.py tests/test_question_banks_service.py -m "not prompt_quality" -q 2>&1 | tail -8`
Expected: 0 failed.

- [ ] **Step 2: Frontend gate**

Run: `cd /home/ishant/Projects/ProjectX/frontend/app && npx tsc --noEmit && npm run build 2>&1 | tail -5`
Expected: pass.

- [ ] **Step 3: Restart worker**

Run: `docker compose up -d --force-recreate nexus-worker`

- [ ] **Step 4: Live smoke (user-run)**

On the Workato job (`/jobs/ce6dad9a…?tab=jd`, currently `active`): click "Unlock & re-run extraction" → confirm the dialog → the page shows the extracting view → fresh v2 signals appear for review (~8–10, purpose badges visible, eligibility tagged); the question banks are now cleared (the questions tab shows "Generate"). Review + re-confirm the signals, regenerate the bank, and verify it's a scenario-dominant skills test within budget.

---

## Self-Review (plan vs spec)

- **Spec §3 (4 new transitions)** → Task 1. ✓
- **Spec §4 (re-extract endpoint: guards, clear banks, transition, dispatch, publish)** → Task 3. ✓
- **Spec §4.1 (`reset_banks_for_job`, public API, wipe+reset, no-op)** → Task 2. ✓
- **Spec §5 (api fn + hook + button + DangerConfirmDialog)** → Tasks 4, 5. ✓
- **Spec §6 (purpose in SignalRow + SignalInspector)** → Task 6. ✓
- **Spec §7 (tests incl. is_confirmed derivation)** → Tasks 1,2,3 (Step 5), 6; live smoke Task 7. ✓
- **Spec §8 (code-quality: public-API reset, isolated endpoint, atomic, legacy guard)** → enforced in Tasks 2,3,5,6. ✓

**Type consistency:** `reset_banks_for_job(db, *, job_id)` defined Task 2, called Task 3. `reExtractSignals`/`useReExtractSignals` defined Task 4, used Task 5. `_REEXTRACT_SOURCE_STATES` Task 3. `(s.purpose ?? 'skill')` guard consistent Tasks 5/6. Endpoint path `/re-extract-signals` consistent across backend (Task 3) + frontend (Task 4).
