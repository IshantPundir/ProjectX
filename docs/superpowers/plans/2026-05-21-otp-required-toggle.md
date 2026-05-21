# OTP-Required Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-persisted "OTP" toggle next to "Auto-invite" in the AI-Screening kanban column header so recruiters control whether candidates must pass an OTP gate.

**Architecture:** Persist to `job_pipeline_stages.otp_required_default` via a dedicated per-stage endpoint (mirroring `pause`/`unpause`) so toggling never bumps `pipeline_version` or invalidates question banks. Fix the pipeline GET response to actually serialize the column. Stop the auto-invite/resend paths from hardcoding `otp_required: true` so `send_invite` resolves from the persisted stage default.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), Next.js 16 + TanStack Query + Vitest (frontend).

Spec: `docs/superpowers/specs/2026-05-21-otp-required-toggle-design.md`

---

## File Structure

**Backend (`backend/nexus/`):**
- Modify `app/modules/pipelines/router.py` — read-path serialization fix + new endpoint + imports.
- Modify `app/modules/pipelines/service.py` — `_OTP_ALLOWED_TYPES` + `set_stage_otp_required`.
- Modify `app/modules/pipelines/errors.py` — `StageOtpNotApplicableError`.
- Modify `app/modules/pipelines/schemas.py` — `StageOtpRequiredRequest`.
- Create `tests/test_pipelines_otp_required.py` — endpoint + serialization tests.
- Modify `tests/test_scheduler_service.py` — stage-default inheritance test.

**Frontend (`frontend/app/`):**
- Modify `lib/api/pipelines.ts` — `setStageOtpRequired` fetcher.
- Create `lib/hooks/use-set-stage-otp.ts` — mutation hook.
- Modify `components/dashboard/tracker/CandidateKanbanColumn.tsx` — `OtpRequiredToggle` + `otpRequired` prop.
- Modify `components/dashboard/tracker/CandidateKanbanView.tsx` — thread prop, drop hardcoded OTP, fix toast.
- Modify `components/dashboard/tracker/CandidateKanbanCard.tsx` — drop hardcoded OTP, relabel.
- Create `tests/components/OtpRequiredToggle.test.tsx` — component test.

**Note on commits:** This repo commits only on explicit request and is currently on `main`. Do not commit per-task. After all tasks pass lint/type-check/test, branch first (`git switch -c feat/otp-required-toggle`) and commit only when the user approves (Task 9).

---

### Task 1: Backend — serialize `otp_required` in the pipeline GET response

**Files:**
- Modify: `app/modules/pipelines/router.py:123` (`_stage_row_to_response`)
- Test: `backend/nexus/tests/test_pipelines_otp_required.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_pipelines_otp_required.py`. Copy the auth/DB/fixture helpers verbatim from `tests/test_pipelines_pause.py` lines 1–183 (the imports, `_TEST_BEARER`, `_VALID_PROFILE`, `_setup_test_context`, `_setup_tenant`, `_make_job_with_pipeline`), changing only `_TEST_BEARER = "test-otp-token"` and the job title to `"OTP Test Job"`. Then add:

```python
@pytest.mark.asyncio
async def test_pipeline_get_serializes_otp_required(db: AsyncSession):
    """GET /pipeline must return otp_required for an instance stage (default False)."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            stages = (await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)).json()["stages"]
            phone = next(s for s in stages if s["stage_type"] == "phone_screen")
            assert phone["otp_required"] is False
    finally:
        restore()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_pipelines_otp_required.py::test_pipeline_get_serializes_otp_required -q`
Expected: FAIL — `otp_required` is `None`, not `False` (the response builder doesn't set it).

- [ ] **Step 3: Implement — map the column in `_stage_row_to_response`**

In `app/modules/pipelines/router.py`, inside the `PipelineStageResponse(...)` constructor in `_stage_row_to_response` (after `sla_days=row.sla_days,`), add:

```python
        # otp_required_default exists only on JobPipelineStage (instance rows),
        # not PipelineTemplateStage — getattr keeps template serialization safe,
        # mirroring the paused_at handling above.
        otp_required=getattr(row, "otp_required_default", None),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_pipelines_otp_required.py::test_pipeline_get_serializes_otp_required -q`
Expected: PASS

---

### Task 2: Backend — error type, service writer, request schema

**Files:**
- Modify: `app/modules/pipelines/errors.py`
- Modify: `app/modules/pipelines/service.py` (near `pause_stage`, ~line 1164)
- Modify: `app/modules/pipelines/schemas.py`

- [ ] **Step 1: Add the typed error**

In `app/modules/pipelines/errors.py`, append:

```python
class StageOtpNotApplicableError(Exception):
    """Raised when setting otp_required on a stage type that forbids it."""

    def __init__(self, stage_type: str) -> None:
        self.stage_type = stage_type
        super().__init__(f"OTP is not configurable for stage type '{stage_type}'")
```

- [ ] **Step 2: Add the service writer**

In `app/modules/pipelines/service.py`, immediately after `unpause_stage` (ends ~line 1217), add:

```python
# OTP is OPTIONAL only for these stage types (FORBIDDEN elsewhere — see
# schemas._FIELD_RULES_BY_TYPE).
_OTP_ALLOWED_TYPES: frozenset[str] = frozenset(
    {"phone_screen", "ai_screening", "human_interview"}
)


async def set_stage_otp_required(
    db: AsyncSession,
    *,
    stage: JobPipelineStage,
    otp_required: bool,
) -> JobPipelineStage:
    """Set otp_required_default on a stage.

    Forbidden for intake/debrief/take_home (raises StageOtpNotApplicableError).
    Idempotent. Does NOT bump pipeline_version or touch bank staleness — OTP is
    an invite-time gate, orthogonal to question-bank content.
    """
    if stage.stage_type not in _OTP_ALLOWED_TYPES:
        raise StageOtpNotApplicableError(stage.stage_type)
    if stage.otp_required_default == otp_required:
        return stage  # idempotent
    stage.otp_required_default = otp_required
    await db.flush()
    logger.info(
        "pipelines.stage_otp_required_set",
        stage_id=str(stage.id),
        stage_type=stage.stage_type,
        otp_required=otp_required,
    )
    return stage
```

Add `StageOtpNotApplicableError` to the existing `from app.modules.pipelines.errors import (...)` block at the top of `service.py` (alongside `StagePauseForbiddenError`).

- [ ] **Step 3: Add the request schema**

In `app/modules/pipelines/schemas.py`, after the stage schemas (after `PipelineStageResponse`), add:

```python
class StageOtpRequiredRequest(BaseModel):
    """Body for PATCH /pipeline/stages/{id}/otp-required."""

    model_config = ConfigDict(extra="forbid")
    otp_required: bool
```

(`BaseModel` and `ConfigDict` are already imported in this file.)

- [ ] **Step 4: Verify it imports cleanly**

Run: `docker compose run --rm nexus python -c "from app.modules.pipelines.service import set_stage_otp_required; from app.modules.pipelines.errors import StageOtpNotApplicableError; from app.modules.pipelines.schemas import StageOtpRequiredRequest; print('ok')"`
Expected: prints `ok`

---

### Task 3: Backend — the PATCH endpoint + audit

**Files:**
- Modify: `app/modules/pipelines/router.py` (imports + new endpoint after `unpause_stage_endpoint`, ~line 711)
- Test: `backend/nexus/tests/test_pipelines_otp_required.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipelines_otp_required.py`:

```python
@pytest.mark.asyncio
async def test_set_otp_required_on_phone_screen_succeeds(db: AsyncSession):
    """PATCH otp-required on phone_screen sets the default and returns it."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            v0 = r0.json()["pipeline_version"]
            phone = next(s for s in r0.json()["stages"] if s["stage_type"] == "phone_screen")

            r = await ac.patch(
                f"/api/jobs/{job.id}/pipeline/stages/{phone['id']}/otp-required",
                headers=headers,
                json={"otp_required": True},
            )
            assert r.status_code == 200, r.text
            updated = next(s for s in r.json()["stages"] if s["id"] == phone["id"])
            assert updated["otp_required"] is True
            # OTP toggle must NOT bump pipeline_version (banks stay valid).
            assert r.json()["pipeline_version"] == v0


@pytest.mark.asyncio
async def test_set_otp_required_on_intake_returns_422(db: AsyncSession):
    """PATCH otp-required on an intake stage must be rejected with 422."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            stages = (await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)).json()["stages"]
            intake = next(s for s in stages if s["stage_type"] == "intake")
            r = await ac.patch(
                f"/api/jobs/{job.id}/pipeline/stages/{intake['id']}/otp-required",
                headers=headers,
                json={"otp_required": True},
            )
            assert r.status_code == 422, r.text
            detail = r.json().get("detail", {})
            assert "otp_not_applicable" in (
                detail.get("code") if isinstance(detail, dict) else str(detail)
            )
    finally:
        restore()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/test_pipelines_otp_required.py -q`
Expected: the two new tests FAIL with 404/405 (endpoint not defined).

- [ ] **Step 3: Implement the endpoint**

In `app/modules/pipelines/router.py`:

(a) Extend the errors import block to include `StageOtpNotApplicableError`, the service import block to include `set_stage_otp_required`, and the schemas import block to include `StageOtpRequiredRequest`. Add at the top with the other module imports:

```python
from app.modules.audit import log_event
```

(b) After `unpause_stage_endpoint` (~line 711), add:

```python
@router.patch(
    "/api/jobs/{job_id}/pipeline/stages/{stage_id}/otp-required",
    response_model=JobPipelineInstanceResponse,
)
async def set_stage_otp_required_endpoint(
    job_id: UUID,
    stage_id: UUID,
    body: StageOtpRequiredRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPipelineInstanceResponse:
    """Set whether candidates must pass an OTP gate for this stage.

    Persists job_pipeline_stages.otp_required_default. send_invite reads this as
    the default when the invite body omits otp_required. Allowed only for
    phone_screen / ai_screening / human_interview (422 otherwise). Does not bump
    pipeline_version.
    """
    _job, instance = await require_instance_access(db, job_id, user, "manage")
    if instance is None:
        raise HTTPException(404, detail="No pipeline for this job")
    stage = await get_stage_in_instance(db, instance=instance, stage_id=stage_id)
    try:
        await set_stage_otp_required(db, stage=stage, otp_required=body.otp_required)
    except StageOtpNotApplicableError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "otp_not_applicable",
                "message": (
                    f"OTP is not configurable for stage type '{e.stage_type}'."
                ),
            },
        )
    await log_event(
        db,
        tenant_id=instance.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="pipeline.stage_otp_required_set",
        resource="pipeline_stage",
        resource_id=stage_id,
        payload={"otp_required": body.otp_required, "job_id": str(job_id)},
    )
    result = await get_job_pipeline_with_stages(db, job_id)
    if result is None:
        raise HTTPException(500, detail="Reload failed")
    new_instance, stages, source_template, participants_by_stage = result
    return _instance_to_response(new_instance, stages, source_template, participants_by_stage)
```

- [ ] **Step 4: Verify the audit signature**

Confirm `log_event`'s parameter names match (`tenant_id`, `actor_id`, `actor_email`, `action`, `resource`, `resource_id`, `payload`) — they are used identically in `app/modules/scheduler/service.py:111-126`. If `log_event` is not exported from `app.modules.audit`, import from `app.modules.audit.service`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/test_pipelines_otp_required.py -q`
Expected: all PASS

---

### Task 4: Backend — `send_invite` inherits the stage default when omitted

**Files:**
- Test: `backend/nexus/tests/test_scheduler_service.py`

This behavior already exists (`scheduler/service.py:74-77`); this task pins it with a regression test so the frontend can safely omit `otp_required`.

- [ ] **Step 1: Inspect the existing test file**

Run: `docker compose run --rm nexus pytest tests/test_scheduler_service.py -q` and open the file to find its fixture helpers for building an assignment on an `ai_screening` stage. Reuse them.

- [ ] **Step 2: Add the regression test**

Add a test that builds an assignment whose `current_stage` is an `ai_screening` stage with `otp_required_default=True`, calls `send_invite` with `InviteCreateRequest(assignment_id=..., otp_required=None)`, and asserts the created session's `otp_required is True`. Add a mirror case with the stage default `False` asserting the session's `otp_required is False`. Follow the existing test's construction and mocking of `send_email` exactly.

- [ ] **Step 3: Run to verify pass**

Run: `docker compose run --rm nexus pytest tests/test_scheduler_service.py -q`
Expected: PASS

---

### Task 5: Frontend — API client fetcher + mutation hook

**Files:**
- Modify: `frontend/app/lib/api/pipelines.ts` (inside `pipelinesApi`, after `unpauseStage`)
- Create: `frontend/app/lib/hooks/use-set-stage-otp.ts`

- [ ] **Step 1: Add the fetcher**

In `lib/api/pipelines.ts`, inside the `pipelinesApi` object after `unpauseStage`, add:

```typescript
  setStageOtpRequired: (
    token: string,
    jobId: string,
    stageId: string,
    otpRequired: boolean,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/otp-required`,
      {
        method: 'PATCH',
        token,
        body: JSON.stringify({ otp_required: otpRequired }),
      },
    ),
```

- [ ] **Step 2: Create the mutation hook**

Create `lib/hooks/use-set-stage-otp.ts`:

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { pipelinesApi, type JobPipelineInstance } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Persists a stage's OTP-required default (job_pipeline_stages.otp_required_default).
 * On success the returned instance is written straight into the job-pipeline
 * cache so the column toggle reflects the authoritative value without a refetch.
 */
export function useSetStageOtp(jobId: string) {
  const qc = useQueryClient()
  return useMutation<
    JobPipelineInstance,
    Error,
    { stageId: string; otpRequired: boolean }
  >({
    mutationFn: async ({ stageId, otpRequired }) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.setStageOtpRequired(token, jobId, stageId, otpRequired)
    },
    onSuccess: (instance) => {
      qc.setQueryData(['job-pipeline', jobId], instance)
    },
  })
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend/app && npm run type-check`
Expected: zero errors.

---

### Task 6: Frontend — `OtpRequiredToggle` in the column header

**Files:**
- Modify: `frontend/app/components/dashboard/tracker/CandidateKanbanColumn.tsx`
- Create: `frontend/app/tests/components/OtpRequiredToggle.test.tsx`

- [ ] **Step 1: Write the failing component test**

Create `frontend/app/tests/components/OtpRequiredToggle.test.tsx`:

```typescript
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { renderWithProviders } from '@/tests/_utils/render'
import { OtpRequiredToggle } from '@/components/dashboard/tracker/CandidateKanbanColumn'
import { pipelinesApi } from '@/lib/api/pipelines'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(async () => 'tok'),
}))

describe('OtpRequiredToggle', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('reflects the initial state from pipeline data', () => {
    renderWithProviders(
      <OtpRequiredToggle jobId="j1" stageId="s1" initial={true} />,
    )
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('calls setStageOtpRequired with the new value on toggle', async () => {
    const spy = vi
      .spyOn(pipelinesApi, 'setStageOtpRequired')
      .mockResolvedValue({
        id: 'i1',
        job_posting_id: 'j1',
        source_template_id: null,
        source_template_name: null,
        pipeline_version: 1,
        stages: [],
        created_at: '',
        updated_at: '',
      })
    renderWithProviders(
      <OtpRequiredToggle jobId="j1" stageId="s1" initial={false} />,
    )
    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('tok', 'j1', 's1', true),
    )
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- OtpRequiredToggle`
Expected: FAIL — `OtpRequiredToggle` is not exported.

- [ ] **Step 3: Implement the toggle + wire it into the header**

In `components/dashboard/tracker/CandidateKanbanColumn.tsx`:

(a) Add imports at the top:

```typescript
import { toast } from 'sonner'
import { useSetStageOtp } from '@/lib/hooks/use-set-stage-otp'
```

(b) Extend `Props` with the new optional field and accept it in the component signature:

```typescript
interface Props {
  stage: KanbanColumn
  jobId: string
  stageType: StageType | undefined
  /** Stage's persisted otp_required_default (from the pipeline lookup in the
   *  parent). Undefined while the pipeline is still loading. */
  otpRequired?: boolean
}
```

```typescript
export default function CandidateKanbanColumn({
  stage,
  jobId,
  stageType,
  otpRequired,
}: Props) {
```

(c) In the header, render the OTP toggle right after the Auto-invite toggle:

```typescript
        {stageType === 'ai_screening' && (
          <AutoInviteToggle jobId={jobId} stageId={stage.stage_id} />
        )}
        {stageType === 'ai_screening' && (
          <OtpRequiredToggle
            jobId={jobId}
            stageId={stage.stage_id}
            initial={otpRequired ?? false}
          />
        )}
```

(d) At the bottom of the file, add the exported toggle component (mirrors `AutoInviteToggle` ergonomics but backed by the API). Optimistic local state, synced from `initial` when the cache updates, reverts on error:

```typescript
/**
 * Column-header checkbox that persists this stage's OTP requirement to the
 * backend (job_pipeline_stages.otp_required_default). Unlike Auto-invite
 * (browser-local), OTP is a security control, so it is server-persisted and
 * shared across recruiters. Optimistic: flips immediately, reverts + toasts on
 * error. `initial` comes from pipeline data; we re-sync when it changes (the
 * mutation writes the fresh instance into the cache on success).
 */
export function OtpRequiredToggle({
  jobId,
  stageId,
  initial,
}: {
  jobId: string
  stageId: string
  initial: boolean
}) {
  const [enabled, setEnabled] = useState(initial)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEnabled(initial)
  }, [initial])

  const setOtp = useSetStageOtp(jobId)

  function handleChange(next: boolean) {
    setEnabled(next) // optimistic
    setOtp.mutate(
      { stageId, otpRequired: next },
      {
        onError: (err) => {
          setEnabled(!next) // revert
          toast.error(err.message || 'Failed to update OTP setting')
        },
      },
    )
  }

  return (
    <label
      className="inline-flex cursor-pointer items-center gap-1.5"
      style={{ color: 'var(--px-fg-3)' }}
      title="When enabled, candidates must verify a one-time code before the interview starts."
    >
      <input
        type="checkbox"
        checked={enabled}
        disabled={setOtp.isPending}
        onChange={(e) => handleChange(e.target.checked)}
        aria-label="Require OTP verification for this stage"
        className="cursor-pointer"
        style={{ width: 12, height: 12, accentColor: 'var(--px-accent)' }}
      />
      <span className="text-[10px] font-medium uppercase" style={{ letterSpacing: '0.4px' }}>
        OTP
      </span>
    </label>
  )
}
```

Note: `AutoInviteToggle`'s label uses `ml-auto` to push it to the right edge. With two toggles, keep `ml-auto` on `AutoInviteToggle` only (it already has it) so the pair sits together on the right.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/app && npm run test -- OtpRequiredToggle`
Expected: PASS (both cases)

---

### Task 7: Frontend — thread the prop + stop hardcoding OTP

**Files:**
- Modify: `frontend/app/components/dashboard/tracker/CandidateKanbanView.tsx`
- Modify: `frontend/app/components/dashboard/tracker/CandidateKanbanCard.tsx`

- [ ] **Step 1: Thread `otpRequired` into the column (View)**

In `CandidateKanbanView.tsx`, where `<CandidateKanbanColumn>` is rendered (~line 228), add the prop alongside `stageType`:

```typescript
        {data.stages.map((stage) => {
          const pstage = pipeline.data?.stages.find((s) => s.id === stage.stage_id)
          return (
            <CandidateKanbanColumn
              key={stage.stage_id}
              stage={stage}
              jobId={jobId}
              stageType={pstage?.stage_type}
              otpRequired={pstage?.otp_required ?? false}
            />
          )
        })}
```

- [ ] **Step 2: Drop hardcoded OTP from the auto-invite body + fix the toast**

In `CandidateKanbanView.tsx`, change the `autoInvite` mutation body (~line 61-66) to omit `otp_required` so the backend resolves the stage default:

```typescript
    mutationFn: async ({ assignmentId }) => {
      const token = await getFreshSupabaseToken()
      return schedulerApi.sendInvite(token, {
        assignment_id: assignmentId,
      })
    },
```

In the `onSuccess` of the `autoInvite.mutate` call inside `handleDragEnd` (~line 165-167), reflect the actual stage setting (computed from the pipeline data already captured in scope via `overData.stageId`):

```typescript
                onSuccess: () => {
                  const otpOn =
                    pipeline.data?.stages.find(
                      (s) => s.id === overData.stageId,
                    )?.otp_required ?? false
                  toast.success(
                    otpOn ? 'Invite sent (OTP required)' : 'Invite sent',
                  )
                },
```

- [ ] **Step 3: Drop hardcoded OTP from the card resend + relabel**

In `CandidateKanbanCard.tsx`, `handleResend` (~line 164-176): drop `otp_required: true` and update the toast:

```typescript
  function handleResend() {
    sendInvite.mutate(
      { assignment_id: card.assignment_id },
      {
        onSuccess: () => {
          toast.success('Invite re-sent')
        },
        onError: (err) => {
          toast.error(err.message || 'Failed to resend invite')
        },
      },
    )
  }
```

And the menu item label (~line 214):

```typescript
            {sendInvite.isPending ? 'Sending…' : 'Resend invite'}
```

- [ ] **Step 4: Type-check + run the tracker-related tests**

Run: `cd frontend/app && npm run type-check && npm run test -- OtpRequiredToggle SendInviteDialog`
Expected: zero type errors; tests PASS.

---

### Task 8: Full verification

- [ ] **Step 1: Backend tests**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_pipelines_otp_required.py tests/test_pipelines_pause.py tests/test_pipelines_router.py tests/test_scheduler_service.py -q`
Expected: all PASS.

- [ ] **Step 2: Backend lint**

Run: `docker compose run --rm nexus ruff check app/modules/pipelines tests/test_pipelines_otp_required.py`
Expected: clean.

- [ ] **Step 3: Frontend gates**

Run: `cd frontend/app && npm run lint && npm run type-check && npm run test`
Expected: zero lint errors, zero type errors, all tests pass.

- [ ] **Step 4: Manual smoke (optional, with stack up)**

Open `http://localhost:3000/tracker/<jobId>`, confirm an "OTP" checkbox sits next to "Auto-invite" on the Bot column, toggle it, reload, confirm the state persists (served from the backend). Drag a candidate in → invite toast reflects the OTP setting.

---

### Task 9: Branch + commit (only after user approval)

- [ ] **Step 1: Branch off main**

```bash
git switch -c feat/otp-required-toggle
```

- [ ] **Step 2: Commit backend + spec/plan**

```bash
git add backend/nexus/app/modules/pipelines backend/nexus/tests/test_pipelines_otp_required.py backend/nexus/tests/test_scheduler_service.py docs/superpowers
git commit -m "feat(pipelines): persist per-stage OTP-required default + dedicated endpoint

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Commit frontend**

```bash
git add frontend/app/lib/api/pipelines.ts frontend/app/lib/hooks/use-set-stage-otp.ts frontend/app/components/dashboard/tracker frontend/app/tests/components/OtpRequiredToggle.test.tsx
git commit -m "feat(tracker): OTP-required toggle on the Bot stage; stop hardcoding otp on invite

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Read-path fix → Task 1. ✓
- Service writer (no version bump / no bank staleness) → Task 2 + asserted in Task 3. ✓
- New endpoint + stage-type guard (422) + audit → Task 3. ✓
- `send_invite` inherits stage default → Task 4. ✓
- API client + hook → Task 5. ✓
- Toggle UI threaded from pipeline data → Tasks 6 + 7 step 1. ✓
- Stop hardcoding OTP on auto-invite + card resend → Task 7. ✓
- Tests (backend + frontend) → Tasks 1, 3, 4, 6, 8. ✓
- Out-of-scope items (StageConfigDrawer, bulk-PATCH bug) → intentionally untouched. ✓

**Deviation from spec test list:** The spec listed a frontend test for "auto-invite drag path sends a body without `otp_required: true`." There is no existing test harness for the @dnd-kit drag-drop flow in this repo, and simulating it is brittle. That assertion is instead covered by (a) the backend `send_invite` stage-default test (Task 4) and (b) the type/diff + manual smoke (Task 8 step 4). The automated frontend test focuses on the toggle component (Task 6).

**Placeholder scan:** No TBD/TODO; all code blocks are concrete. Task 4 references the existing test file's fixtures by location rather than reproducing them (the file's helpers vary; the engineer reuses what's there) — acceptable since it's mirroring an existing in-repo pattern, not inventing one.

**Type consistency:** `setStageOtpRequired(token, jobId, stageId, otpRequired)` is consistent across Task 5 (definition), the hook (Task 5), and the test (Task 6). `OtpRequiredToggle({ jobId, stageId, initial })` is consistent across Tasks 6 and the test. `StageOtpRequiredRequest.otp_required` (backend) matches the JSON body `{ otp_required }` sent by the fetcher. Endpoint path `/api/jobs/{jobId}/pipeline/stages/{stageId}/otp-required` is identical in router (Task 3) and fetcher (Task 5).
