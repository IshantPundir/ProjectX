# Phase 2C.2 Question Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the question bank generation system — per-stage rich structured questions, AI-generated with recruiter review, explicit confirmation gate for Phase 3 handoff.

**Architecture:** Two new tables (`stage_question_banks`, `stage_questions`) with RLS, 11 HTTP endpoints, 3 Dramatiq actors (per-stage / per-pipeline sequential / single-question regeneration), 7 prompt files (common + 5 stage types + regenerate-one), new `/jobs/[id]/questions` frontend route with sidebar + main pane. Plus a prerequisite 2C.1 fix so `update_job_pipeline_stages` preserves stage IDs through edits.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + Pydantic v2 + Dramatiq + OpenAI/instructor + Langfuse (backend). Next.js 16 + React 19 + TypeScript strict + TanStack Query + Tailwind v4 + shadcn v4 (frontend).

**Spec:** `docs/superpowers/specs/2026-04-12-phase-2c2-question-generation-design.md`

---

## File Structure

### Backend (new module `app/modules/question_bank/`)

```
backend/nexus/
├── migrations/versions/
│   └── 0006_question_banks.py                    NEW
├── app/
│   ├── models.py                                 MODIFY (+ StageQuestionBank, StageQuestion)
│   ├── modules/
│   │   ├── pipelines/
│   │   │   ├── schemas.py                        MODIFY (+ PipelineStageUpdateInput)
│   │   │   └── service.py                        MODIFY (rewrite update_job_pipeline_stages)
│   │   └── question_bank/
│   │       ├── __init__.py                       KEEP (already exists, empty)
│   │       ├── schemas.py                        REWRITE (currently 15-line stub)
│   │       ├── errors.py                         NEW
│   │       ├── state_machine.py                  NEW
│   │       ├── authz.py                          NEW
│   │       ├── service.py                        REWRITE (currently 5-line stub)
│   │       ├── actors.py                         NEW
│   │       ├── router.py                         REWRITE (currently 15-line stub)
│   │       └── sse.py                            NEW
│   ├── ai/
│   │   └── prompts.py                            MODIFY (+ load_pair method)
│   ├── worker.py                                 MODIFY (import actors)
│   └── main.py                                   MODIFY (register exception handlers)
├── prompts/v1/
│   ├── question_bank_common.txt                  NEW
│   ├── question_bank_phone_screen.txt            NEW
│   ├── question_bank_ai_interview.txt            NEW
│   ├── question_bank_human_interview.txt         NEW
│   ├── question_bank_panel_interview.txt         NEW
│   ├── question_bank_take_home.txt               NEW
│   └── question_bank_regenerate_one.txt          NEW
└── tests/
    ├── test_pipeline_stage_id_stability.py       NEW (regression for 2C.1 fix)
    ├── test_question_banks_schemas.py            NEW
    ├── test_question_banks_service.py            NEW
    ├── test_question_banks_authz.py              NEW
    ├── test_question_banks_actors.py             NEW
    ├── test_question_banks_router.py             NEW
    └── test_question_banks_integration.py        NEW
```

### Frontend (new surface under `/jobs/[id]/questions`)

```
frontend/app/
├── lib/
│   ├── api/
│   │   ├── pipelines.ts                          MODIFY (+ PipelineStageUpdateInput)
│   │   └── question-banks.ts                     NEW
│   └── hooks/
│       ├── use-banks-overview.ts                 NEW
│       ├── use-bank-with-questions.ts            NEW
│       ├── use-generate-questions.ts             NEW
│       ├── use-regenerate-question.ts            NEW
│       ├── use-save-question.ts                  NEW
│       ├── use-confirm-bank.ts                   NEW
│       └── use-questions-status-stream.ts        NEW
├── components/dashboard/question-bank/
│   ├── BankStatusBadge.tsx                       NEW
│   ├── QuestionsReviewContent.tsx                NEW
│   ├── QuestionSidebar.tsx                       NEW
│   ├── QuestionsMainPane.tsx                     NEW
│   ├── BankHeader.tsx                            NEW
│   ├── QuestionList.tsx                          NEW
│   ├── QuestionCard.tsx                          NEW
│   ├── QuestionEditForm.tsx                      NEW
│   ├── QuestionRubricExpanded.tsx                NEW
│   ├── AddCustomQuestionDialog.tsx               NEW
│   └── ConfirmBankDialog.tsx                     NEW
├── app/(dashboard)/jobs/[jobId]/
│   ├── questions/page.tsx                        NEW
│   └── pipeline/page.tsx                         MODIFY (+ Review questions link)
├── app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/
│   ├── new/page.tsx                              MODIFY (stop stripId on save)
│   └── [templateId]/page.tsx                     MODIFY (stop stripId on save)
└── tests/components/
    ├── QuestionCard.test.tsx                     NEW
    └── BankStatusBadge.test.tsx                  NEW
```

---

## Task 1: 2C.1 Stage ID Stability Fix — Backend

**Why first:** 2C.2 questions FK to `job_pipeline_stages.id`. Currently, `update_job_pipeline_stages` deletes and re-inserts all stages on every save, which would cascade-delete every question bank on every keystroke. This task makes stages survive edits.

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/schemas.py`
- Modify: `backend/nexus/app/modules/pipelines/service.py`
- Test: `backend/nexus/tests/test_pipeline_stage_id_stability.py`

- [ ] **Step 1: Add `PipelineStageUpdateInput` schema**

Open `backend/nexus/app/modules/pipelines/schemas.py`. Find `class UpdateJobPipelineRequest` (has `stages: list[PipelineStageInput]`). Add above it:

```python
class PipelineStageUpdateInput(PipelineStageInput):
    """Stage input used on UPDATE — carries optional id to preserve row identity.

    Existing stages pass their id; new stages (added via the UI "+ Add stage"
    button) omit it. The service's diff-and-sync update matches incoming items
    by id to existing rows.
    """

    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
```

Then change `UpdateJobPipelineRequest.stages` type to `list[PipelineStageUpdateInput]`.

Also ensure `from uuid import UUID` is imported at the top of the file.

- [ ] **Step 2: Write the failing regression test**

Create `backend/nexus/tests/test_pipeline_stage_id_stability.py`:

```python
"""Regression test for Phase 2C.1 / 2C.2 — stage IDs must survive edits
so question banks FK'd to stage_id don't get cascade-deleted on every save."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import JobPipelineStage
from app.modules.pipelines.schemas import (
    PipelineStageInput,
    PipelineStageUpdateInput,
    SignalFilter,
    PassCriteriaKnockout,
)
from app.modules.pipelines.service import (
    create_job_pipeline_from_scratch,
    update_job_pipeline_stages,
)

from tests.test_pipelines_service import _setup_tenant_user_unit, _set_tenant_ctx


def _make_stage_input(position: int, name: str) -> PipelineStageInput:
    return PipelineStageInput(
        position=position,
        name=name,
        stage_type="phone_screen",
        duration_minutes=10,
        difficulty="easy",
        signal_filter=SignalFilter(include_types=["competency", "experience"]),
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior="auto_advance",
    )


def _to_update_input(stage: JobPipelineStage) -> PipelineStageUpdateInput:
    return PipelineStageUpdateInput(
        id=stage.id,
        position=stage.position,
        name=stage.name,
        stage_type=stage.stage_type,  # type: ignore[arg-type]
        duration_minutes=stage.duration_minutes,
        difficulty=stage.difficulty,  # type: ignore[arg-type]
        signal_filter=SignalFilter(include_types=stage.signal_filter["include_types"]),
        pass_criteria=stage.pass_criteria,  # type: ignore[arg-type]
        advance_behavior=stage.advance_behavior,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_update_preserves_ids_when_all_stages_pass_their_id(
    async_session,
):
    """Editing existing stages with their IDs preserved leaves row UUIDs intact."""
    tenant, user, unit = await _setup_tenant_user_unit(async_session)
    await _set_tenant_ctx(async_session, tenant.id)

    # Create a job + signals_confirmed so pipeline creation is allowed
    from tests.test_pipelines_service import _make_confirmed_job
    job = await _make_confirmed_job(async_session, tenant.id, unit.id, user.id)

    # Create instance via scratch with 3 stages
    instance = await create_job_pipeline_from_scratch(
        async_session,
        job=job,
        stages=[
            _make_stage_input(0, "Screen"),
            _make_stage_input(1, "Interview"),
            _make_stage_input(2, "Panel"),
        ],
    )
    await async_session.flush()

    # Capture original stage IDs
    result = await async_session.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    original_stages = result.scalars().all()
    original_ids = [s.id for s in original_stages]

    # Update — pass all three existing stages' IDs unchanged
    await update_job_pipeline_stages(
        async_session,
        instance=instance,
        stages=[_to_update_input(s) for s in original_stages],
    )
    await async_session.flush()

    # Re-fetch
    result = await async_session.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    new_stages = result.scalars().all()
    new_ids = [s.id for s in new_stages]

    assert new_ids == original_ids, "Stage UUIDs must be preserved across update"


@pytest.mark.asyncio
async def test_update_inserts_new_stage_without_touching_existing(
    async_session,
):
    """Adding a new stage (no id) to the end inserts one row; existing rows unchanged."""
    tenant, user, unit = await _setup_tenant_user_unit(async_session)
    await _set_tenant_ctx(async_session, tenant.id)

    from tests.test_pipelines_service import _make_confirmed_job
    job = await _make_confirmed_job(async_session, tenant.id, unit.id, user.id)

    instance = await create_job_pipeline_from_scratch(
        async_session,
        job=job,
        stages=[_make_stage_input(0, "Screen"), _make_stage_input(1, "Interview")],
    )
    await async_session.flush()

    existing = (
        (
            await async_session.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    original_ids = [s.id for s in existing]

    # Pass existing 2 with their ids + 1 new without id
    updates: list[PipelineStageUpdateInput] = [
        _to_update_input(existing[0]),
        _to_update_input(existing[1]),
        PipelineStageUpdateInput(
            id=None,
            position=2,
            name="Panel",
            stage_type="panel_interview",
            duration_minutes=60,
            difficulty="hard",
            signal_filter=SignalFilter(
                include_types=["competency", "experience", "behavioral"],
            ),
            pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
            advance_behavior="manual_review",
        ),
    ]

    await update_job_pipeline_stages(async_session, instance=instance, stages=updates)
    await async_session.flush()

    final = (
        (
            await async_session.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    assert len(final) == 3
    assert final[0].id == original_ids[0]
    assert final[1].id == original_ids[1]
    assert final[2].id not in original_ids
    assert final[2].name == "Panel"


@pytest.mark.asyncio
async def test_update_removes_stage_when_id_omitted(async_session):
    """Deleting a stage from the incoming list drops that row and preserves others."""
    tenant, user, unit = await _setup_tenant_user_unit(async_session)
    await _set_tenant_ctx(async_session, tenant.id)

    from tests.test_pipelines_service import _make_confirmed_job
    job = await _make_confirmed_job(async_session, tenant.id, unit.id, user.id)

    instance = await create_job_pipeline_from_scratch(
        async_session,
        job=job,
        stages=[
            _make_stage_input(0, "Screen"),
            _make_stage_input(1, "Interview"),
            _make_stage_input(2, "Panel"),
        ],
    )
    await async_session.flush()

    existing = (
        (
            await async_session.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    screen_id, interview_id, _panel_id = [s.id for s in existing]

    # Remove the Panel — pass only the first two stages
    await update_job_pipeline_stages(
        async_session,
        instance=instance,
        stages=[_to_update_input(existing[0]), _to_update_input(existing[1])],
    )
    await async_session.flush()

    final = (
        (
            await async_session.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    assert len(final) == 2
    assert final[0].id == screen_id
    assert final[1].id == interview_id


@pytest.mark.asyncio
async def test_update_combines_add_and_remove_in_one_call(async_session):
    """Diff-and-sync: add one new + remove one existing + update one in place."""
    tenant, user, unit = await _setup_tenant_user_unit(async_session)
    await _set_tenant_ctx(async_session, tenant.id)

    from tests.test_pipelines_service import _make_confirmed_job
    job = await _make_confirmed_job(async_session, tenant.id, unit.id, user.id)

    instance = await create_job_pipeline_from_scratch(
        async_session,
        job=job,
        stages=[_make_stage_input(0, "Screen"), _make_stage_input(1, "OldPanel")],
    )
    await async_session.flush()

    existing = (
        (
            await async_session.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    screen_id = existing[0].id

    # Rename Screen (update in place), drop OldPanel, add new Interview + Panel
    renamed_screen = _to_update_input(existing[0])
    renamed_screen.name = "Phone Screen"
    new_interview = PipelineStageUpdateInput(
        id=None,
        position=1,
        name="Interview",
        stage_type="ai_interview",
        duration_minutes=45,
        difficulty="hard",
        signal_filter=SignalFilter(include_types=["competency", "experience"]),
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior="auto_advance",
    )
    new_panel = PipelineStageUpdateInput(
        id=None,
        position=2,
        name="Panel",
        stage_type="panel_interview",
        duration_minutes=60,
        difficulty="hard",
        signal_filter=SignalFilter(
            include_types=["competency", "experience", "behavioral"],
        ),
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior="manual_review",
    )

    await update_job_pipeline_stages(
        async_session,
        instance=instance,
        stages=[renamed_screen, new_interview, new_panel],
    )
    await async_session.flush()

    final = (
        (
            await async_session.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    assert len(final) == 3
    assert final[0].id == screen_id, "Screen row preserved via id match"
    assert final[0].name == "Phone Screen", "Fields updated in place"
    assert final[1].name == "Interview"
    assert final[2].name == "Panel"
```

This test file assumes `tests/test_pipelines_service.py` exposes `_setup_tenant_user_unit`, `_set_tenant_ctx`, and `_make_confirmed_job` helpers. If any is missing, add it to `test_pipelines_service.py` first (grep existing tests to see the current helper set; copy patterns).

- [ ] **Step 3: Run the tests — expect failures**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest tests/test_pipeline_stage_id_stability.py -x -q
```

Expected: FAIL — the service still deletes and re-inserts, so stage IDs will not match. Validator error on `PipelineStageUpdateInput` is also possible until step 1's schema changes are imported.

- [ ] **Step 4: Rewrite `update_job_pipeline_stages` with diff-and-sync**

Open `backend/nexus/app/modules/pipelines/service.py`. Find the current `update_job_pipeline_stages` (around line 543) and replace with:

```python
async def update_job_pipeline_stages(
    db: AsyncSession,
    *,
    instance: JobPipelineInstance,
    stages: list["PipelineStageUpdateInput"],
) -> JobPipelineInstance:
    """Replace the stages on a job pipeline instance via diff-and-sync.

    Matching rule: an incoming stage with id=X updates the existing row with
    that id in place, preserving the UUID. An incoming stage with id=None is
    inserted as a new row. Existing rows whose id is not in the incoming list
    are deleted. This preserves stage row identity across edits — critical so
    question banks FK'd to stage_id survive auto-save edits.
    """
    from app.modules.pipelines.schemas import PipelineStageUpdateInput  # local import to avoid cycle

    # Load current stages for the instance
    existing_result = await db.execute(
        select(JobPipelineStage).where(
            JobPipelineStage.instance_id == instance.id
        )
    )
    existing_list = list(existing_result.scalars().all())
    existing_by_id: dict[UUID, JobPipelineStage] = {s.id: s for s in existing_list}

    # Partition incoming
    incoming_by_id: dict[UUID, PipelineStageUpdateInput] = {}
    incoming_new: list[PipelineStageUpdateInput] = []
    for s in stages:
        if s.id is not None:
            incoming_by_id[s.id] = s
        else:
            incoming_new.append(s)

    # Update-in-place for matched existing stages
    for existing in existing_list:
        if existing.id in incoming_by_id:
            update = incoming_by_id[existing.id]
            existing.position = update.position
            existing.name = update.name
            existing.stage_type = update.stage_type
            existing.duration_minutes = update.duration_minutes
            existing.difficulty = update.difficulty
            existing.signal_filter = update.signal_filter.model_dump()
            existing.pass_criteria = (
                update.pass_criteria.model_dump()
                if hasattr(update.pass_criteria, "model_dump")
                else dict(update.pass_criteria)
            )
            existing.advance_behavior = update.advance_behavior
        else:
            # Existing row that the recruiter removed
            await db.delete(existing)

    # Insert new stages
    for new_stage in incoming_new:
        # Reuse the existing row-dict helper
        base_input = PipelineStageInput(
            position=new_stage.position,
            name=new_stage.name,
            stage_type=new_stage.stage_type,
            duration_minutes=new_stage.duration_minutes,
            difficulty=new_stage.difficulty,
            signal_filter=new_stage.signal_filter,
            pass_criteria=new_stage.pass_criteria,
            advance_behavior=new_stage.advance_behavior,
        )
        db.add(
            JobPipelineStage(
                **_stage_input_to_row_dict(
                    base_input, instance.tenant_id, instance_id=instance.id
                )
            )
        )

    instance.updated_at = _now_utc()
    await db.flush()
    logger.info(
        "pipelines.job_instance_stages_synced",
        instance_id=str(instance.id),
        updated=len([s for s in existing_list if s.id in incoming_by_id]),
        deleted=len([s for s in existing_list if s.id not in incoming_by_id]),
        inserted=len(incoming_new),
    )
    return instance
```

Ensure the `UUID` import is present (`from uuid import UUID`).

- [ ] **Step 5: Run the regression tests — expect pass**

```bash
docker compose run --rm nexus pytest tests/test_pipeline_stage_id_stability.py -x -q
```

Expected: 4 tests PASS.

- [ ] **Step 6: Run the full existing pipelines test suite — expect no regressions**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_service.py tests/test_pipelines_router.py -x -q
```

Expected: all existing tests still pass. If any test was sending an `UpdateJobPipelineRequest` body with plain `PipelineStageInput` (no id), those tests should still work — `id` defaults to `None`, which is the "new stage" path.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/pipelines/schemas.py \
        backend/nexus/app/modules/pipelines/service.py \
        backend/nexus/tests/test_pipeline_stage_id_stability.py
git commit -m "fix(pipelines): preserve stage IDs through edits (2C.1 prerequisite for 2C.2)"
```

---

## Task 2: 2C.1 Stage ID Stability Fix — Frontend

The backend now accepts stage IDs in the update body. The frontend currently strips them via `stripId` before saving. Change all save paths to carry IDs through.

**Files:**
- Modify: `frontend/app/lib/api/pipelines.ts`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/new/page.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/[templateId]/page.tsx`

- [ ] **Step 1: Add `PipelineStageUpdateInput` TypeScript type**

Open `frontend/app/lib/api/pipelines.ts`. Find `PipelineStageInput` type. After it, add:

```typescript
// Stage update shape — existing stages pass their id, new stages omit it.
// The backend's diff-and-sync uses id to preserve row UUIDs through edits
// so question banks FK'd to stage_id survive pipeline auto-save.
export type PipelineStageUpdateInput = PipelineStageInput & {
  id?: string
}
```

Then find `UpdateJobPipelineBody` (used by `saveJobPipeline`). Change its `stages` field type from `PipelineStageInput[]` to `PipelineStageUpdateInput[]`:

```typescript
export type UpdateJobPipelineBody = {
  stages: PipelineStageUpdateInput[]
}
```

The `updateTemplate` body (`UpdateTemplateBody`) uses `PipelineStageInput[]` for template stages. Templates also go through diff-and-sync. Change that too:

```typescript
export type UpdateTemplateBody = {
  name: string
  description: string | null
  stages: PipelineStageUpdateInput[]
}
```

- [ ] **Step 2: Carry stage IDs through local editor state in the job pipeline page**

Open `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`. Find the `stripId` helper and the `useState<PipelineStageInput[]>` call inside `JobPipelineEditor`:

```typescript
const [stages, setStages] = useState<PipelineStageInput[]>(() =>
  pipeline.stages.map(stripId),
)
```

Change the state type to carry optional ids. Replace the state + `stripId` helper with:

```typescript
import type { PipelineStageUpdateInput } from '@/lib/api/pipelines'

// ... inside JobPipelineEditor, replace the stages state:
const [stages, setStages] = useState<PipelineStageUpdateInput[]>(() =>
  pipeline.stages.map((s) => ({ ...s, id: s.id })),
)
```

Remove the `stripId` function entirely — it's no longer needed.

Search the file for `.map(stripId)` — there are additional call sites in `handleReset`, `onSuccess` of reset/swap mutations, and a few other places. Replace each with an inline spread that keeps the id:

```typescript
// Before:
setStages(fresh.stages.map(stripId))
// After:
setStages(fresh.stages.map((s) => ({ ...s, id: s.id })))
```

Also update `makeBlankStage(position)` so new stages have `id: undefined`:

```typescript
function makeBlankStage(position: number): PipelineStageUpdateInput {
  return {
    id: undefined,  // new stage — backend will assign a UUID
    position,
    name: 'New Stage',
    // ... rest unchanged
  }
}
```

The save flow already sends `{ stages }` via `useSaveJobPipeline` → `saveJobPipeline` → PATCH. Since the body type was updated in step 1, TypeScript will carry the new shape through.

- [ ] **Step 3: Same change in the "new template" page**

Open `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/new/page.tsx`. The create flow uses `PipelineStageInput[]` (no id, since templates are being created fresh). This page does NOT need changes — new templates have all-new stages.

**Confirm:** the `CreateTemplateBody` still accepts `PipelineStageInput[]` (not `PipelineStageUpdateInput[]`). Creating a template is always "all new stages", so ids are never needed. If the TS compiler complains, it's because `CreateTemplateBody` was widened — revert that narrowing.

- [ ] **Step 4: Same change in the "edit template" page**

Open `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/[templateId]/page.tsx`. This page DOES need changes — editing a template hits the same diff-and-sync path.

Find the inner `EditTemplateForm` component's `useState(() => template.stages.map(stripId))` initializer (or wherever it hydrates local state from `template.stages`). Replace similarly:

```typescript
const [stages, setStages] = useState<PipelineStageUpdateInput[]>(() =>
  template.stages.map((s) => ({ ...s, id: s.id })),
)
```

Remove the local `stripId` helper if it's defined here. Update `makeBlankStage` to return `PipelineStageUpdateInput` with `id: undefined`.

The save mutation (`useUpdateTemplate`) now receives `PipelineStageUpdateInput[]`, which TypeScript carries through because `UpdateTemplateBody.stages` was widened in step 1.

- [ ] **Step 5: Also update the pipeline template CRUD service on the backend to accept `PipelineStageUpdateInput` for template updates**

Templates also need diff-and-sync for the same reason — but wait, do they? Templates are PipelineTemplate rows; editing them updates their stages. If question banks are scoped to JOB pipeline stages (not template stages), template stage IDs don't matter for Phase 2C.2.

Check: the spec says `stage_question_banks.stage_id REFERENCES job_pipeline_stages(id)`. Not `pipeline_template_stages(id)`. So template stage IDs are not referenced by anything in 2C.2.

**Conclusion:** templates don't strictly need the diff-and-sync fix for 2C.2 correctness. But the frontend was updated to carry IDs through (step 4), which means the backend's `update_template_with_stages` service now receives `PipelineStageUpdateInput[]` instead of `PipelineStageInput[]`.

Either (a) also rewrite the template update service for symmetry, or (b) leave it untouched (backend accepts `PipelineStageInput[]`, which `PipelineStageUpdateInput` extends — the extra `id` field is silently ignored because `extra="ignore"` is the Pydantic default on `PipelineStageInput`).

**Choose (b) for minimal scope.** The template update service keeps its current behavior. Template stages still churn UUIDs on edit, but 2C.2 doesn't care because nothing references them.

Verify by running:

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
```

Expected: zero errors related to the pipeline or template pages.

- [ ] **Step 6: Smoke-test the frontend**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run lint
npm run test -- --run
npm run build
```

Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/lib/api/pipelines.ts \
        frontend/app/app/\(dashboard\)/jobs/\[jobId\]/pipeline/page.tsx \
        frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/pipeline-templates/\[templateId\]/page.tsx
git commit -m "fix(pipelines): carry stage IDs through save (frontend side of 2C.1 prerequisite)"
```

---

## Task 3: Alembic Migration + ORM Models

**Files:**
- Create: `backend/nexus/migrations/versions/0006_question_banks.py`
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Create the Alembic migration**

Create `backend/nexus/migrations/versions/0006_question_banks.py`:

```python
"""question banks + stage questions

Revision ID: 0006_question_banks
Revises: 0005_simplify_signal_filter
Create Date: 2026-04-12

Phase 2C.2 — Question Generation. Two new tables scoped per tenant with RLS.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_question_banks"
down_revision = "0005_simplify_signal_filter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- stage_question_banks ---
    op.create_table(
        "stage_question_banks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("prompt_version", sa.Text(), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("generation_error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('draft', 'generating', 'reviewing', 'confirmed', 'failed')",
            name="stage_question_banks_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["job_pipeline_stages.id"],
            ondelete="CASCADE", name="fk_stage_question_banks_stage",
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_id"], ["job_postings.id"],
            ondelete="CASCADE", name="fk_stage_question_banks_job",
        ),
        sa.ForeignKeyConstraint(
            ["signal_snapshot_id"], ["job_posting_signal_snapshots.id"],
            name="fk_stage_question_banks_signal_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["generated_by"], ["users.id"],
            name="fk_stage_question_banks_generated_by",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"],
            name="fk_stage_question_banks_confirmed_by",
        ),
    )

    op.create_index(
        "ix_stage_question_banks_stage",
        "stage_question_banks",
        ["stage_id"],
        unique=True,
    )
    op.create_index(
        "ix_stage_question_banks_job",
        "stage_question_banks",
        ["job_posting_id"],
    )
    op.create_index(
        "ix_stage_question_banks_tenant_status",
        "stage_question_banks",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_stage_question_banks_snapshot",
        "stage_question_banks",
        ["signal_snapshot_id"],
    )

    # RLS
    op.execute("ALTER TABLE stage_question_banks ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY "tenant_isolation" ON stage_question_banks
          USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("""
        CREATE POLICY "service_role_bypass" ON stage_question_banks
          USING (current_setting('app.bypass_rls', true) = 'true')
    """)

    # --- stage_questions ---
    op.create_table(
        "stage_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("signal_values", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("estimated_minutes", sa.Numeric(4, 1), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("follow_ups", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("positive_evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("red_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("rubric", postgresql.JSONB(), nullable=False),
        sa.Column("evaluation_hint", sa.Text(), nullable=False),
        sa.Column("edited_by_recruiter", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("position >= 0", name="stage_questions_position_nonneg"),
        sa.CheckConstraint(
            "source IN ('ai_generated', 'ai_regenerated', 'recruiter')",
            name="stage_questions_source_check",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id"], ["stage_question_banks.id"],
            ondelete="CASCADE", name="fk_stage_questions_bank",
        ),
    )

    op.create_index(
        "ix_stage_questions_bank_position",
        "stage_questions",
        ["bank_id", "position"],
        unique=True,
    )
    op.execute(
        "CREATE INDEX ix_stage_questions_signal_values_gin "
        "ON stage_questions USING GIN (signal_values)"
    )

    # RLS
    op.execute("ALTER TABLE stage_questions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY "tenant_isolation" ON stage_questions
          USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("""
        CREATE POLICY "service_role_bypass" ON stage_questions
          USING (current_setting('app.bypass_rls', true) = 'true')
    """)


def downgrade() -> None:
    op.drop_index("ix_stage_questions_signal_values_gin", table_name="stage_questions")
    op.drop_index("ix_stage_questions_bank_position", table_name="stage_questions")
    op.drop_table("stage_questions")
    op.drop_index("ix_stage_question_banks_snapshot", table_name="stage_question_banks")
    op.drop_index("ix_stage_question_banks_tenant_status", table_name="stage_question_banks")
    op.drop_index("ix_stage_question_banks_job", table_name="stage_question_banks")
    op.drop_index("ix_stage_question_banks_stage", table_name="stage_question_banks")
    op.drop_table("stage_question_banks")
```

- [ ] **Step 2: Add ORM models**

Open `backend/nexus/app/models.py`. Find the existing pipeline models (`JobPipelineInstance`, `JobPipelineStage`). After them, add:

```python
class StageQuestionBank(Base):
    __tablename__ = "stage_question_banks"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    stage_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job_pipeline_stages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job_posting_signal_snapshots.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, default="v1")
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StageQuestion(Base):
    __tablename__ = "stage_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    bank_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stage_question_banks.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    signal_values: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    estimated_minutes: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    follow_ups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    positive_evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    red_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rubric: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluation_hint: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by_recruiter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

Confirm these imports are at the top of `models.py`: `Text, Boolean, Numeric, ForeignKey, DateTime, func` from `sqlalchemy`; `JSONB, ARRAY, PGUUID` from `sqlalchemy.dialects.postgresql`; `Mapped, mapped_column` from `sqlalchemy.orm`. Most are likely already imported from existing models.

- [ ] **Step 3: Apply the migration**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus alembic upgrade head
```

Expected: migration runs, both tables created, RLS enabled. Verify with:

```bash
docker compose run --rm nexus alembic current
```

Expected: `0006_question_banks (head)`.

- [ ] **Step 4: Run the full backend test suite — expect no regressions**

```bash
docker compose run --rm nexus pytest -x -q
```

Expected: all existing tests pass. Failures here indicate a bad ORM definition — fix column types / constraints.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/migrations/versions/0006_question_banks.py \
        backend/nexus/app/models.py
git commit -m "feat(question-bank): add stage_question_banks + stage_questions tables with RLS (migration 0006)"
```

---

## Task 4: Pydantic Schemas + Errors + State Machine

**Files:**
- Rewrite: `backend/nexus/app/modules/question_bank/schemas.py`
- Create: `backend/nexus/app/modules/question_bank/errors.py`
- Create: `backend/nexus/app/modules/question_bank/state_machine.py`

- [ ] **Step 1: Create `errors.py`**

Create `backend/nexus/app/modules/question_bank/errors.py`:

```python
"""Custom exceptions raised by the question_bank service.

Each exception is mapped to an HTTP response in app/main.py via FastAPI
exception handlers. Exceptions carry structured data so the handlers can
produce specific error messages.
"""

from __future__ import annotations

from uuid import UUID


class BankNotFoundError(Exception):
    """The requested bank does not exist (or is invisible due to RLS)."""

    def __init__(self, bank_id: UUID | None = None, stage_id: UUID | None = None):
        self.bank_id = bank_id
        self.stage_id = stage_id
        super().__init__(f"Bank not found (bank_id={bank_id}, stage_id={stage_id})")


class QuestionNotFoundError(Exception):
    """The requested question does not exist."""

    def __init__(self, question_id: UUID):
        self.question_id = question_id
        super().__init__(f"Question not found: {question_id}")


class BankAlreadyGeneratingError(Exception):
    """Generation was triggered while another generation was already in progress."""

    def __init__(self, bank_id: UUID):
        self.bank_id = bank_id
        super().__init__(f"Bank {bank_id} is already in 'generating' state")


class BankNotInReviewingError(Exception):
    """Attempted to confirm a bank that is not in 'reviewing' state."""

    def __init__(self, bank_id: UUID, current_status: str):
        self.bank_id = bank_id
        self.current_status = current_status
        super().__init__(
            f"Cannot confirm bank {bank_id}: current status is "
            f"'{current_status}', expected 'reviewing'"
        )


class KnockoutUnprobedError(Exception):
    """A knockout signal has no mandatory question — blocks confirmation."""

    def __init__(self, signal_value: str, bank_id: UUID):
        self.signal_value = signal_value
        self.bank_id = bank_id
        super().__init__(
            f"Cannot confirm: knockout signal '{signal_value}' has no "
            f"mandatory question in bank {bank_id}"
        )


class DurationBudgetOutOfRangeError(Exception):
    """Sum of estimated_minutes is outside the 50–150% range at confirm time."""

    def __init__(self, bank_id: UUID, total_minutes: float, stage_minutes: int):
        self.bank_id = bank_id
        self.total_minutes = total_minutes
        self.stage_minutes = stage_minutes
        self.min_allowed = round(stage_minutes * 0.5, 1)
        self.max_allowed = round(stage_minutes * 1.5, 1)
        super().__init__(
            f"Question time budget ({total_minutes} min) is outside the allowed "
            f"range for this {stage_minutes}-minute stage "
            f"({self.min_allowed}–{self.max_allowed} min)"
        )


class SignalValueNotInSnapshotError(Exception):
    """A signal_value referenced by a question does not exist in the pinned snapshot."""

    def __init__(self, signal_value: str, snapshot_id: UUID):
        self.signal_value = signal_value
        self.snapshot_id = snapshot_id
        super().__init__(
            f"Signal value '{signal_value}' does not exist in snapshot {snapshot_id}"
        )


class SignalTypeNotAllowedError(Exception):
    """A question probes a signal whose type is not in the stage's include_types."""

    def __init__(self, signal_value: str, signal_type: str, allowed_types: list[str]):
        self.signal_value = signal_value
        self.signal_type = signal_type
        self.allowed_types = allowed_types
        super().__init__(
            f"Signal '{signal_value}' has type '{signal_type}' which is not in "
            f"this stage's allowed types {allowed_types}"
        )


class StarterNotSupportedError(Exception):
    """Placeholder for unsupported generation actions."""

    pass
```

- [ ] **Step 2: Create `state_machine.py`**

Create `backend/nexus/app/modules/question_bank/state_machine.py`:

```python
"""Per-bank state machine for question generation.

States: draft → generating → reviewing → confirmed
               ↓
            failed (with error)

Transitions are enforced by explicit helpers. The service layer calls these
rather than mutating bank.status directly.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Literal
from uuid import UUID

from app.models import StageQuestionBank
from app.modules.question_bank.errors import (
    BankAlreadyGeneratingError,
    BankNotInReviewingError,
)

BankStatus = Literal["draft", "generating", "reviewing", "confirmed", "failed"]

# Legal transitions. Each value is the set of statuses the left-hand state can move to.
# NOTE: auto-revert (confirmed → reviewing on edit) is a separate helper because
# it's triggered by data mutations, not explicit state transitions.
LEGAL: dict[BankStatus, set[BankStatus]] = {
    "draft": {"generating", "reviewing", "failed"},
    "generating": {"reviewing", "failed"},
    "reviewing": {"generating", "confirmed"},
    "confirmed": {"generating", "reviewing"},
    "failed": {"generating"},
}


def _now_utc() -> datetime:
    return datetime.now(UTC)


def transition_to_generating(bank: StageQuestionBank) -> None:
    """draft | reviewing | confirmed | failed → generating.

    Raises BankAlreadyGeneratingError if the bank is already generating.
    """
    if bank.status == "generating":
        raise BankAlreadyGeneratingError(bank_id=bank.id)
    if bank.status not in LEGAL or "generating" not in LEGAL[bank.status]:  # defensive
        raise BankAlreadyGeneratingError(bank_id=bank.id)
    bank.status = "generating"
    bank.generation_error = None
    bank.updated_at = _now_utc()


def transition_to_reviewing_after_generation(bank: StageQuestionBank, *, user_id: UUID) -> None:
    """generating → reviewing on LLM success."""
    assert bank.status == "generating", f"expected generating, got {bank.status}"
    bank.status = "reviewing"
    bank.generated_at = _now_utc()
    bank.generated_by = user_id
    bank.updated_at = _now_utc()


def transition_to_failed(bank: StageQuestionBank, *, error: str) -> None:
    """generating → failed with error message."""
    assert bank.status == "generating", f"expected generating, got {bank.status}"
    bank.status = "failed"
    bank.generation_error = error
    bank.updated_at = _now_utc()


def transition_to_confirmed(bank: StageQuestionBank, *, user_id: UUID) -> None:
    """reviewing → confirmed. Caller MUST run coverage + budget checks first."""
    if bank.status != "reviewing":
        raise BankNotInReviewingError(bank_id=bank.id, current_status=bank.status)
    bank.status = "confirmed"
    bank.confirmed_at = _now_utc()
    bank.confirmed_by = user_id
    bank.updated_at = _now_utc()


def auto_revert_on_edit(bank: StageQuestionBank) -> bool:
    """Called after any data mutation on a bank's questions.

    - confirmed → reviewing (clears confirmed_at / confirmed_by)
    - draft → reviewing (first recruiter content)
    - everything else → no change

    Returns True if the bank status changed.
    """
    if bank.status == "confirmed":
        bank.status = "reviewing"
        bank.confirmed_at = None
        bank.confirmed_by = None
        bank.updated_at = _now_utc()
        return True
    if bank.status == "draft":
        bank.status = "reviewing"
        bank.updated_at = _now_utc()
        return True
    return False
```

- [ ] **Step 3: Rewrite `schemas.py`**

Overwrite `backend/nexus/app/modules/question_bank/schemas.py` with:

```python
"""Pydantic schemas for the question_bank module.

Three groups:
1. LLM output schemas — what `instructor` validates from the LLM response
2. API request bodies — what FastAPI endpoints accept
3. API response shapes — what endpoints return

signal_values are TEXT, not UUID, because Phase 2B signals don't have
stable UUIDs (see the spec's data model section).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

BankStatus = Literal["draft", "generating", "reviewing", "confirmed", "failed"]
QuestionSource = Literal["ai_generated", "ai_regenerated", "recruiter"]


# ---------------------------------------------------------------------------
# LLM output schemas (validated by `instructor`)
# ---------------------------------------------------------------------------

class QuestionRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excellent: str = Field(
        ..., min_length=20, max_length=300,
        description="Anchor for top-of-scale — what a strong answer contains",
    )
    meets_bar: str = Field(
        ..., min_length=20, max_length=300,
        description="Anchor for middle — what an acceptable answer contains",
    )
    below_bar: str = Field(
        ..., min_length=20, max_length=300,
        description="Anchor for bottom — what a weak answer looks like",
    )


class GeneratedQuestion(BaseModel):
    """One question as returned by the LLM inside a StageQuestionBankOutput."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(..., ge=0)
    text: str = Field(..., min_length=10, max_length=500)
    signal_values: list[str] = Field(
        ..., min_length=1, max_length=3,
        description=(
            "Signal values from the pinned snapshot that this question probes. "
            "Must exactly match values in the snapshot's signals array."
        ),
    )
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool
    follow_ups: list[str] = Field(..., min_length=0, max_length=3)
    positive_evidence: list[str] = Field(..., min_length=3, max_length=5)
    red_flags: list[str] = Field(..., min_length=2, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)


class StageQuestionBankOutput(BaseModel):
    """Full LLM response for one stage's bank generation."""

    model_config = ConfigDict(extra="forbid")

    stage_summary: str = Field(..., min_length=20, max_length=300,
                                description="1-sentence: what this stage tests")
    questions: list[GeneratedQuestion] = Field(..., min_length=1, max_length=15)
    coverage_notes: str = Field(
        ..., min_length=20, max_length=500,
        description=(
            "Chain-of-thought: why you allocated questions this way. "
            "Captured by Langfuse for debugging — NOT stored in the DB."
        ),
    )


class SingleQuestionOutput(BaseModel):
    """LLM response for a single-question regeneration (the regen-one flow).

    Unlike the bulk output, this returns exactly one question — no wrapper.
    """

    model_config = ConfigDict(extra="forbid")

    question: GeneratedQuestion
    reasoning: str = Field(
        ..., min_length=20, max_length=500,
        description="Why this question covers the signal at the right angle",
    )


# ---------------------------------------------------------------------------
# API request bodies
# ---------------------------------------------------------------------------

class CreateQuestionBody(BaseModel):
    """POST /questions — add a hand-written custom question."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=10, max_length=500)
    signal_values: list[str] = Field(..., min_length=1, max_length=3)
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool = False
    follow_ups: list[str] = Field(default_factory=list, max_length=3)
    positive_evidence: list[str] = Field(default_factory=list, max_length=5)
    red_flags: list[str] = Field(default_factory=list, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)
    position: int | None = Field(default=None, ge=0)


class UpdateQuestionBody(BaseModel):
    """PATCH /questions/{id} — any subset of editable fields."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=10, max_length=500)
    signal_values: list[str] | None = Field(default=None, min_length=1, max_length=3)
    estimated_minutes: float | None = Field(default=None, gt=0, le=15)
    is_mandatory: bool | None = None
    follow_ups: list[str] | None = Field(default=None, max_length=3)
    positive_evidence: list[str] | None = Field(default=None, max_length=5)
    red_flags: list[str] | None = Field(default=None, max_length=3)
    rubric: QuestionRubric | None = None
    evaluation_hint: str | None = Field(default=None, min_length=10, max_length=200)
    position: int | None = Field(default=None, ge=0)


class ReorderBody(BaseModel):
    """PATCH /reorder — new question order as a list of UUIDs."""

    model_config = ConfigDict(extra="forbid")
    question_ids: list[UUID] = Field(..., min_length=1)


class RegenerateQuestionBody(BaseModel):
    """POST /questions/{id}/regenerate — optionally retarget to different signals."""

    model_config = ConfigDict(extra="forbid")
    replace_signal_values: list[str] | None = Field(
        default=None, min_length=1, max_length=3,
    )


# ---------------------------------------------------------------------------
# API response shapes
# ---------------------------------------------------------------------------

class QuestionResponse(BaseModel):
    id: UUID
    bank_id: UUID
    position: int
    source: QuestionSource
    text: str
    signal_values: list[str]
    estimated_minutes: float
    is_mandatory: bool
    follow_ups: list[str]
    positive_evidence: list[str]
    red_flags: list[str]
    rubric: QuestionRubric
    evaluation_hint: str
    edited_by_recruiter: bool
    created_at: datetime
    updated_at: datetime


class BankResponse(BaseModel):
    id: UUID
    stage_id: UUID
    job_posting_id: UUID
    signal_snapshot_id: UUID
    status: BankStatus
    prompt_version: str
    generation_error: str | None
    generated_at: datetime | None
    generated_by: UUID | None
    confirmed_at: datetime | None
    confirmed_by: UUID | None
    question_count: int       # derived, from len(questions)
    total_minutes: float      # derived, sum of estimated_minutes
    is_stale: bool            # derived, != latest confirmed snapshot
    created_at: datetime
    updated_at: datetime


class BankWithQuestionsResponse(BankResponse):
    questions: list[QuestionResponse]


class BanksOverviewResponse(BaseModel):
    banks: list[BankResponse]


class GenerateResponse(BaseModel):
    """202 body returned by any generate endpoint."""

    bank_id: UUID | None = None  # null for pipeline-level generate-all
    status: BankStatus = "generating"
```

- [ ] **Step 4: Smoke-test imports**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus python -c "
from app.modules.question_bank.schemas import GeneratedQuestion, StageQuestionBankOutput, QuestionRubric
from app.modules.question_bank.errors import BankNotFoundError, KnockoutUnprobedError
from app.modules.question_bank.state_machine import transition_to_generating, auto_revert_on_edit
print('OK')
"
```

Expected: prints `OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/schemas.py \
        backend/nexus/app/modules/question_bank/errors.py \
        backend/nexus/app/modules/question_bank/state_machine.py
git commit -m "feat(question-bank): schemas + errors + state machine"
```

---

## Task 5: Authz Helpers

**Files:**
- Create: `backend/nexus/app/modules/question_bank/authz.py`

- [ ] **Step 1: Create `authz.py`**

```python
"""Authz helpers for the question_bank module.

Walks bank/question → stage → instance → job → org_unit → ancestry to check
`jobs.view` / `jobs.manage` permission. Matches Phase 2C.1's pipelines/authz.py
pattern. Cross-tenant access returns 404 (RLS hides other tenants' rows).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.auth.context import UserContext
from app.modules.org_units.service import get_org_unit_ancestry

Action = Literal["view", "manage"]


async def _check_permission(
    db: AsyncSession,
    user: UserContext,
    org_unit_id: UUID,
    action: Action,
) -> bool:
    """Walk the org unit's ancestry and check if the user has jobs.{action} anywhere along it."""
    required = f"jobs.{action}"
    ancestry = await get_org_unit_ancestry(db, org_unit_id)
    return any(user.has_permission_in_unit(unit.id, required) for unit in ancestry)


async def require_bank_access(
    db: AsyncSession,
    bank_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[StageQuestionBank, JobPipelineStage, JobPosting]:
    """Load a bank and verify the user has `jobs.{action}` on some ancestor org unit.

    - Raises 404 if the bank doesn't exist (including cross-tenant via RLS)
    - Raises 403 if the bank exists but the user lacks permission
    """
    result = await db.execute(
        select(StageQuestionBank, JobPipelineStage, JobPipelineInstance, JobPosting)
        .join(JobPipelineStage, StageQuestionBank.stage_id == JobPipelineStage.id)
        .join(
            JobPipelineInstance,
            JobPipelineStage.instance_id == JobPipelineInstance.id,
        )
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(StageQuestionBank.id == bank_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Bank not found")
    bank, stage, _instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} questions for this job",
        )
    return bank, stage, job


async def require_bank_access_by_stage(
    db: AsyncSession,
    job_id: UUID,
    stage_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[StageQuestionBank | None, JobPipelineStage, JobPosting]:
    """Like require_bank_access but starts from a (job_id, stage_id) tuple.

    Returns (bank_or_None, stage, job). If the bank doesn't exist yet (draft
    before any generation), bank is None but stage + job are loaded so the
    service can create the bank.
    """
    result = await db.execute(
        select(JobPipelineStage, JobPipelineInstance, JobPosting)
        .join(
            JobPipelineInstance,
            JobPipelineStage.instance_id == JobPipelineInstance.id,
        )
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(
            JobPipelineStage.id == stage_id,
            JobPosting.id == job_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Stage not found for this job")
    stage, _instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} questions for this job",
        )

    # Try to load an existing bank for this stage (may not exist yet)
    bank_result = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id == stage_id)
    )
    bank = bank_result.scalar_one_or_none()
    return bank, stage, job


async def require_question_access(
    db: AsyncSession,
    question_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[StageQuestion, StageQuestionBank, JobPipelineStage, JobPosting]:
    """Load a question and walk up through bank → stage → instance → job for authz."""
    result = await db.execute(
        select(StageQuestion, StageQuestionBank, JobPipelineStage, JobPipelineInstance, JobPosting)
        .join(StageQuestionBank, StageQuestion.bank_id == StageQuestionBank.id)
        .join(JobPipelineStage, StageQuestionBank.stage_id == JobPipelineStage.id)
        .join(
            JobPipelineInstance,
            JobPipelineStage.instance_id == JobPipelineInstance.id,
        )
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(StageQuestion.id == question_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    question, bank, stage, _instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} this question",
        )
    return question, bank, stage, job


async def require_pipeline_access(
    db: AsyncSession,
    job_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[JobPipelineInstance, JobPosting]:
    """For pipeline-level operations (generate-all, banks overview, SSE stream).

    Raises 404 if no pipeline instance exists for the job.
    """
    result = await db.execute(
        select(JobPipelineInstance, JobPosting)
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(JobPosting.id == job_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No pipeline found for this job")
    instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} this pipeline",
        )
    return instance, job
```

- [ ] **Step 2: Smoke-test import**

```bash
docker compose run --rm nexus python -c "
from app.modules.question_bank.authz import (
    require_bank_access, require_bank_access_by_stage,
    require_question_access, require_pipeline_access,
)
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/question_bank/authz.py
git commit -m "feat(question-bank): ancestry-walking authz helpers"
```

---

## Task 6: Service Layer — Bank CRUD + State Transitions + Validators

**Files:**
- Rewrite: `backend/nexus/app/modules/question_bank/service.py`

- [ ] **Step 1: Rewrite `service.py`**

Overwrite `backend/nexus/app/modules/question_bank/service.py` with the full service implementation. This file is large (~550 lines) — the core logic is below. Copy verbatim:

```python
"""Question bank service layer.

Bank lifecycle, question CRUD, coverage/budget validators, and post-LLM
validation checks. All mutations call auto_revert_on_edit to keep the bank
status in sync after recruiter-side changes.

Audit logging: every state transition and every recruiter mutation calls
log_event so EEOC audits can trace who did what when.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.audit.service import log_event
from app.modules.question_bank.errors import (
    BankNotFoundError,
    DurationBudgetOutOfRangeError,
    KnockoutUnprobedError,
    QuestionNotFoundError,
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    GeneratedQuestion,
    QuestionRubric,
    UpdateQuestionBody,
)
from app.modules.question_bank.state_machine import (
    auto_revert_on_edit,
    transition_to_confirmed,
    transition_to_failed,
    transition_to_generating,
    transition_to_reviewing_after_generation,
)

logger = structlog.get_logger()


def _now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

async def get_latest_confirmed_snapshot(
    db: AsyncSession, job_id: UUID
) -> JobPostingSignalSnapshot | None:
    """Latest confirmed signal snapshot for a job, or None if none confirmed.

    Query: ORDER BY version DESC WHERE confirmed_at IS NOT NULL LIMIT 1.
    """
    result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(
            JobPostingSignalSnapshot.job_posting_id == job_id,
            JobPostingSignalSnapshot.confirmed_at.is_not(None),
        )
        .order_by(desc(JobPostingSignalSnapshot.version))
        .limit(1)
    )
    return result.scalar_one_or_none()


def _signal_by_value(
    snapshot: JobPostingSignalSnapshot, value: str
) -> dict[str, Any] | None:
    """Find a signal dict by its `value` field inside the snapshot's signals JSONB array."""
    for signal in snapshot.signals:
        if signal.get("value") == value:
            return signal
    return None


# ---------------------------------------------------------------------------
# Bank CRUD
# ---------------------------------------------------------------------------

async def ensure_bank_exists(
    db: AsyncSession,
    *,
    stage: JobPipelineStage,
    job: JobPosting,
) -> StageQuestionBank:
    """Get or create the bank for a given stage. Creates in 'draft' state
    pinned to the latest confirmed signal snapshot."""
    result = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
    )
    bank = result.scalar_one_or_none()
    if bank is not None:
        return bank

    snapshot = await get_latest_confirmed_snapshot(db, job.id)
    if snapshot is None:
        raise RuntimeError(
            f"Cannot create bank for job {job.id}: no confirmed signal snapshot exists. "
            "Generation should not be triggered until signals are confirmed."
        )

    bank = StageQuestionBank(
        tenant_id=job.tenant_id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="draft",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()
    logger.info(
        "question_bank.bank_created",
        bank_id=str(bank.id),
        stage_id=str(stage.id),
        job_id=str(job.id),
        snapshot_id=str(snapshot.id),
    )
    return bank


async def get_bank_questions(
    db: AsyncSession, bank_id: UUID
) -> list[StageQuestion]:
    """Load all questions in a bank, ordered by position."""
    result = await db.execute(
        select(StageQuestion)
        .where(StageQuestion.bank_id == bank_id)
        .order_by(StageQuestion.position)
    )
    return list(result.scalars().all())


async def compute_is_stale(
    db: AsyncSession, bank: StageQuestionBank
) -> bool:
    """True if the bank's pinned snapshot is not the job's latest confirmed one."""
    latest = await get_latest_confirmed_snapshot(db, bank.job_posting_id)
    if latest is None:
        return False  # no confirmed snapshot; bank can't be stale
    return bank.signal_snapshot_id != latest.id


async def get_banks_for_pipeline(
    db: AsyncSession, instance: JobPipelineInstance
) -> list[tuple[StageQuestionBank, int, float, bool]]:
    """Return (bank, question_count, total_minutes, is_stale) tuples for every
    bank in the pipeline, ordered by stage position. Missing banks are NOT
    included — caller is expected to handle 'no bank yet' states separately.
    """
    # Load stages in position order
    stage_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stage_result.scalars().all())

    # Cache latest confirmed snapshot once for staleness
    latest = await get_latest_confirmed_snapshot(db, instance.job_posting_id)
    latest_id = latest.id if latest else None

    out: list[tuple[StageQuestionBank, int, float, bool]] = []
    for stage in stages:
        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
        )
        bank = bank_result.scalar_one_or_none()
        if bank is None:
            continue

        q_result = await db.execute(
            select(StageQuestion).where(StageQuestion.bank_id == bank.id)
        )
        questions = list(q_result.scalars().all())
        question_count = len(questions)
        total_minutes = float(sum(q.estimated_minutes for q in questions))
        is_stale = latest_id is not None and bank.signal_snapshot_id != latest_id
        out.append((bank, question_count, total_minutes, is_stale))
    return out


# ---------------------------------------------------------------------------
# Validators (used at confirm time and by LLM post-validation)
# ---------------------------------------------------------------------------

async def validate_knockout_coverage(
    db: AsyncSession,
    bank: StageQuestionBank,
) -> None:
    """Raise KnockoutUnprobedError if any knockout signal lacks a mandatory question.

    Knockouts are determined by loading the pinned snapshot and checking the
    stage's signal_filter.include_types (only knockouts of matching type count
    — a behavioral knockout doesn't need to be covered in an ai_interview stage).
    """
    snapshot_result = await db.execute(
        select(JobPostingSignalSnapshot).where(
            JobPostingSignalSnapshot.id == bank.signal_snapshot_id
        )
    )
    snapshot = snapshot_result.scalar_one_or_none()
    if snapshot is None:
        raise RuntimeError(f"Pinned snapshot {bank.signal_snapshot_id} missing")

    stage_result = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
    )
    stage = stage_result.scalar_one()
    allowed_types = stage.signal_filter.get("include_types", [])

    questions = await get_bank_questions(db, bank.id)
    # Build set of signal values covered by mandatory questions
    mandatory_values: set[str] = set()
    for q in questions:
        if q.is_mandatory:
            mandatory_values.update(q.signal_values)

    for signal in snapshot.signals:
        if not signal.get("knockout", False):
            continue
        if signal.get("type") not in allowed_types:
            continue
        if signal["value"] not in mandatory_values:
            raise KnockoutUnprobedError(
                signal_value=signal["value"], bank_id=bank.id
            )


async def validate_duration_budget(
    db: AsyncSession,
    bank: StageQuestionBank,
) -> None:
    """Raise DurationBudgetOutOfRangeError if sum outside 50–150% of stage duration."""
    stage_result = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
    )
    stage = stage_result.scalar_one()

    questions = await get_bank_questions(db, bank.id)
    total = float(sum(q.estimated_minutes for q in questions))
    min_allowed = stage.duration_minutes * 0.5
    max_allowed = stage.duration_minutes * 1.5

    if total < min_allowed or total > max_allowed:
        raise DurationBudgetOutOfRangeError(
            bank_id=bank.id,
            total_minutes=total,
            stage_minutes=stage.duration_minutes,
        )


async def validate_llm_output_against_snapshot(
    db: AsyncSession,
    *,
    snapshot: JobPostingSignalSnapshot,
    allowed_types: list[str],
    questions: list[GeneratedQuestion],
) -> list[GeneratedQuestion]:
    """Run post-LLM validation checks. Returns the (possibly auto-corrected) list.

    - signal_values must all exist in the snapshot → SignalValueNotInSnapshotError
    - signal types must be in allowed_types → SignalTypeNotAllowedError
    - knockout signals → is_mandatory auto-corrected to True (warning logged)
    """
    snapshot_by_value = {s["value"]: s for s in snapshot.signals}

    for q in questions:
        for value in q.signal_values:
            if value not in snapshot_by_value:
                raise SignalValueNotInSnapshotError(
                    signal_value=value, snapshot_id=snapshot.id
                )
            signal = snapshot_by_value[value]
            if signal["type"] not in allowed_types:
                raise SignalTypeNotAllowedError(
                    signal_value=value,
                    signal_type=signal["type"],
                    allowed_types=allowed_types,
                )

        # Auto-correct is_mandatory for knockouts
        probes_knockout = any(
            snapshot_by_value[v].get("knockout", False) for v in q.signal_values
        )
        if probes_knockout and not q.is_mandatory:
            logger.warning(
                "question_bank.auto_corrected_mandatory",
                signal_values=q.signal_values,
                reason="knockout_signal_without_mandatory",
            )
            q.is_mandatory = True
    return questions


# ---------------------------------------------------------------------------
# Write questions (used by actors after LLM success)
# ---------------------------------------------------------------------------

async def write_generated_questions(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    questions: list[GeneratedQuestion],
    source: str = "ai_generated",
) -> None:
    """Delete existing AI-sourced questions, keep recruiter-sourced ones,
    write the new generated questions. Called by the generate actors.
    """
    # Delete all AI-sourced questions (ai_generated + ai_regenerated)
    await db.execute(
        delete(StageQuestion).where(
            StageQuestion.bank_id == bank.id,
            StageQuestion.source.in_(["ai_generated", "ai_regenerated"]),
        )
    )
    await db.flush()

    # Find the max position among remaining (recruiter) questions so new ones
    # slot in after them — simpler than re-packing
    existing = await get_bank_questions(db, bank.id)
    existing_max_pos = max((q.position for q in existing), default=-1)
    offset = existing_max_pos + 1

    for incoming in questions:
        db.add(
            StageQuestion(
                tenant_id=bank.tenant_id,
                bank_id=bank.id,
                position=offset + incoming.position,
                source=source,
                text=incoming.text,
                signal_values=list(incoming.signal_values),
                estimated_minutes=incoming.estimated_minutes,
                is_mandatory=incoming.is_mandatory,
                follow_ups=list(incoming.follow_ups),
                positive_evidence=list(incoming.positive_evidence),
                red_flags=list(incoming.red_flags),
                rubric=incoming.rubric.model_dump(),
                evaluation_hint=incoming.evaluation_hint,
            )
        )
    await db.flush()

    # Re-pack positions to 0..N-1 so the final ordering is clean
    final = await get_bank_questions(db, bank.id)
    for i, q in enumerate(final):
        q.position = i
    await db.flush()


async def replace_question_in_place(
    db: AsyncSession,
    *,
    question: StageQuestion,
    new_data: GeneratedQuestion,
) -> None:
    """Update an existing question row with new LLM-generated data. Preserves id."""
    question.text = new_data.text
    question.signal_values = list(new_data.signal_values)
    question.estimated_minutes = new_data.estimated_minutes
    question.is_mandatory = new_data.is_mandatory
    question.follow_ups = list(new_data.follow_ups)
    question.positive_evidence = list(new_data.positive_evidence)
    question.red_flags = list(new_data.red_flags)
    question.rubric = new_data.rubric.model_dump()
    question.evaluation_hint = new_data.evaluation_hint
    question.source = "ai_regenerated"
    question.edited_by_recruiter = False
    question.updated_at = _now_utc()
    await db.flush()


# ---------------------------------------------------------------------------
# Recruiter mutations
# ---------------------------------------------------------------------------

async def create_recruiter_question(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    body: CreateQuestionBody,
    user_id: UUID,
    user_email: str | None,
    snapshot: JobPostingSignalSnapshot,
    allowed_types: list[str],
) -> StageQuestion:
    """Add a hand-written question. source='recruiter'."""
    # Validate signals exist + types allowed
    for value in body.signal_values:
        signal = _signal_by_value(snapshot, value)
        if signal is None:
            raise SignalValueNotInSnapshotError(
                signal_value=value, snapshot_id=snapshot.id
            )
        if signal["type"] not in allowed_types:
            raise SignalTypeNotAllowedError(
                signal_value=value,
                signal_type=signal["type"],
                allowed_types=allowed_types,
            )

    # Determine position
    existing = await get_bank_questions(db, bank.id)
    if body.position is None:
        position = len(existing)
    else:
        position = min(body.position, len(existing))
        # Shift existing questions down
        for q in existing:
            if q.position >= position:
                q.position += 1

    question = StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=position,
        source="recruiter",
        text=body.text,
        signal_values=list(body.signal_values),
        estimated_minutes=body.estimated_minutes,
        is_mandatory=body.is_mandatory,
        follow_ups=list(body.follow_ups),
        positive_evidence=list(body.positive_evidence),
        red_flags=list(body.red_flags),
        rubric=body.rubric.model_dump(),
        evaluation_hint=body.evaluation_hint,
        edited_by_recruiter=False,
    )
    db.add(question)
    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.recruiter_question_created",
        resource="stage_question",
        resource_id=question.id,
        payload={"bank_id": str(bank.id), "position": position},
    )
    return question


async def update_question(
    db: AsyncSession,
    *,
    question: StageQuestion,
    bank: StageQuestionBank,
    body: UpdateQuestionBody,
    user_id: UUID,
    user_email: str | None,
    snapshot: JobPostingSignalSnapshot,
    allowed_types: list[str],
) -> StageQuestion:
    """Partial update of a question. Validates signal_values if provided."""
    data = body.model_dump(exclude_unset=True)

    if "signal_values" in data:
        for value in data["signal_values"]:
            signal = _signal_by_value(snapshot, value)
            if signal is None:
                raise SignalValueNotInSnapshotError(
                    signal_value=value, snapshot_id=snapshot.id
                )
            if signal["type"] not in allowed_types:
                raise SignalTypeNotAllowedError(
                    signal_value=value,
                    signal_type=signal["type"],
                    allowed_types=allowed_types,
                )

    # Handle position changes separately (may need to shift others)
    new_position = data.pop("position", None)

    # Apply simple scalar + list updates
    for key, value in data.items():
        if key == "rubric" and value is not None:
            question.rubric = QuestionRubric(**value).model_dump()
        else:
            setattr(question, key, value)

    if new_position is not None and new_position != question.position:
        await _move_question_to_position(db, bank.id, question, new_position)

    question.edited_by_recruiter = True
    question.updated_at = _now_utc()
    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.question_edited",
        resource="stage_question",
        resource_id=question.id,
        payload={"bank_id": str(bank.id), "fields": list(data.keys())},
    )
    return question


async def _move_question_to_position(
    db: AsyncSession,
    bank_id: UUID,
    question: StageQuestion,
    new_position: int,
) -> None:
    """Move a question to a new position, re-packing the rest to 0..N-1."""
    siblings = await get_bank_questions(db, bank_id)
    siblings = [q for q in siblings if q.id != question.id]
    new_position = max(0, min(new_position, len(siblings)))
    siblings.insert(new_position, question)
    for i, q in enumerate(siblings):
        q.position = i
    await db.flush()


async def delete_question(
    db: AsyncSession,
    *,
    question: StageQuestion,
    bank: StageQuestionBank,
    user_id: UUID,
    user_email: str | None,
) -> None:
    """Delete a question and re-pack remaining positions."""
    await db.delete(question)
    await db.flush()

    # Re-pack
    remaining = await get_bank_questions(db, bank.id)
    for i, q in enumerate(remaining):
        q.position = i

    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.question_deleted",
        resource="stage_question",
        resource_id=question.id,
        payload={"bank_id": str(bank.id)},
    )


async def reorder_questions(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    question_ids: list[UUID],
    user_id: UUID,
    user_email: str | None,
) -> None:
    """Set positions 0..N-1 from the given order. Validates the set matches."""
    existing = await get_bank_questions(db, bank.id)
    existing_ids = {q.id for q in existing}
    incoming_ids = set(question_ids)

    if existing_ids != incoming_ids:
        raise ValueError(
            "Reorder list must contain exactly the existing question IDs"
        )
    if len(question_ids) != len(incoming_ids):
        raise ValueError("Reorder list contains duplicates")

    by_id = {q.id: q for q in existing}
    for i, qid in enumerate(question_ids):
        by_id[qid].position = i
    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.questions_reordered",
        resource="stage_question_bank",
        resource_id=bank.id,
    )


async def confirm_bank(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    user_id: UUID,
    user_email: str | None,
) -> StageQuestionBank:
    """Transition bank to 'confirmed' after running all validators."""
    await validate_knockout_coverage(db, bank)
    await validate_duration_budget(db, bank)
    transition_to_confirmed(bank, user_id=user_id)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.bank_confirmed",
        resource="stage_question_bank",
        resource_id=bank.id,
    )
    return bank


# Re-export state transitions for convenience
__all__ = [
    "ensure_bank_exists",
    "get_bank_questions",
    "get_banks_for_pipeline",
    "compute_is_stale",
    "get_latest_confirmed_snapshot",
    "validate_knockout_coverage",
    "validate_duration_budget",
    "validate_llm_output_against_snapshot",
    "write_generated_questions",
    "replace_question_in_place",
    "create_recruiter_question",
    "update_question",
    "delete_question",
    "reorder_questions",
    "confirm_bank",
    "transition_to_generating",
    "transition_to_reviewing_after_generation",
    "transition_to_failed",
]
```

- [ ] **Step 2: Smoke-test import**

```bash
docker compose run --rm nexus python -c "
from app.modules.question_bank.service import (
    ensure_bank_exists, confirm_bank, validate_knockout_coverage,
    write_generated_questions, create_recruiter_question,
)
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py
git commit -m "feat(question-bank): service layer — CRUD, state transitions, validators"
```

---

## Task 7: Prompt Files

**Files:**
- Create: `backend/nexus/prompts/v1/question_bank_common.txt`
- Create: `backend/nexus/prompts/v1/question_bank_phone_screen.txt`
- Create: `backend/nexus/prompts/v1/question_bank_ai_interview.txt`
- Create: `backend/nexus/prompts/v1/question_bank_human_interview.txt`
- Create: `backend/nexus/prompts/v1/question_bank_panel_interview.txt`
- Create: `backend/nexus/prompts/v1/question_bank_take_home.txt`
- Create: `backend/nexus/prompts/v1/question_bank_regenerate_one.txt`

These are plain-text prompt files. The spec's Section 3 ("Generation Flow + Prompt Design") defines the principles each file must teach. Each file should be 80–200 lines and written with real enterprise-grade prompt-engineering care (structured sections, explicit rules, banned patterns, examples). Do NOT copy marketing text — write dense, operational instructions to the LLM.

- [ ] **Step 1: Create `question_bank_common.txt`**

This is the shared header. Every per-type prompt is concatenated AFTER this file. Contents MUST include (quoting from the spec, Section "The common header establishes five principles"):

1. **Role framing:** "You are an enterprise hiring intelligence system. Your job is to generate structured, audit-grade interview questions for one specific stage of one specific hiring pipeline."

2. **Inputs you will receive:** enumerate them — job context (enriched JD, role summary, seniority, company profile), job signals (full list with metadata), this stage's metadata, full pipeline context (all stages with their metadata), prior stages' generated questions (if any).

3. **Core principles** (in priority order):
   - **Anti-lie invariant.** Full instruction: "Candidates can bluff shallow questions... Later stages exist specifically to catch liars by re-probing the same signals at greater depth." Then the weight-based rules:
     - `weight=3 or knockout` already probed → MUST re-probe at greater depth, different angle
     - `weight=2` already probed → re-probe if budget allows, harder angle
     - `weight=1` already probed → skip, use budget for uncovered signals
     - Signals not yet probed → probe at this stage's depth target
   - **Evidence-based scoring.** Every question must have 3–5 positive evidence items, 2–3 red flags, 3-level rubric. EXPLICIT BAN on scripted example answers with the exact language: "Do NOT write 'the candidate describes a time they debugged a production issue.' Write what to LISTEN FOR: 'names specific observability tools (logs, APM, tracing); describes hypothesis-verify loop; mentions blameless post-mortem.' Anchors describe what an answer contains, never what the answer is."
   - **Signal allocation math.** "Allocate questions proportionally to weight × priority. weight=3 or knockout → 2 questions (one verification, one depth). weight=2 → 1 question. weight=1 → 0–1. Cap by duration budget — each Q takes ~3–8 min including answer and follow-ups. Sum of estimated_minutes should be 85–105% of stage duration."
   - **Mandatory knockouts.** "Any signal with knockout=true gets a corresponding question with is_mandatory=true, appearing early in stage order (low position number)."
   - **Company tone.** "The company profile (`about`, `industry`, `company_stage`, `hiring_bar`) should subtly shape question phrasing. A scrappy Series A startup doesn't ask FAANG-style questions. A mature enterprise doesn't ask early-stage-style questions. Calibrate difficulty using `hiring_bar`. Use `industry` and `about` for domain-appropriate examples. NEVER mention the company name in the question text — tone only."

4. **Banned patterns** (what NOT to generate):
   - Scripted example answers or candidate dialogue
   - Generic slop ("Tell me about yourself", "What's your biggest weakness")
   - Vague positive_evidence ("gives detailed answer")
   - Duplicate questions across the pipeline (see anti-lie rules)
   - Signals filtered out by stage.signal_filter.include_types
   - More questions than the duration budget allows
   - Hallucinated technical details or tools not in the JD/signals

5. **Output schema.** "You will output a structured JSON object matching `StageQuestionBankOutput`. `instructor` validates your output against the Pydantic schema — any deviation will be rejected and you will be asked to retry with the validation error. Every field is required. Counts are enforced (e.g. `positive_evidence` must have 3–5 items, `red_flags` must have 2–3, `follow_ups` must have 0–3, `signal_values` must have 1–3)."

6. **The `signal_values` rule (CRITICAL):** Include this verbatim: "Each question has a `signal_values` field listing which signals it probes. These MUST be exact string matches to values in the snapshot's signals array. Do NOT paraphrase. Do NOT invent new signal values. The system will reject any question that references a signal value that doesn't exist in the pinned snapshot."

Save to `backend/nexus/prompts/v1/question_bank_common.txt`. Aim for ~120 lines.

- [ ] **Step 2: Create `question_bank_phone_screen.txt`**

Append per-stage-type instructions. Target content:

```
## Stage type: Phone Screen

This is a SHORT SCREENING CALL (target: 5–15 minutes). The conductor is an
AI bot. The candidate is being vetted for basic qualification before investing
human time.

### Question style
- Short and direct. 1–2 sentences per question.
- Closed or semi-closed. "Do you have X?" / "What's your experience with Y?"
  Not "Walk me through..."
- Verification-focused. Goal is to confirm the candidate isn't bluffing basics,
  not to probe for mastery.
- Knockout-heavy. Most questions should map to knockout or weight=3 signals.
  Skip weight=1 unless budget is open.

### Question count
- 3–5 questions for 5–10 min stages
- 5–8 questions for 10–15 min stages
- Never more than 8 even if budget allows — candidate needs breathing room

### Depth target: SHALLOW
- Follow-ups are 1–2 quick clarifications, not deep probes
- Example: "Have you worked with Kubernetes in production?" → follow-up:
  "What was your cluster size roughly?"
- NOT: "Describe your approach to Kubernetes capacity planning at scale."
  (That's ai_interview territory.)

### Evidence style for phone screen
- Positive evidence items should be observable in 30 seconds of answer:
  "names specific cluster size", "mentions production deployment",
  "describes a specific incident handled".
- Red flags: "cannot name specific tooling", "only tutorial experience",
  "vague 'we used Kubernetes' with no specifics".

### Mandatory
- Every knockout signal must have exactly one mandatory question here
  (phone screen is the first line of defense against unqualified applicants).
```

Save the full text to the file. ~50 lines.

- [ ] **Step 3: Create `question_bank_ai_interview.txt`**

```
## Stage type: AI Technical Interview

Long-form deep interview (target: 30–60 minutes). The conductor is an AI bot
with strong probing behavior. This is the technical depth stage.

### Question style
- Open-ended, hypothesis-verify flow.
- "Walk me through...", "How would you approach...", "Describe a time..."
- Technical depth focused. Probe HOW the candidate thinks, not just WHAT
  they know.
- Re-probe signals from phone screen at greater depth (anti-lie invariant).

### Question count
- 6–8 questions for 30–45 min stages
- 8–10 questions for 45–60 min stages

### Depth target: DEEP
- Follow-ups are multi-level drill-downs, not clarifications
- Example: "Walk me through debugging a production 5xx storm through an
  Apigee proxy" → "What tools did you use to trace the request?" →
  "How did you distinguish between backend slowness and proxy overhead?"
- Follow-ups should be ready for the common answers — pre-written for the
  AI bot to fire based on what the candidate says.

### Allocation
- Critical signals (weight=3 / knockout) get 2 questions: one depth probe,
  one re-verification at greater depth than phone screen
- weight=2 signals get 1 depth question each
- weight=1 signals are usually skipped — no budget to waste
- Skip behavioral signals entirely — those belong in human/panel stages

### Evidence style
- Positive evidence should be substantial: "demonstrates understanding of
  tradeoffs", "names specific tools AND explains why they chose them",
  "walks through failure modes and mitigations"
- Red flags: "surface-level answers without specifics", "cannot defend
  design choices", "memorized textbook answers without application context"
```

Save. ~60 lines.

- [ ] **Step 4: Create `question_bank_human_interview.txt`**

```
## Stage type: Human Interview (1-on-1)

Interview delivered by a single human interviewer, not an AI bot. Target:
45–60 minutes. Mix of technical and behavioral.

### Question style
- Structured behavioral + technical mix.
- Written for a human to deliver — natural phrasing, room for judgment.
- Include "why" and "how" probes that a human can adapt.

### Question count
- 6–8 questions for 45 min
- 8–10 questions for 60 min

### Depth target: MEDIUM to DEEP
- Follow-ups are guides for the human, not scripts.
- "If the candidate mentions X, probe for Y."

### Allocation
- Prioritize behavioral signals — humans are better at reading them than AI
- Also cover competency signals at depth (re-verify from AI interview if it
  was in the pipeline before this stage)
- Skip credential signals (verified via documents)

### Evidence style
- Evidence items written for a human interviewer to look for during the answer
- Red flags: include "evasive on specifics", "defensive when challenged",
  "blames others for past failures"
```

Save. ~50 lines.

- [ ] **Step 5: Create `question_bank_panel_interview.txt`**

```
## Stage type: Panel Interview

Multi-interviewer panel (typically 2–4 interviewers, 60–90 minutes). Senior
calibration stage — the final check before offer.

### Question style
- Calibration-grade questions designed for multiple interviewers to compare notes
- Mix of competency, experience, behavioral
- Suitable for "each panelist asks 2–3 questions" distribution

### Question count
- 8–10 questions for 60 min
- 10–12 questions for 90 min

### Depth target: DEEP
- Questions must be answerable in depth — panelists will probe for 5–10 min each
- Re-verify the most critical signals (weight=3 + knockouts) with DIFFERENT
  angles from earlier stages. The anti-lie invariant applies MAXIMALLY here —
  this is the last chance to catch a candidate who has been bluffing.

### Allocation
- Every critical signal gets one panel-level question regardless of whether
  it was probed in earlier stages
- Behavioral signals are heavily represented (panel is where culture fit is
  calibrated)
- Skip credential and take-home signals

### Evidence style
- Include "calibration hints" in the evaluation_hint so different interviewers
  converge on similar ratings
- Red flags: "inconsistent with earlier stage answers", "cannot scale up the
  discussion to senior-level tradeoffs"
```

Save. ~55 lines.

- [ ] **Step 6: Create `question_bank_take_home.txt`**

```
## Stage type: Take-home Assignment

Asynchronous assignment (target: 1–3 hours of candidate work). Delivered as
a problem statement with structured evaluation criteria. NOT a live interview.

### Output shape
- Generate EXACTLY ONE "question" which is actually a full problem statement
- The `text` field is the problem statement (can be longer than usual —
  up to 500 characters)
- `follow_ups` are the deliverables expected (README, code, tests, etc.)
- `positive_evidence` lists the specific things a strong submission contains
- `red_flags` list common failure modes in submissions
- `rubric` describes what a strong / acceptable / weak submission looks like
- `estimated_minutes` reflects the expected candidate time investment

### Question style
- Real-world problem that exercises 1–2 core competency signals
- Clear deliverable list
- No tricks, no puzzles — production-adjacent problems only

### Depth target: DEEP
- The submission itself is the evidence
```

Save. ~40 lines.

- [ ] **Step 7: Create `question_bank_regenerate_one.txt`**

This is a surgical prompt used to replace ONE question in an existing bank. It is NOT concatenated with the stage-type prompts — it's a standalone file.

```
## Single-question regeneration

You are regenerating ONE question in an existing pipeline stage's question
bank. You receive:

1. Full job context (enriched JD, signals, company profile)
2. The stage's metadata (type, duration, difficulty, signal_filter.include_types)
3. The CURRENT question being replaced (its text, signal_values, rubric)
4. The OTHER questions in this stage's bank (as "do not duplicate" context)
5. Optionally: `replace_signal_values` — different signals to probe instead
   of the current question's signals

## Your task

Produce ONE new question that:
- Probes the same signals as the current question (or the replacement signals
  if provided)
- Takes roughly the same `estimated_minutes` as the current question
- Has a DIFFERENT angle than the current question — not a minor rewording
- Does NOT functionally duplicate any of the OTHER questions in the bank
- Respects this stage's depth target (shallow/medium/deep based on stage
  difficulty)
- Meets all the evidence + rubric quality requirements from the common prompt

## Output

Return a `SingleQuestionOutput` with:
- `question`: the new GeneratedQuestion
- `reasoning`: 1–3 sentences explaining how this question covers the signal(s)
  at a different angle from the other questions in the bank

Everything else from the common prompt still applies — evidence must be
concrete, rubric must describe content not script, do not fabricate tools.
```

Save. ~40 lines.

- [ ] **Step 8: Verify all 7 files exist**

```bash
ls -la backend/nexus/prompts/v1/question_bank_*.txt
```

Expected: 7 files listed (common, phone_screen, ai_interview, human_interview, panel_interview, take_home, regenerate_one).

- [ ] **Step 9: Commit**

```bash
git add backend/nexus/prompts/v1/question_bank_*.txt
git commit -m "feat(question-bank): prompts — common + 5 stage types + regenerate-one"
```

---

## Task 8: PromptLoader Extension + Dramatiq Actors

**Files:**
- Modify: `backend/nexus/app/ai/prompts.py`
- Create: `backend/nexus/app/modules/question_bank/actors.py`
- Modify: `backend/nexus/app/worker.py`

- [ ] **Step 1: Extend PromptLoader with `load_pair`**

Open `backend/nexus/app/ai/prompts.py`. Add a method after `get()`:

```python
    def load_pair(self, common_name: str, type_name: str) -> str:
        """Concatenate a common header file with a per-type specialization file.

        Used by question_bank actors: common = 'question_bank_common',
        type = 'question_bank_phone_screen'. Returns header + '\\n\\n' + type.
        """
        header = self.get(common_name)
        specialization = self.get(type_name)
        return f"{header}\n\n{specialization}"
```

- [ ] **Step 2: Create `actors.py`**

Create `backend/nexus/app/modules/question_bank/actors.py`:

```python
"""Dramatiq actors for question bank generation.

Three actors:
- generate_question_bank_stage: generate ONE stage's bank
- generate_question_bank_pipeline: generate ALL stages sequentially
- regenerate_question: replace ONE question in an existing bank

All actors use get_bypass_session and SET LOCAL app.current_tenant for RLS,
matching the pattern from app/modules/jd/actors.py.
"""

from __future__ import annotations

from uuid import UUID

import dramatiq
import structlog
from sqlalchemy import select
from sqlalchemy.sql import text

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.database import get_bypass_session
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    OrgUnit,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.audit.service import log_event
from app.modules.org_units.service import get_org_unit_ancestry
from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    SingleQuestionOutput,
    StageQuestionBankOutput,
)
from app.modules.question_bank.service import (
    compute_is_stale,
    get_bank_questions,
    replace_question_in_place,
    transition_to_failed,
    transition_to_generating,
    transition_to_reviewing_after_generation,
    validate_llm_output_against_snapshot,
    write_generated_questions,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------

STAGE_TYPE_TO_PROMPT = {
    "phone_screen": "question_bank_phone_screen",
    "ai_interview": "question_bank_ai_interview",
    "human_interview": "question_bank_human_interview",
    "panel_interview": "question_bank_panel_interview",
    "take_home": "question_bank_take_home",
}


async def _find_company_profile(
    db, *, org_unit_id: UUID
) -> dict | None:
    """Walk ancestry to find the nearest org unit with a company_profile set."""
    ancestry = await get_org_unit_ancestry(db, org_unit_id)
    for unit in ancestry:
        if unit.company_profile:
            return unit.company_profile
    return None


async def _load_pipeline_context(
    db, *, instance_id: UUID
) -> list[dict]:
    """Load all stages in the instance with their metadata, ordered by position."""
    result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance_id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(result.scalars().all())
    return [
        {
            "id": str(s.id),
            "position": s.position,
            "name": s.name,
            "stage_type": s.stage_type,
            "duration_minutes": s.duration_minutes,
            "difficulty": s.difficulty,
            "advance_behavior": s.advance_behavior,
        }
        for s in stages
    ]


async def _load_prior_stages_questions(
    db, *, instance_id: UUID, current_position: int
) -> list[dict]:
    """Load questions from stages with position < current_position, grouped by stage."""
    stage_result = await db.execute(
        select(JobPipelineStage)
        .where(
            JobPipelineStage.instance_id == instance_id,
            JobPipelineStage.position < current_position,
        )
        .order_by(JobPipelineStage.position)
    )
    prior_stages = list(stage_result.scalars().all())

    out = []
    for stage in prior_stages:
        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
        )
        bank = bank_result.scalar_one_or_none()
        questions: list[dict] = []
        if bank is not None:
            q_result = await db.execute(
                select(StageQuestion)
                .where(StageQuestion.bank_id == bank.id)
                .order_by(StageQuestion.position)
            )
            for q in q_result.scalars().all():
                questions.append(
                    {
                        "position": q.position,
                        "text": q.text,
                        "signal_values": q.signal_values,
                        "is_mandatory": q.is_mandatory,
                        "rubric_meets_bar": q.rubric.get("meets_bar", ""),
                    }
                )
        out.append(
            {
                "stage_name": stage.name,
                "stage_type": stage.stage_type,
                "duration_minutes": stage.duration_minutes,
                "difficulty": stage.difficulty,
                "questions": questions,
            }
        )
    return out


def _build_user_message(
    *,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
    company_profile: dict | None,
    stage: JobPipelineStage,
    pipeline_stages: list[dict],
    prior_stages_questions: list[dict],
) -> str:
    """Build the user message — all context for the LLM.

    Order matters: context (company profile + JD + signals) BEFORE the stage-
    specific instructions. This matches the 'prompt_context_ordering' rule
    established in Phase 2A.
    """
    parts = []

    parts.append("# JOB CONTEXT\n")
    parts.append(f"Job title: {job.title}\n")
    parts.append(f"Role summary: {snapshot.role_summary}\n")
    parts.append(f"Seniority: {snapshot.seniority_level}\n")
    if job.description_enriched:
        parts.append(
            f"\n## Enriched JD\n\n{job.description_enriched}\n"
        )

    if company_profile:
        parts.append("\n# COMPANY PROFILE\n")
        for key in ("about", "industry", "company_stage", "hiring_bar"):
            if key in company_profile:
                parts.append(f"{key}: {company_profile[key]}\n")

    parts.append("\n# SIGNALS TO ASSESS (pinned snapshot)\n")
    parts.append(
        "Each signal is listed with its metadata. Use the `value` field exactly "
        "as-is in your question's `signal_values` output.\n\n"
    )
    for signal in snapshot.signals:
        parts.append(
            f"- value: {signal['value']!r}\n"
            f"  type: {signal['type']}\n"
            f"  priority: {signal['priority']}\n"
            f"  weight: {signal['weight']}\n"
            f"  knockout: {signal.get('knockout', False)}\n"
            f"  stage_tag: {signal['stage']}\n"
        )

    parts.append("\n# PIPELINE CONTEXT\n")
    current_idx = next(
        (i for i, s in enumerate(pipeline_stages) if s["id"] == str(stage.id)),
        0,
    )
    parts.append(
        f"This pipeline has {len(pipeline_stages)} stages. "
        f"You are generating questions for STAGE {current_idx + 1}.\n\n"
    )

    for i, s in enumerate(pipeline_stages):
        is_current = s["id"] == str(stage.id)
        marker = " (CURRENT — you are generating this)" if is_current else ""
        parts.append(
            f"## Stage {i + 1} — {s['name']}{marker}\n"
            f"  Type: {s['stage_type']}, Duration: {s['duration_minutes']} min, "
            f"Difficulty: {s['difficulty']}\n"
        )

        if not is_current and i < current_idx and i < len(prior_stages_questions):
            prior = prior_stages_questions[i]
            if prior["questions"]:
                parts.append(
                    f"  Already generated questions ({len(prior['questions'])}):\n"
                )
                for q in prior["questions"]:
                    mandatory = " [MANDATORY]" if q["is_mandatory"] else ""
                    parts.append(
                        f"    Q{q['position']}{mandatory} "
                        f"(probes: {q['signal_values']}):\n"
                        f"      {q['text']}\n"
                        f"      Rubric meets_bar: {q['rubric_meets_bar']}\n"
                    )

    parts.append("\n# THIS STAGE'S METADATA\n")
    parts.append(
        f"Name: {stage.name}\n"
        f"Type: {stage.stage_type}\n"
        f"Duration: {stage.duration_minutes} min\n"
        f"Difficulty: {stage.difficulty}\n"
        f"Signal type filter (include_types): "
        f"{stage.signal_filter.get('include_types', [])}\n"
        f"Advance behavior: {stage.advance_behavior}\n"
    )
    parts.append(
        "\nNow generate the structured question bank output as specified "
        "in the system instructions.\n"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Core generation function (shared by the stage and pipeline actors)
# ---------------------------------------------------------------------------

async def _generate_one_bank(
    db,
    *,
    bank: StageQuestionBank,
    stage: JobPipelineStage,
    instance: JobPipelineInstance,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
) -> None:
    """Run generation for one bank. Must be called with bank.status='generating'.
    On success → transitions to reviewing. On error → transitions to failed.
    Caller must commit or rollback."""
    try:
        company_profile = await _find_company_profile(db, org_unit_id=job.org_unit_id)
        pipeline_stages = await _load_pipeline_context(
            db, instance_id=instance.id
        )
        prior_stages_questions = await _load_prior_stages_questions(
            db, instance_id=instance.id, current_position=stage.position
        )

        type_prompt = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)
        if type_prompt is None:
            raise RuntimeError(f"No prompt file mapped for stage_type={stage.stage_type}")

        system_prompt = prompt_loader.load_pair("question_bank_common", type_prompt)
        user_message = _build_user_message(
            job=job,
            snapshot=snapshot,
            company_profile=company_profile,
            stage=stage,
            pipeline_stages=pipeline_stages,
            prior_stages_questions=prior_stages_questions,
        )

        client = get_openai_client()
        result: StageQuestionBankOutput = await client.chat.completions.create(
            model=ai_config.question_bank_model,
            response_model=StageQuestionBankOutput,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_retries=2,
        )

        logger.info(
            "question_bank.llm_response_received",
            bank_id=str(bank.id),
            question_count=len(result.questions),
            coverage_notes_preview=result.coverage_notes[:100],
        )

        # Post-validate
        allowed_types = stage.signal_filter.get("include_types", [])
        validated = await validate_llm_output_against_snapshot(
            db,
            snapshot=snapshot,
            allowed_types=allowed_types,
            questions=result.questions,
        )

        # Write questions to the DB (wipes prior AI-sourced, keeps recruiter-sourced)
        await write_generated_questions(
            db, bank=bank, questions=validated, source="ai_generated"
        )

        # Transition bank → reviewing
        transition_to_reviewing_after_generation(
            bank, user_id=bank.generated_by or UUID(int=0)
        )
    except Exception as exc:
        logger.error(
            "question_bank.generation_failed",
            bank_id=str(bank.id),
            error=str(exc),
            exc_info=True,
        )
        transition_to_failed(bank, error=str(exc)[:500])
        raise


# ---------------------------------------------------------------------------
# Actor: single stage
# ---------------------------------------------------------------------------

@dramatiq.actor(
    max_retries=2,
    min_backoff=2_000,
    max_backoff=60_000,
    queue_name="question_bank_generation",
)
async def generate_question_bank_stage(
    bank_id: str,
    tenant_id: str,
    started_by: str,
) -> None:
    """Generate questions for ONE stage's bank. Retries on transient failures.

    Before the first call, the router must have:
    - Ensured the bank exists
    - Set bank.status = 'generating'
    - Committed so the actor sees the updated state
    """
    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.id == UUID(bank_id))
        )
        bank = bank_result.scalar_one_or_none()
        if bank is None:
            logger.error("question_bank.bank_missing", bank_id=bank_id)
            return

        # Load stage, instance, job, snapshot
        stage_result = await db.execute(
            select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
        )
        stage = stage_result.scalar_one()
        instance_result = await db.execute(
            select(JobPipelineInstance).where(
                JobPipelineInstance.id == stage.instance_id
            )
        )
        instance = instance_result.scalar_one()
        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == bank.job_posting_id)
        )
        job = job_result.scalar_one()
        snap_result = await db.execute(
            select(JobPostingSignalSnapshot).where(
                JobPostingSignalSnapshot.id == bank.signal_snapshot_id
            )
        )
        snapshot = snap_result.scalar_one()

        try:
            await _generate_one_bank(
                db,
                bank=bank,
                stage=stage,
                instance=instance,
                job=job,
                snapshot=snapshot,
            )
            await log_event(
                db,
                tenant_id=UUID(tenant_id),
                actor_id=UUID(started_by),
                actor_email=None,
                action="question_bank.bank_generated",
                resource="stage_question_bank",
                resource_id=bank.id,
            )
            await db.commit()
        except Exception:
            await db.commit()  # commit the 'failed' status
            raise


# ---------------------------------------------------------------------------
# Actor: full pipeline (sequential — required for anti-lie coherence)
# ---------------------------------------------------------------------------

@dramatiq.actor(
    max_retries=0,
    time_limit=600_000,  # 10 minutes
    queue_name="question_bank_generation",
)
async def generate_question_bank_pipeline(
    instance_id: str,
    tenant_id: str,
    started_by: str,
) -> None:
    """Generate banks for ALL stages in a pipeline, sequentially.

    Sequential is REQUIRED — stage N needs to see stages 1..N-1's questions.
    On mid-pipeline failure: marks that stage failed, CONTINUES to next stage.
    User retries failed stages individually via the single-stage endpoint.
    """
    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        instance_result = await db.execute(
            select(JobPipelineInstance).where(
                JobPipelineInstance.id == UUID(instance_id)
            )
        )
        instance = instance_result.scalar_one_or_none()
        if instance is None:
            logger.error("question_bank.instance_missing", instance_id=instance_id)
            return

        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == instance.job_posting_id)
        )
        job = job_result.scalar_one()

        stages_result = await db.execute(
            select(JobPipelineStage)
            .where(JobPipelineStage.instance_id == instance.id)
            .order_by(JobPipelineStage.position)
        )
        stages = list(stages_result.scalars().all())

        succeeded = 0
        failed = 0
        for stage in stages:
            # Ensure bank exists and is in generating state
            from app.modules.question_bank.service import ensure_bank_exists
            bank = await ensure_bank_exists(db, stage=stage, job=job)
            try:
                transition_to_generating(bank)
                await db.flush()
            except Exception as exc:
                logger.warning(
                    "question_bank.skip_busy_stage",
                    stage_id=str(stage.id),
                    reason=str(exc),
                )
                continue

            snap_result = await db.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.id == bank.signal_snapshot_id
                )
            )
            snapshot = snap_result.scalar_one()

            try:
                await _generate_one_bank(
                    db,
                    bank=bank,
                    stage=stage,
                    instance=instance,
                    job=job,
                    snapshot=snapshot,
                )
                succeeded += 1
                await db.flush()
            except Exception as exc:
                logger.error(
                    "question_bank.pipeline_stage_failed",
                    stage_id=str(stage.id),
                    error=str(exc),
                )
                failed += 1
                # _generate_one_bank already transitioned the bank to failed
                await db.flush()
                continue  # move to next stage

        await log_event(
            db,
            tenant_id=UUID(tenant_id),
            actor_id=UUID(started_by),
            actor_email=None,
            action="question_bank.pipeline_generation_complete",
            resource="job_pipeline_instance",
            resource_id=instance.id,
            payload={"succeeded": succeeded, "failed": failed, "total": len(stages)},
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Actor: single question regeneration
# ---------------------------------------------------------------------------

@dramatiq.actor(
    max_retries=2,
    min_backoff=2_000,
    max_backoff=30_000,
    queue_name="question_bank_generation",
)
async def regenerate_question(
    question_id: str,
    tenant_id: str,
    started_by: str,
    replace_signal_values: list[str] | None = None,
) -> None:
    """Regenerate a single question slot, preserving its UUID.

    Uses the regenerate-one prompt which takes other questions in the bank
    as 'do not duplicate' context.
    """
    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        q_result = await db.execute(
            select(StageQuestion).where(StageQuestion.id == UUID(question_id))
        )
        question = q_result.scalar_one_or_none()
        if question is None:
            logger.error("question_bank.question_missing", question_id=question_id)
            return

        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.id == question.bank_id)
        )
        bank = bank_result.scalar_one()
        stage_result = await db.execute(
            select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
        )
        stage = stage_result.scalar_one()
        instance_result = await db.execute(
            select(JobPipelineInstance).where(
                JobPipelineInstance.id == stage.instance_id
            )
        )
        instance = instance_result.scalar_one()
        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == bank.job_posting_id)
        )
        job = job_result.scalar_one()
        snap_result = await db.execute(
            select(JobPostingSignalSnapshot).where(
                JobPostingSignalSnapshot.id == bank.signal_snapshot_id
            )
        )
        snapshot = snap_result.scalar_one()

        # Build prompt: common header + regenerate_one + rich user context
        system_prompt = prompt_loader.load_pair(
            "question_bank_common", "question_bank_regenerate_one"
        )

        other_questions = await get_bank_questions(db, bank.id)
        other_questions = [q for q in other_questions if q.id != question.id]
        target_signals = replace_signal_values or question.signal_values

        user_parts = [
            f"# JOB CONTEXT\n\nJob: {job.title}\nSeniority: {snapshot.seniority_level}\n\n",
            "# SIGNALS (pinned snapshot)\n",
        ]
        for signal in snapshot.signals:
            user_parts.append(
                f"- {signal['value']!r} (type: {signal['type']}, "
                f"weight: {signal['weight']}, knockout: {signal.get('knockout', False)})\n"
            )

        user_parts.append("\n# CURRENT QUESTION BEING REPLACED\n")
        user_parts.append(
            f"Text: {question.text}\n"
            f"Probes: {question.signal_values}\n"
            f"Rubric meets_bar: {question.rubric.get('meets_bar', '')}\n"
            f"Estimated minutes: {question.estimated_minutes}\n"
        )

        user_parts.append("\n# TARGET SIGNALS (probe these)\n")
        for v in target_signals:
            user_parts.append(f"- {v!r}\n")

        user_parts.append("\n# OTHER QUESTIONS IN THIS STAGE'S BANK — DO NOT DUPLICATE\n")
        for q in other_questions:
            user_parts.append(
                f"- Q{q.position}: {q.text} (probes: {q.signal_values})\n"
            )

        user_parts.append(
            f"\n# STAGE METADATA\n"
            f"Type: {stage.stage_type}, Duration: {stage.duration_minutes} min, "
            f"Difficulty: {stage.difficulty}\n"
        )

        user_parts.append(
            "\nNow generate ONE replacement question as a SingleQuestionOutput.\n"
        )

        client = get_openai_client()
        result: SingleQuestionOutput = await client.chat.completions.create(
            model=ai_config.question_bank_model,
            response_model=SingleQuestionOutput,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "".join(user_parts)},
            ],
            max_retries=2,
        )

        # Post-validate the one question against the snapshot
        allowed_types = stage.signal_filter.get("include_types", [])
        await validate_llm_output_against_snapshot(
            db,
            snapshot=snapshot,
            allowed_types=allowed_types,
            questions=[result.question],
        )

        await replace_question_in_place(
            db, question=question, new_data=result.question
        )
        # Auto-revert on edit (confirmed → reviewing if needed)
        from app.modules.question_bank.state_machine import auto_revert_on_edit
        auto_revert_on_edit(bank)
        await db.flush()

        await log_event(
            db,
            tenant_id=UUID(tenant_id),
            actor_id=UUID(started_by),
            actor_email=None,
            action="question_bank.question_regenerated",
            resource="stage_question",
            resource_id=question.id,
            payload={"bank_id": str(bank.id)},
        )
        await db.commit()
```

**Note on `ai_config.question_bank_model`:** you must add `question_bank_model` to `app/ai/config.py`:

- [ ] **Step 3: Add `question_bank_model` to AIConfig**

Open `backend/nexus/app/ai/config.py`. Find the `AIConfig` class and add a new field:

```python
    # Phase 2C.2 — question generation
    question_bank_model: str = "gpt-5"  # tune via env var QUESTION_BANK_MODEL
```

Make sure it's exposed via environment variable following the same pattern as existing fields in the file (check the current conventions — typically using `pydantic-settings`).

- [ ] **Step 4: Register the actor module in the worker**

Open `backend/nexus/app/worker.py`. It imports actor modules so Dramatiq registers them with the broker at startup. Add:

```python
# Phase 2C.2 — question bank generation actors
from app.modules.question_bank import actors as _question_bank_actors  # noqa: F401
```

Place the import next to the existing actor module imports.

- [ ] **Step 5: Smoke-test actor module import**

```bash
docker compose run --rm nexus python -c "
from app.modules.question_bank.actors import (
    generate_question_bank_stage,
    generate_question_bank_pipeline,
    regenerate_question,
)
print('OK')
"
```

Expected: prints `OK`. Any Python error indicates an import loop or missing symbol.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/ai/prompts.py \
        backend/nexus/app/ai/config.py \
        backend/nexus/app/modules/question_bank/actors.py \
        backend/nexus/app/worker.py
git commit -m "feat(question-bank): Dramatiq actors + PromptLoader.load_pair"
```

---

## Task 9: SSE Status Stream

**Files:**
- Create: `backend/nexus/app/modules/question_bank/sse.py`

- [ ] **Step 1: Create `sse.py`**

Mirrors the pattern from `backend/nexus/app/modules/jd/sse.py`. Create:

```python
"""Server-Sent Events stream for question bank generation status.

Polls the DB every 500ms, emits events only when state changes (dedup).
Closes when all banks in the pipeline are terminal OR on 10 minutes of idle.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

import structlog
from sqlalchemy import select

from app.database import AsyncSessionMaker
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    StageQuestion,
    StageQuestionBank,
)

logger = structlog.get_logger()


POLL_INTERVAL_SEC = 0.5
IDLE_TIMEOUT_SEC = 600  # 10 minutes


async def stream_question_bank_status(
    *,
    tenant_id: UUID,
    job_id: UUID,
):
    """Async generator yielding SSE-formatted event strings.

    Format: `event: <name>\\ndata: <json>\\n\\n`
    """
    last_snapshots: dict[UUID, dict] = {}  # bank_id → last emitted state
    idle_since = asyncio.get_event_loop().time()

    while True:
        async with AsyncSessionMaker() as db:
            from sqlalchemy.sql import text
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
            )

            # Load pipeline + stages + banks
            instance_result = await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.job_posting_id == job_id
                )
            )
            instance = instance_result.scalar_one_or_none()
            if instance is None:
                yield _format("error", {"error": "No pipeline for this job"})
                return

            stages_result = await db.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
            stages = list(stages_result.scalars().all())

            any_change = False
            all_terminal = True

            for stage in stages:
                bank_result = await db.execute(
                    select(StageQuestionBank).where(
                        StageQuestionBank.stage_id == stage.id
                    )
                )
                bank = bank_result.scalar_one_or_none()
                if bank is None:
                    all_terminal = False
                    continue

                q_result = await db.execute(
                    select(StageQuestion).where(StageQuestion.bank_id == bank.id)
                )
                questions = list(q_result.scalars().all())
                question_count = len(questions)
                total_minutes = float(sum(q.estimated_minutes for q in questions))

                if bank.status in ("draft", "generating"):
                    all_terminal = False

                current_state = {
                    "status": bank.status,
                    "question_count": question_count,
                    "total_minutes": total_minutes,
                    "error": bank.generation_error,
                }

                if last_snapshots.get(bank.id) != current_state:
                    any_change = True
                    last_snapshots[bank.id] = current_state
                    event_payload = {
                        "stage_id": str(stage.id),
                        "status": bank.status,
                        "question_count": question_count,
                        "total_minutes": total_minutes,
                    }
                    if bank.generation_error:
                        event_payload["error"] = bank.generation_error
                    yield _format("bank.status_changed", event_payload)

        if any_change:
            idle_since = asyncio.get_event_loop().time()
        elif asyncio.get_event_loop().time() - idle_since > IDLE_TIMEOUT_SEC:
            # Close the stream after 10 minutes of no changes
            return

        if all_terminal and len(last_snapshots) == len(stages):
            # All banks reached a terminal state — emit completion and close
            succeeded = sum(
                1 for s in last_snapshots.values() if s["status"] == "confirmed" or s["status"] == "reviewing"
            )
            failed = sum(1 for s in last_snapshots.values() if s["status"] == "failed")
            yield _format(
                "pipeline.generation_complete",
                {"succeeded": succeeded, "failed": failed, "total": len(stages)},
            )
            return

        await asyncio.sleep(POLL_INTERVAL_SEC)


def _format(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/question_bank/sse.py
git commit -m "feat(question-bank): SSE status stream"
```

---

## Task 10: Router + Main Integration

**Files:**
- Rewrite: `backend/nexus/app/modules/question_bank/router.py`
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Rewrite `router.py`**

Overwrite `backend/nexus/app/modules/question_bank/router.py` with 11 endpoints. This file is large — ~450 lines. Structure:

```python
"""Question bank HTTP endpoints.

11 endpoints under /api/jobs/{job_id}/pipeline/... covering CRUD, generation
triggers, bank confirmation, and the SSE status stream.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.question_bank import actors as bank_actors
from app.modules.question_bank.authz import (
    require_bank_access,
    require_bank_access_by_stage,
    require_pipeline_access,
    require_question_access,
)
from app.modules.question_bank.errors import (
    BankAlreadyGeneratingError,
    BankNotInReviewingError,
    DurationBudgetOutOfRangeError,
    KnockoutUnprobedError,
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    BankResponse,
    BankWithQuestionsResponse,
    BanksOverviewResponse,
    CreateQuestionBody,
    GenerateResponse,
    QuestionResponse,
    QuestionRubric,
    RegenerateQuestionBody,
    ReorderBody,
    UpdateQuestionBody,
)
from app.modules.question_bank.service import (
    compute_is_stale,
    confirm_bank,
    create_recruiter_question,
    delete_question,
    ensure_bank_exists,
    get_bank_questions,
    get_banks_for_pipeline,
    get_latest_confirmed_snapshot,
    reorder_questions,
    transition_to_generating,
    update_question,
)
from app.modules.question_bank.sse import stream_question_bank_status

router = APIRouter(prefix="/api", tags=["question_bank"])


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _question_to_response(q: StageQuestion) -> QuestionResponse:
    return QuestionResponse(
        id=q.id,
        bank_id=q.bank_id,
        position=q.position,
        source=q.source,  # type: ignore[arg-type]
        text=q.text,
        signal_values=list(q.signal_values),
        estimated_minutes=float(q.estimated_minutes),
        is_mandatory=q.is_mandatory,
        follow_ups=list(q.follow_ups),
        positive_evidence=list(q.positive_evidence),
        red_flags=list(q.red_flags),
        rubric=QuestionRubric(**q.rubric),
        evaluation_hint=q.evaluation_hint,
        edited_by_recruiter=q.edited_by_recruiter,
        created_at=q.created_at,
        updated_at=q.updated_at,
    )


def _bank_to_response(
    bank: StageQuestionBank,
    *,
    question_count: int,
    total_minutes: float,
    is_stale: bool,
) -> BankResponse:
    return BankResponse(
        id=bank.id,
        stage_id=bank.stage_id,
        job_posting_id=bank.job_posting_id,
        signal_snapshot_id=bank.signal_snapshot_id,
        status=bank.status,  # type: ignore[arg-type]
        prompt_version=bank.prompt_version,
        generation_error=bank.generation_error,
        generated_at=bank.generated_at,
        generated_by=bank.generated_by,
        confirmed_at=bank.confirmed_at,
        confirmed_by=bank.confirmed_by,
        question_count=question_count,
        total_minutes=total_minutes,
        is_stale=is_stale,
        created_at=bank.created_at,
        updated_at=bank.updated_at,
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/jobs/{job_id}/pipeline/questions",
    response_model=BanksOverviewResponse,
)
async def list_banks(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BanksOverviewResponse:
    """Lightweight list of all banks in the pipeline (sidebar data)."""
    instance, _job = await require_pipeline_access(db, job_id, user, "view")

    # Ensure every stage has a bank row (draft if missing)
    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stages_result.scalars().all())
    job_result = await db.execute(
        select(JobPosting).where(JobPosting.id == instance.job_posting_id)
    )
    job = job_result.scalar_one()

    for stage in stages:
        await ensure_bank_exists(db, stage=stage, job=job)
    await db.flush()

    rows = await get_banks_for_pipeline(db, instance)
    banks = [
        _bank_to_response(
            bank,
            question_count=question_count,
            total_minutes=total_minutes,
            is_stale=is_stale,
        )
        for bank, question_count, total_minutes, is_stale in rows
    ]
    return BanksOverviewResponse(banks=banks)


@router.get(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions",
    response_model=BankWithQuestionsResponse,
)
async def get_bank(
    job_id: UUID,
    stage_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankWithQuestionsResponse:
    """Full bank detail including all questions for the main pane."""
    bank, stage, job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "view"
    )
    if bank is None:
        # Create an empty draft bank so the frontend can show "generate" button
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    questions = await get_bank_questions(db, bank.id)
    is_stale = await compute_is_stale(db, bank)
    total_minutes = float(sum(q.estimated_minutes for q in questions))

    return BankWithQuestionsResponse(
        **_bank_to_response(
            bank,
            question_count=len(questions),
            total_minutes=total_minutes,
            is_stale=is_stale,
        ).model_dump(),
        questions=[_question_to_response(q) for q in questions],
    )


# ---------------------------------------------------------------------------
# Generation endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/generate",
    response_model=GenerateResponse,
    status_code=202,
)
async def generate_stage_questions(
    job_id: UUID,
    stage_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Trigger single-stage generation. Returns 202 with bank id."""
    bank, stage, job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    try:
        transition_to_generating(bank)
    except BankAlreadyGeneratingError as exc:
        raise HTTPException(409, detail=str(exc))
    bank.generated_by = user.user.id
    await db.flush()
    await db.commit()

    bank_actors.generate_question_bank_stage.send(
        str(bank.id), str(bank.tenant_id), str(user.user.id)
    )
    return GenerateResponse(bank_id=bank.id, status="generating")


@router.post(
    "/jobs/{job_id}/pipeline/questions/generate-all",
    response_model=GenerateResponse,
    status_code=202,
)
async def generate_all_questions(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Trigger sequential generation for all stages in the pipeline."""
    instance, job = await require_pipeline_access(db, job_id, user, "manage")

    # Check no bank is currently generating
    existing_result = await db.execute(
        select(StageQuestionBank).where(
            StageQuestionBank.job_posting_id == job_id,
            StageQuestionBank.status == "generating",
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            409, detail="Another bank is currently generating in this pipeline"
        )

    await db.commit()
    bank_actors.generate_question_bank_pipeline.send(
        str(instance.id), str(job.tenant_id), str(user.user.id)
    )
    return GenerateResponse(bank_id=None, status="generating")


@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}/regenerate",
    response_model=GenerateResponse,
    status_code=202,
)
async def regenerate_one_question(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    body: RegenerateQuestionBody,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Regenerate one question slot."""
    question, bank, _stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )
    await db.commit()

    bank_actors.regenerate_question.send(
        str(question.id),
        str(bank.tenant_id),
        str(user.user.id),
        body.replace_signal_values,
    )
    return GenerateResponse(bank_id=bank.id, status="generating")


# ---------------------------------------------------------------------------
# Mutation endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions",
    response_model=QuestionResponse,
    status_code=201,
)
async def create_question(
    job_id: UUID,
    stage_id: UUID,
    body: CreateQuestionBody,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> QuestionResponse:
    """Add a hand-written recruiter question to a bank."""
    bank, stage, job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    snap_result = await db.execute(
        select(JobPostingSignalSnapshot).where(
            JobPostingSignalSnapshot.id == bank.signal_snapshot_id
        )
    )
    snapshot = snap_result.scalar_one()

    try:
        question = await create_recruiter_question(
            db,
            bank=bank,
            body=body,
            user_id=user.user.id,
            user_email=user.user.email,
            snapshot=snapshot,
            allowed_types=stage.signal_filter.get("include_types", []),
        )
    except SignalValueNotInSnapshotError as exc:
        raise HTTPException(400, detail=str(exc))
    except SignalTypeNotAllowedError as exc:
        raise HTTPException(400, detail=str(exc))

    await db.commit()
    return _question_to_response(question)


@router.patch(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}",
    response_model=QuestionResponse,
)
async def patch_question(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    body: UpdateQuestionBody,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> QuestionResponse:
    """Edit a question in place. Auto-reverts bank confirmed → reviewing."""
    question, bank, stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )
    snap_result = await db.execute(
        select(JobPostingSignalSnapshot).where(
            JobPostingSignalSnapshot.id == bank.signal_snapshot_id
        )
    )
    snapshot = snap_result.scalar_one()

    try:
        updated = await update_question(
            db,
            question=question,
            bank=bank,
            body=body,
            user_id=user.user.id,
            user_email=user.user.email,
            snapshot=snapshot,
            allowed_types=stage.signal_filter.get("include_types", []),
        )
    except SignalValueNotInSnapshotError as exc:
        raise HTTPException(400, detail=str(exc))
    except SignalTypeNotAllowedError as exc:
        raise HTTPException(400, detail=str(exc))

    await db.commit()
    return _question_to_response(updated)


@router.delete(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}",
    status_code=204,
)
async def delete_question_endpoint(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    """Delete a question and re-pack positions."""
    question, bank, _stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )
    await delete_question(
        db,
        question=question,
        bank=bank,
        user_id=user.user.id,
        user_email=user.user.email,
    )
    await db.commit()


@router.patch(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/reorder",
    response_model=BankWithQuestionsResponse,
)
async def reorder_questions_endpoint(
    job_id: UUID,
    stage_id: UUID,
    body: ReorderBody,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankWithQuestionsResponse:
    """Reorder questions in a bank."""
    bank, stage, _job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        raise HTTPException(404, detail="No bank for this stage")

    try:
        await reorder_questions(
            db,
            bank=bank,
            question_ids=body.question_ids,
            user_id=user.user.id,
            user_email=user.user.email,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))

    await db.commit()

    questions = await get_bank_questions(db, bank.id)
    is_stale = await compute_is_stale(db, bank)
    total_minutes = float(sum(q.estimated_minutes for q in questions))
    return BankWithQuestionsResponse(
        **_bank_to_response(
            bank,
            question_count=len(questions),
            total_minutes=total_minutes,
            is_stale=is_stale,
        ).model_dump(),
        questions=[_question_to_response(q) for q in questions],
    )


# ---------------------------------------------------------------------------
# State transition
# ---------------------------------------------------------------------------

@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/confirm",
    response_model=BankResponse,
)
async def confirm_bank_endpoint(
    job_id: UUID,
    stage_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankResponse:
    """Confirm a bank after running knockout + budget validators."""
    bank, _stage, _job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        raise HTTPException(404, detail="No bank for this stage")

    try:
        await confirm_bank(
            db, bank=bank, user_id=user.user.id, user_email=user.user.email
        )
    except BankNotInReviewingError as exc:
        raise HTTPException(409, detail=str(exc))
    except KnockoutUnprobedError as exc:
        raise HTTPException(
            409,
            detail=(
                f"Cannot confirm: knockout signal '{exc.signal_value}' has "
                f"no mandatory question"
            ),
        )
    except DurationBudgetOutOfRangeError as exc:
        raise HTTPException(409, detail=str(exc))

    await db.commit()

    questions = await get_bank_questions(db, bank.id)
    is_stale = await compute_is_stale(db, bank)
    total_minutes = float(sum(q.estimated_minutes for q in questions))
    return _bank_to_response(
        bank,
        question_count=len(questions),
        total_minutes=total_minutes,
        is_stale=is_stale,
    )


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/pipeline/questions/status-stream")
async def questions_status_stream(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> StreamingResponse:
    """SSE stream of bank status + question update events."""
    _instance, job = await require_pipeline_access(db, job_id, user, "view")
    return StreamingResponse(
        stream_question_bank_status(tenant_id=job.tenant_id, job_id=job_id),
        media_type="text/event-stream",
    )
```

- [ ] **Step 2: Register router + exception handlers in `main.py`**

Open `backend/nexus/app/main.py`. Find the imports and add:

```python
from app.modules.question_bank.router import router as question_bank_router
from app.modules.question_bank.errors import (
    BankAlreadyGeneratingError as QB_BankAlreadyGeneratingError,
    BankNotInReviewingError as QB_BankNotInReviewingError,
    KnockoutUnprobedError as QB_KnockoutUnprobedError,
    DurationBudgetOutOfRangeError as QB_DurationBudgetOutOfRangeError,
)
```

Find where other routers are `include_router`'d and add:

```python
app.include_router(question_bank_router)
```

Find where other exception handlers are registered (Phase 2A/2C.1 patterns) and add:

```python
@app.exception_handler(QB_BankAlreadyGeneratingError)
async def qb_already_generating(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(QB_BankNotInReviewingError)
async def qb_not_reviewing(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(QB_KnockoutUnprobedError)
async def qb_knockout_unprobed(request, exc):
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "signal_value": exc.signal_value},
    )


@app.exception_handler(QB_DurationBudgetOutOfRangeError)
async def qb_duration_out_of_range(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})
```

- [ ] **Step 3: Smoke-test routing**

```bash
docker compose up -d nexus
curl -s http://localhost:8000/openapi.json | \
  python -c "import json, sys; spec = json.load(sys.stdin); \
    paths = [p for p in spec['paths'] if 'questions' in p]; \
    print('\n'.join(sorted(paths)))"
```

Expected: 11 paths listed, all under `/api/jobs/{job_id}/pipeline/...`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/question_bank/router.py \
        backend/nexus/app/main.py
git commit -m "feat(question-bank): HTTP router + SSE stream + main integration"
```

---

## Task 11: Backend Tests — Schemas + Service + Authz

**Files:**
- Create: `backend/nexus/tests/test_question_banks_schemas.py`
- Create: `backend/nexus/tests/test_question_banks_service.py`
- Create: `backend/nexus/tests/test_question_banks_authz.py`

The implementer should mirror the fixture and helper patterns from `tests/test_pipelines_service.py` and `tests/test_pipelines_auto_apply.py` (RLS context via `SET LOCAL app.current_tenant`, tenant/user/unit setup, confirmed-signal-snapshot helpers).

- [ ] **Step 1: Create `test_question_banks_schemas.py` (~8 tests)**

```python
"""Pydantic schema validation tests for question_bank."""

import pytest
from pydantic import ValidationError

from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    GeneratedQuestion,
    QuestionRubric,
    ReorderBody,
    StageQuestionBankOutput,
    UpdateQuestionBody,
)


def _valid_rubric() -> QuestionRubric:
    return QuestionRubric(
        excellent="A strong answer names specific tools and describes hypothesis-verify flow.",
        meets_bar="An acceptable answer mentions at least one tool and shows structure.",
        below_bar="A weak answer is vague with no tools and no structure.",
    )


def _valid_generated_question(**overrides) -> dict:
    base = dict(
        position=0,
        text="Walk me through a production incident you handled.",
        signal_values=["Incident response"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=["What tools did you use?"],
        positive_evidence=[
            "Names specific tools",
            "Describes hypothesis-verify",
            "Mentions post-mortem",
        ],
        red_flags=["No specific tools", "Blames team"],
        rubric=_valid_rubric(),
        evaluation_hint="Strong answer names tools, describes structured approach.",
    )
    base.update(overrides)
    return base


def test_valid_generated_question_parses():
    q = GeneratedQuestion(**_valid_generated_question())
    assert q.position == 0
    assert len(q.positive_evidence) >= 3


def test_generated_question_rejects_too_many_signal_values():
    with pytest.raises(ValidationError):
        GeneratedQuestion(
            **_valid_generated_question(signal_values=["A", "B", "C", "D"]),
        )


def test_generated_question_rejects_too_few_positive_evidence():
    with pytest.raises(ValidationError):
        GeneratedQuestion(
            **_valid_generated_question(positive_evidence=["only one"]),
        )


def test_generated_question_rejects_estimated_minutes_too_large():
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(estimated_minutes=20.0))


def test_stage_question_bank_output_requires_at_least_one_question():
    with pytest.raises(ValidationError):
        StageQuestionBankOutput(
            stage_summary="A" * 25,
            questions=[],
            coverage_notes="B" * 25,
        )


def test_create_question_body_forbids_extra_fields():
    with pytest.raises(ValidationError):
        CreateQuestionBody(
            **_valid_generated_question(),
            unknown_field="oops",
        )


def test_update_question_body_accepts_partial():
    # All fields optional in UpdateQuestionBody
    body = UpdateQuestionBody(text="New question text")
    assert body.text == "New question text"
    assert body.signal_values is None


def test_reorder_body_rejects_empty_list():
    with pytest.raises(ValidationError):
        ReorderBody(question_ids=[])
```

- [ ] **Step 2: Create `test_question_banks_service.py` (~25 tests)**

This file is long (~500 lines). Follow the pattern from `test_pipelines_service.py`. Build helpers at the top of the file for creating a tenant/user/unit + a confirmed signal snapshot + a pipeline instance + a stage. Key tests:

1. `test_ensure_bank_exists_creates_draft_pinned_to_latest_snapshot` — creates a bank, verifies status='draft' and signal_snapshot_id matches the latest confirmed snapshot
2. `test_ensure_bank_exists_returns_existing_when_already_present` — second call returns same bank
3. `test_transition_to_generating_succeeds_from_draft` — state machine happy path
4. `test_transition_to_generating_rejects_if_already_generating` — raises BankAlreadyGeneratingError
5. `test_transition_to_reviewing_after_generation_sets_timestamps` — generated_at + generated_by populated
6. `test_transition_to_failed_records_error_message`
7. `test_transition_to_confirmed_rejects_from_draft` — raises BankNotInReviewingError
8. `test_confirm_bank_rejects_uncovered_knockout` — raises KnockoutUnprobedError
9. `test_confirm_bank_rejects_duration_out_of_range` — raises DurationBudgetOutOfRangeError
10. `test_confirm_bank_success_sets_confirmed_at_and_confirmed_by`
11. `test_auto_revert_on_edit_flips_confirmed_to_reviewing` — clears confirmed_at/by
12. `test_auto_revert_on_edit_flips_draft_to_reviewing`
13. `test_auto_revert_on_edit_leaves_reviewing_unchanged`
14. `test_create_recruiter_question_sets_source_and_position` — source='recruiter', position defaults to end
15. `test_create_recruiter_question_shifts_existing_down_when_position_provided`
16. `test_create_recruiter_question_rejects_invalid_signal_value`
17. `test_create_recruiter_question_rejects_signal_type_outside_include_types`
18. `test_update_question_sets_edited_by_recruiter_flag`
19. `test_update_question_rejects_invalid_signal_value`
20. `test_delete_question_repacks_positions_to_zero_based`
21. `test_reorder_questions_sets_positions_from_list_order`
22. `test_reorder_questions_rejects_mismatched_id_set`
23. `test_write_generated_questions_wipes_ai_sourced_preserves_recruiter`
24. `test_replace_question_in_place_preserves_uuid`
25. `test_compute_is_stale_returns_true_when_snapshot_superseded`

For each test, write the test body using the helpers. Example of the knockout-unprobed test:

```python
@pytest.mark.asyncio
async def test_confirm_bank_rejects_uncovered_knockout(async_session):
    tenant, user, unit = await _setup_tenant_user_unit(async_session)
    await _set_tenant_ctx(async_session, tenant.id)

    # Create job + signal snapshot with a knockout signal
    job, snapshot = await _make_job_with_signals(
        async_session,
        tenant.id,
        unit.id,
        user.id,
        signals=[
            {
                "value": "Apigee",
                "type": "competency",
                "priority": "required",
                "weight": 3,
                "knockout": True,
                "stage": "screen",
                "evaluation_method": "verification",
                "evaluation_hint": None,
                "source": "ai_extracted",
                "inference_basis": None,
            },
        ],
    )
    instance, stage = await _make_pipeline_and_stage(
        async_session, job=job, stage_type="phone_screen",
    )

    bank = await ensure_bank_exists(async_session, stage=stage, job=job)
    bank.status = "reviewing"
    await async_session.flush()

    # No questions yet → knockout is uncovered
    with pytest.raises(KnockoutUnprobedError) as excinfo:
        await confirm_bank(
            async_session, bank=bank, user_id=user.id, user_email=user.email
        )
    assert excinfo.value.signal_value == "Apigee"
```

Write all 25 tests with similar structure. Run:

```bash
docker compose run --rm nexus pytest tests/test_question_banks_service.py -x -q
```

Expected: all 25 pass.

- [ ] **Step 3: Create `test_question_banks_authz.py` (~10 tests)**

```python
"""Authz walkup tests for question_bank helpers."""

import pytest
from fastapi import HTTPException

from app.modules.question_bank.authz import (
    require_bank_access,
    require_bank_access_by_stage,
    require_pipeline_access,
    require_question_access,
)
# ... helpers from other test files ...


@pytest.mark.asyncio
async def test_require_bank_access_returns_bank_when_permitted(async_session):
    """User with jobs.view on the job's org unit can access the bank."""
    ...


@pytest.mark.asyncio
async def test_require_bank_access_raises_404_for_nonexistent_bank(async_session):
    ...


@pytest.mark.asyncio
async def test_require_bank_access_raises_403_when_user_lacks_permission(async_session):
    ...


@pytest.mark.asyncio
async def test_require_bank_access_walks_ancestry(async_session):
    """User with jobs.view on parent org unit can access banks for descendant org units."""
    ...


@pytest.mark.asyncio
async def test_require_bank_access_cross_tenant_returns_404_not_403(async_session):
    """Cross-tenant access is hidden by RLS; looks like a missing bank, not a permission denial."""
    ...


@pytest.mark.asyncio
async def test_require_question_access_walks_up_through_bank(async_session):
    ...


@pytest.mark.asyncio
async def test_require_pipeline_access_returns_instance_when_permitted(async_session):
    ...


@pytest.mark.asyncio
async def test_require_pipeline_access_raises_404_when_no_instance(async_session):
    ...


@pytest.mark.asyncio
async def test_require_bank_access_view_vs_manage(async_session):
    """User with jobs.view but not jobs.manage can view but not manage."""
    ...


@pytest.mark.asyncio
async def test_require_bank_access_by_stage_returns_none_bank_when_not_yet_created(async_session):
    """require_bank_access_by_stage returns (None, stage, job) for a stage with no bank."""
    ...
```

Implement each test by setting up tenants/users/units with different permission combinations. Run:

```bash
docker compose run --rm nexus pytest tests/test_question_banks_authz.py -x -q
```

Expected: 10 pass.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/test_question_banks_schemas.py \
        backend/nexus/tests/test_question_banks_service.py \
        backend/nexus/tests/test_question_banks_authz.py
git commit -m "test(question-bank): schemas + service + authz tests"
```

---

## Task 12: Backend Tests — Actors + Router + Integration

**Files:**
- Create: `backend/nexus/tests/test_question_banks_actors.py`
- Create: `backend/nexus/tests/test_question_banks_router.py`
- Create: `backend/nexus/tests/test_question_banks_integration.py`

- [ ] **Step 1: Create `test_question_banks_actors.py` (~12 tests, mocked LLM)**

```python
"""Dramatiq actor tests with mocked LLM client."""

from unittest.mock import AsyncMock, patch
import pytest

from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    QuestionRubric,
    StageQuestionBankOutput,
    SingleQuestionOutput,
)
# ... helpers ...


def _mock_llm_output(signal_values: list[str]) -> StageQuestionBankOutput:
    """Build a canned LLM response that passes all validations."""
    return StageQuestionBankOutput(
        stage_summary="Stage tests core competencies for the role.",
        questions=[
            GeneratedQuestion(
                position=0,
                text=f"Tell me about your experience with {v}.",
                signal_values=[v],
                estimated_minutes=5.0,
                is_mandatory=True,
                follow_ups=[f"What specifically did you use {v} for?"],
                positive_evidence=[
                    f"Names specific {v} tooling",
                    "Describes production usage",
                    "Mentions metrics or incidents",
                ],
                red_flags=[f"Cannot describe {v} specifics", "Only tutorial experience"],
                rubric=QuestionRubric(
                    excellent=f"Strong {v} experience with production incidents handled.",
                    meets_bar=f"Basic {v} experience with one production deployment.",
                    below_bar=f"Only tutorial or POC {v} exposure.",
                ),
                evaluation_hint=f"Strong = production {v} usage with specific incidents.",
            )
            for v in signal_values
        ],
        coverage_notes="Allocated one question per signal based on weight.",
    )


@pytest.mark.asyncio
async def test_generate_stage_success_writes_questions_and_sets_reviewing(
    async_session, monkeypatch,
):
    """Happy path: mocked LLM returns valid output, bank ends in 'reviewing' with questions."""
    ...  # setup bank in 'generating' state

    mock_llm = AsyncMock(return_value=_mock_llm_output(["Apigee"]))
    # Patch the instructor client:
    with patch("app.modules.question_bank.actors.get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = mock_llm
        from app.modules.question_bank.actors import _generate_one_bank
        await _generate_one_bank(
            async_session,
            bank=bank,
            stage=stage,
            instance=instance,
            job=job,
            snapshot=snapshot,
        )

    assert bank.status == "reviewing"
    questions = await get_bank_questions(async_session, bank.id)
    assert len(questions) == 1
    assert questions[0].signal_values == ["Apigee"]


@pytest.mark.asyncio
async def test_generate_stage_rejects_hallucinated_signal_value(
    async_session, monkeypatch,
):
    """LLM returns a signal_value not in the snapshot → bank goes to 'failed'."""
    ...


@pytest.mark.asyncio
async def test_generate_stage_auto_corrects_knockout_without_mandatory(
    async_session, monkeypatch,
):
    """LLM produces a knockout signal question without is_mandatory → server flips it to True."""
    ...


@pytest.mark.asyncio
async def test_generate_stage_rejects_signal_outside_include_types(
    async_session, monkeypatch,
):
    """LLM probes a signal whose type is not in stage.signal_filter.include_types → failed."""
    ...


@pytest.mark.asyncio
async def test_generate_pipeline_sequentially_sees_prior_stages(
    async_session, monkeypatch,
):
    """Pipeline actor generates stage 1 first, then stage 2 sees stage 1's questions in prompt."""
    ...


@pytest.mark.asyncio
async def test_generate_pipeline_continues_on_stage_failure(
    async_session, monkeypatch,
):
    """If stage 2 fails, stage 3 still generates."""
    ...


@pytest.mark.asyncio
async def test_regenerate_question_preserves_uuid_and_flips_source(
    async_session, monkeypatch,
):
    """regenerate_question actor replaces the question in place, source becomes ai_regenerated."""
    ...


@pytest.mark.asyncio
async def test_regenerate_question_auto_reverts_confirmed_bank(
    async_session, monkeypatch,
):
    """Running regen on a confirmed bank flips status back to reviewing."""
    ...


@pytest.mark.asyncio
async def test_write_generated_questions_preserves_recruiter_questions(
    async_session,
):
    """write_generated_questions deletes ai_generated/ai_regenerated but not recruiter."""
    ...


@pytest.mark.asyncio
async def test_generate_stage_failed_output_retained_on_retry(async_session):
    """A failed bank can be re-generated — error cleared, status flips back to generating."""
    ...


@pytest.mark.asyncio
async def test_pipeline_context_section_contains_prior_questions(
    async_session, monkeypatch,
):
    """Verify the user_message built for stage N includes prior stages' questions."""
    from app.modules.question_bank.actors import _build_user_message
    # ... construct args and assert the output contains the expected text
    ...


@pytest.mark.asyncio
async def test_pipeline_context_section_omits_self_from_prior(
    async_session, monkeypatch,
):
    """The current stage's own questions must not appear in 'prior stages' context."""
    ...
```

- [ ] **Step 2: Create `test_question_banks_router.py` (~18 tests)**

Follow the HTTP test pattern from `tests/test_pipelines_router.py`. Key tests:

1. `GET /api/jobs/{id}/pipeline/questions` → 200 with banks overview
2. `GET /api/jobs/{id}/pipeline/stages/{stage_id}/questions` → 200 with full bank
3. `GET` on nonexistent bank → 404
4. `POST generate` → 202 + bank.status='generating'
5. `POST generate` when already generating → 409
6. `POST generate-all` → 202
7. `POST generate-all` when any bank generating → 409
8. `POST regenerate one` → 202
9. `POST questions` (create recruiter question) → 201 with source='recruiter'
10. `POST questions` with invalid signal_value → 400
11. `POST questions` with signal type outside include_types → 400
12. `PATCH question` → 200, edited_by_recruiter=true
13. `PATCH question` with extra fields → 400 (extra='forbid')
14. `DELETE question` → 204, positions re-packed
15. `PATCH reorder` → 200 with new positions
16. `POST confirm` → 200 on reviewing bank
17. `POST confirm` → 409 "not in reviewing state" on draft bank
18. `POST confirm` → 409 "knockout uncovered" when knockout has no mandatory

Sample:

```python
@pytest.mark.asyncio
async def test_post_confirm_blocks_uncovered_knockout(client, setup_reviewing_bank_with_uncovered_knockout):
    bank, stage, job = setup_reviewing_bank_with_uncovered_knockout
    response = await client.post(
        f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions/confirm"
    )
    assert response.status_code == 409
    assert "knockout" in response.json()["detail"].lower()
```

- [ ] **Step 3: Create `test_question_banks_integration.py` (~3 tests)**

```python
"""End-to-end integration tests — full user journey with mocked LLM."""

@pytest.mark.asyncio
async def test_full_flow_create_confirm_generate_edit_confirm(async_session, monkeypatch):
    """
    1. Create job + pipeline + confirm signals
    2. POST /generate-all → wait for completion
    3. GET /questions → verify all banks in 'reviewing'
    4. PATCH a question → verify edited_by_recruiter=true
    5. POST /confirm on each bank → verify all 'confirmed'
    """
    ...


@pytest.mark.asyncio
async def test_cascade_delete_on_stage_removal(async_session, monkeypatch):
    """Deleting a pipeline stage cascades to bank → questions."""
    ...


@pytest.mark.asyncio
async def test_staleness_detection_after_signal_edit(async_session, monkeypatch):
    """
    1. Generate questions → bank pinned to snapshot v1
    2. Recruiter edits signals → new snapshot v2 confirmed
    3. GET banks → original bank has is_stale=true
    """
    ...
```

- [ ] **Step 4: Run all backend tests**

```bash
docker compose run --rm nexus pytest tests/test_question_banks_*.py tests/test_pipeline_stage_id_stability.py -x -q
```

Expected: all pass.

- [ ] **Step 5: Run full backend test suite**

```bash
docker compose run --rm nexus pytest -x -q
```

Expected: all pass. Count should be ~261 (180 baseline + ~81 new).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/tests/test_question_banks_actors.py \
        backend/nexus/tests/test_question_banks_router.py \
        backend/nexus/tests/test_question_banks_integration.py
git commit -m "test(question-bank): actors + router + integration tests"
```

---

## Task 13: Frontend API Client + TanStack Query Hooks

**Files:**
- Create: `frontend/app/lib/api/question-banks.ts`
- Create: `frontend/app/lib/hooks/use-banks-overview.ts`
- Create: `frontend/app/lib/hooks/use-bank-with-questions.ts`
- Create: `frontend/app/lib/hooks/use-generate-questions.ts`
- Create: `frontend/app/lib/hooks/use-regenerate-question.ts`
- Create: `frontend/app/lib/hooks/use-save-question.ts`
- Create: `frontend/app/lib/hooks/use-confirm-bank.ts`
- Create: `frontend/app/lib/hooks/use-questions-status-stream.ts`

- [ ] **Step 1: Create `question-banks.ts` — typed API client**

```typescript
// frontend/app/lib/api/question-banks.ts

import { apiFetch } from '@/lib/api/client'

// --- Types ---

export type BankStatus = 'draft' | 'generating' | 'reviewing' | 'confirmed' | 'failed'
export type QuestionSource = 'ai_generated' | 'ai_regenerated' | 'recruiter'

export type QuestionRubric = {
  excellent: string
  meets_bar: string
  below_bar: string
}

export type QuestionResponse = {
  id: string
  bank_id: string
  position: number
  source: QuestionSource
  text: string
  signal_values: string[]
  estimated_minutes: number
  is_mandatory: boolean
  follow_ups: string[]
  positive_evidence: string[]
  red_flags: string[]
  rubric: QuestionRubric
  evaluation_hint: string
  edited_by_recruiter: boolean
  created_at: string
  updated_at: string
}

export type BankResponse = {
  id: string
  stage_id: string
  job_posting_id: string
  signal_snapshot_id: string
  status: BankStatus
  prompt_version: string
  generation_error: string | null
  generated_at: string | null
  generated_by: string | null
  confirmed_at: string | null
  confirmed_by: string | null
  question_count: number
  total_minutes: number
  is_stale: boolean
  created_at: string
  updated_at: string
}

export type BankWithQuestionsResponse = BankResponse & {
  questions: QuestionResponse[]
}

export type BanksOverviewResponse = {
  banks: BankResponse[]
}

export type GenerateResponse = {
  bank_id: string | null
  status: BankStatus
}

export type CreateQuestionBody = {
  text: string
  signal_values: string[]
  estimated_minutes: number
  is_mandatory?: boolean
  follow_ups?: string[]
  positive_evidence?: string[]
  red_flags?: string[]
  rubric: QuestionRubric
  evaluation_hint: string
  position?: number
}

export type UpdateQuestionBody = Partial<{
  text: string
  signal_values: string[]
  estimated_minutes: number
  is_mandatory: boolean
  follow_ups: string[]
  positive_evidence: string[]
  red_flags: string[]
  rubric: QuestionRubric
  evaluation_hint: string
  position: number
}>

export type ReorderBody = {
  question_ids: string[]
}

export type RegenerateQuestionBody = {
  replace_signal_values?: string[]
}

// --- API methods ---

export const questionBanksApi = {
  listBanks: (token: string, jobId: string): Promise<BanksOverviewResponse> =>
    apiFetch<BanksOverviewResponse>(`/api/jobs/${jobId}/pipeline/questions`, { token }),

  getBank: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<BankWithQuestionsResponse> =>
    apiFetch<BankWithQuestionsResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions`,
      { token },
    ),

  generateStage: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<GenerateResponse> =>
    apiFetch<GenerateResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/generate`,
      { method: 'POST', token, body: JSON.stringify({}) },
    ),

  generateAll: (token: string, jobId: string): Promise<GenerateResponse> =>
    apiFetch<GenerateResponse>(
      `/api/jobs/${jobId}/pipeline/questions/generate-all`,
      { method: 'POST', token, body: JSON.stringify({}) },
    ),

  regenerateQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    questionId: string,
    body: RegenerateQuestionBody,
  ): Promise<GenerateResponse> =>
    apiFetch<GenerateResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${questionId}/regenerate`,
      { method: 'POST', token, body: JSON.stringify(body) },
    ),

  createQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    body: CreateQuestionBody,
  ): Promise<QuestionResponse> =>
    apiFetch<QuestionResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions`,
      { method: 'POST', token, body: JSON.stringify(body) },
    ),

  updateQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    questionId: string,
    body: UpdateQuestionBody,
  ): Promise<QuestionResponse> =>
    apiFetch<QuestionResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${questionId}`,
      { method: 'PATCH', token, body: JSON.stringify(body) },
    ),

  deleteQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    questionId: string,
  ): Promise<void> =>
    apiFetch<void>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${questionId}`,
      { method: 'DELETE', token },
    ),

  reorderQuestions: (
    token: string,
    jobId: string,
    stageId: string,
    body: ReorderBody,
  ): Promise<BankWithQuestionsResponse> =>
    apiFetch<BankWithQuestionsResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/reorder`,
      { method: 'PATCH', token, body: JSON.stringify(body) },
    ),

  confirmBank: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<BankResponse> =>
    apiFetch<BankResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/confirm`,
      { method: 'POST', token, body: JSON.stringify({}) },
    ),
}
```

- [ ] **Step 2: Create `use-banks-overview.ts`**

```typescript
'use client'

import { useQuery } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { questionBanksApi, type BanksOverviewResponse } from '@/lib/api/question-banks'

export function useBanksOverview(jobId: string) {
  return useQuery<BanksOverviewResponse>({
    queryKey: ['banks', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.listBanks(token, jobId)
    },
    enabled: !!jobId,
    staleTime: 0,
  })
}
```

- [ ] **Step 3: Create `use-bank-with-questions.ts`**

```typescript
'use client'

import { useQuery } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import {
  questionBanksApi,
  type BankWithQuestionsResponse,
} from '@/lib/api/question-banks'

export function useBankWithQuestions(jobId: string, stageId: string | null) {
  return useQuery<BankWithQuestionsResponse>({
    queryKey: ['bank', jobId, stageId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.getBank(token, jobId, stageId!)
    },
    enabled: !!jobId && !!stageId,
    staleTime: 0,
  })
}
```

- [ ] **Step 4: Create `use-generate-questions.ts`**

Exports two hooks: `useGenerateStageQuestions` + `useGenerateAllQuestions`.

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import {
  questionBanksApi,
  type GenerateResponse,
} from '@/lib/api/question-banks'

export function useGenerateStageQuestions(jobId: string, stageId: string) {
  const qc = useQueryClient()
  return useMutation<GenerateResponse, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.generateStage(token, jobId, stageId)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
      qc.invalidateQueries({ queryKey: ['bank', jobId, stageId] })
    },
    onError: (err) => toast.error(`Failed to start generation: ${err.message}`),
  })
}

export function useGenerateAllQuestions(jobId: string) {
  const qc = useQueryClient()
  return useMutation<GenerateResponse, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.generateAll(token, jobId)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
    },
    onError: (err) => toast.error(`Failed to start generation: ${err.message}`),
  })
}
```

- [ ] **Step 5: Create `use-regenerate-question.ts`**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import {
  questionBanksApi,
  type GenerateResponse,
  type RegenerateQuestionBody,
} from '@/lib/api/question-banks'

export function useRegenerateQuestion(
  jobId: string,
  stageId: string,
  questionId: string,
) {
  const qc = useQueryClient()
  return useMutation<GenerateResponse, Error, RegenerateQuestionBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.regenerateQuestion(
        token, jobId, stageId, questionId, body,
      )
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
      qc.invalidateQueries({ queryKey: ['bank', jobId, stageId] })
    },
    onError: (err) => toast.error(`Failed to regenerate: ${err.message}`),
  })
}
```

- [ ] **Step 6: Create `use-save-question.ts`**

Exports four hooks: `useCreateQuestion`, `useUpdateQuestion`, `useDeleteQuestion`, `useReorderQuestions`.

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import {
  questionBanksApi,
  type BankWithQuestionsResponse,
  type CreateQuestionBody,
  type QuestionResponse,
  type ReorderBody,
  type UpdateQuestionBody,
} from '@/lib/api/question-banks'

export function useCreateQuestion(jobId: string, stageId: string) {
  const qc = useQueryClient()
  return useMutation<QuestionResponse, Error, CreateQuestionBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.createQuestion(token, jobId, stageId, body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
      qc.invalidateQueries({ queryKey: ['bank', jobId, stageId] })
    },
    onError: (err) => toast.error(`Failed to create question: ${err.message}`),
  })
}

export function useUpdateQuestion(
  jobId: string,
  stageId: string,
  questionId: string,
) {
  const qc = useQueryClient()
  return useMutation<QuestionResponse, Error, UpdateQuestionBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.updateQuestion(
        token, jobId, stageId, questionId, body,
      )
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
      qc.invalidateQueries({ queryKey: ['bank', jobId, stageId] })
    },
    onError: (err) => toast.error(`Failed to save question: ${err.message}`),
  })
}

export function useDeleteQuestion(jobId: string, stageId: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (questionId) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.deleteQuestion(token, jobId, stageId, questionId)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
      qc.invalidateQueries({ queryKey: ['bank', jobId, stageId] })
    },
    onError: (err) => toast.error(`Failed to delete question: ${err.message}`),
  })
}

export function useReorderQuestions(jobId: string, stageId: string) {
  const qc = useQueryClient()
  return useMutation<BankWithQuestionsResponse, Error, ReorderBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.reorderQuestions(token, jobId, stageId, body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
      qc.invalidateQueries({ queryKey: ['bank', jobId, stageId] })
    },
    onError: (err) => toast.error(`Failed to reorder: ${err.message}`),
  })
}
```

- [ ] **Step 7: Create `use-confirm-bank.ts`**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import {
  questionBanksApi,
  type BankResponse,
} from '@/lib/api/question-banks'

export function useConfirmBank(jobId: string, stageId: string) {
  const qc = useQueryClient()
  return useMutation<BankResponse, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return questionBanksApi.confirmBank(token, jobId, stageId)
    },
    onSuccess: () => {
      toast.success('Bank confirmed. Ready for interview sessions.')
      qc.invalidateQueries({ queryKey: ['banks', jobId] })
      qc.invalidateQueries({ queryKey: ['bank', jobId, stageId] })
    },
    onError: (err) => toast.error(`Failed to confirm: ${err.message}`),
  })
}
```

- [ ] **Step 8: Create `use-questions-status-stream.ts`**

Follows the pattern from `use-job-status-stream.ts` (existing Phase 2A hook). SSE subscription via `@microsoft/fetch-event-source`.

```typescript
'use client'

import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'

export function useQuestionsStatusStream(jobId: string, selectedStageId: string | null) {
  const qc = useQueryClient()

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()

    const run = async () => {
      const token = await getFreshSupabaseToken()
      try {
        await fetchEventSource(
          `${API_URL}/api/jobs/${jobId}/pipeline/questions/status-stream`,
          {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
            onmessage(ev) {
              // All events invalidate the overview
              qc.invalidateQueries({ queryKey: ['banks', jobId] })
              // Bank-level events also invalidate the selected bank detail
              if (
                (ev.event === 'bank.status_changed' ||
                  ev.event === 'bank.question_updated') &&
                selectedStageId
              ) {
                qc.invalidateQueries({ queryKey: ['bank', jobId, selectedStageId] })
              }
            },
            onerror(err) {
              // Let fetchEventSource auto-retry
              console.error('SSE error:', err)
            },
          },
        )
      } catch {
        // Swallow — abort on unmount
      }
    }

    run()
    return () => controller.abort()
  }, [jobId, selectedStageId, qc])
}
```

- [ ] **Step 9: tsc + lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
```

Expected: zero errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/app/lib/api/question-banks.ts \
        frontend/app/lib/hooks/use-banks-overview.ts \
        frontend/app/lib/hooks/use-bank-with-questions.ts \
        frontend/app/lib/hooks/use-generate-questions.ts \
        frontend/app/lib/hooks/use-regenerate-question.ts \
        frontend/app/lib/hooks/use-save-question.ts \
        frontend/app/lib/hooks/use-confirm-bank.ts \
        frontend/app/lib/hooks/use-questions-status-stream.ts
git commit -m "feat(question-bank): frontend API client + 7 TanStack Query hooks"
```

---

## Task 14: Frontend Components — BankStatusBadge, Sidebar, Layout

**Files:**
- Create: `frontend/app/components/dashboard/question-bank/BankStatusBadge.tsx`
- Create: `frontend/app/components/dashboard/question-bank/QuestionsReviewContent.tsx`
- Create: `frontend/app/components/dashboard/question-bank/QuestionSidebar.tsx`
- Create: `frontend/app/components/dashboard/question-bank/QuestionsMainPane.tsx`
- Create: `frontend/app/components/dashboard/question-bank/BankHeader.tsx`

- [ ] **Step 1: Create `BankStatusBadge.tsx`**

```typescript
'use client'

import { AlertCircle, Check, Clock, Lock, Loader2 } from 'lucide-react'
import type { BankStatus } from '@/lib/api/question-banks'

type Props = {
  status: BankStatus
  small?: boolean
}

const STATUS_STYLES: Record<BankStatus, { bg: string; text: string; label: string }> = {
  draft: { bg: 'bg-zinc-100', text: 'text-zinc-600', label: 'DRAFT' },
  generating: { bg: 'bg-blue-50', text: 'text-blue-700', label: 'GENERATING' },
  reviewing: { bg: 'bg-amber-50', text: 'text-amber-700', label: 'REVIEWING' },
  confirmed: { bg: 'bg-emerald-50', text: 'text-emerald-700', label: 'CONFIRMED' },
  failed: { bg: 'bg-red-50', text: 'text-red-700', label: 'FAILED' },
}

export function BankStatusBadge({ status, small }: Props) {
  const style = STATUS_STYLES[status]
  const sizeClass = small ? 'text-[9px] px-1.5 py-0.5' : 'text-[10px] px-2 py-1'

  const Icon =
    status === 'generating' ? Loader2 :
    status === 'confirmed' ? Lock :
    status === 'failed' ? AlertCircle :
    status === 'reviewing' ? Clock :
    Check

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full font-semibold ${sizeClass} ${style.bg} ${style.text}`}
    >
      <Icon
        className={`${small ? 'w-2.5 h-2.5' : 'w-3 h-3'} ${status === 'generating' ? 'animate-spin' : ''}`}
        aria-hidden="true"
      />
      {style.label}
    </span>
  )
}
```

- [ ] **Step 2: Create `QuestionSidebar.tsx`**

```typescript
'use client'

import type { BankResponse } from '@/lib/api/question-banks'
import { BankStatusBadge } from './BankStatusBadge'

type Props = {
  banks: BankResponse[]
  selectedStageId: string | null
  onSelect: (stageId: string) => void
}

export function QuestionSidebar({ banks, selectedStageId, onSelect }: Props) {
  return (
    <aside className="w-70 border-r border-zinc-200 bg-white overflow-y-auto">
      <div className="px-4 py-4">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 mb-3">
          Pipeline stages
        </div>
        <ul className="space-y-2">
          {banks.map((bank, i) => {
            const isSelected = bank.stage_id === selectedStageId
            return (
              <li key={bank.id}>
                <button
                  type="button"
                  onClick={() => onSelect(bank.stage_id)}
                  className={`w-full text-left rounded-lg border px-3 py-2.5 transition ${
                    isSelected
                      ? 'bg-blue-50 border-blue-200'
                      : 'bg-white border-zinc-200 hover:border-zinc-300'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium text-zinc-900">
                      {i + 1} · Stage
                    </div>
                    <BankStatusBadge status={bank.status} small />
                  </div>
                  <div className="text-[11px] text-zinc-500 mt-1">
                    {bank.question_count > 0
                      ? `${bank.question_count} questions · ${bank.total_minutes.toFixed(0)} min`
                      : 'Not generated yet'}
                  </div>
                  {bank.is_stale && (
                    <div className="text-[10px] text-amber-600 mt-1">
                      Signals changed · regenerate
                    </div>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      </div>
    </aside>
  )
}
```

Note: The sidebar shows bank stage position but needs the stage name too. In practice, the `BankResponse` doesn't carry the stage name — the frontend must look it up from the pipeline stages. For simplicity here, fetch the pipeline separately OR extend the backend `BankResponse` to include stage metadata. **Recommendation:** extend the `BankResponse` with `stage_name`, `stage_type`, `stage_duration_minutes`, `stage_difficulty` on the serialization path in `router.py:_bank_to_response`. This is a small change — add the joined stage fields as optional `stage_*` prefixed fields. Update the Pydantic `BankResponse` schema and the TypeScript type accordingly.

Add to backend `BankResponse`:
```python
class BankResponse(BaseModel):
    ...
    stage_name: str
    stage_type: str
    stage_position: int
    stage_duration_minutes: int
    stage_difficulty: str
```

And load the stage fields when building the response in `router.py`. Then frontend consumers show them directly.

- [ ] **Step 3: Create `BankHeader.tsx`**

```typescript
'use client'

import { AlertCircle, Check, Loader2, RefreshCcw } from 'lucide-react'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { Button } from '@/components/ui/button'
import { BankStatusBadge } from './BankStatusBadge'

type Props = {
  bank: BankWithQuestionsResponse
  isSaving: boolean
  saveFailed: boolean
  onGenerate: () => void
  onRegenerate: () => void
  onConfirm: () => void
  onAddCustom: () => void
}

export function BankHeader({
  bank,
  isSaving,
  saveFailed,
  onGenerate,
  onRegenerate,
  onConfirm,
  onAddCustom,
}: Props) {
  const hasQuestions = bank.questions.length > 0
  const canConfirm = bank.status === 'reviewing'

  return (
    <div className="flex items-start justify-between gap-4 pb-4 border-b border-zinc-200">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <h2 className="text-base font-semibold text-zinc-900">
            {bank.questions.length > 0
              ? `${bank.questions.length} questions · ${bank.total_minutes.toFixed(0)} min`
              : 'No questions yet'}
          </h2>
          <BankStatusBadge status={bank.status} />
        </div>
        {bank.generation_error && (
          <div className="text-xs text-red-600 mt-1">{bank.generation_error}</div>
        )}
        {bank.is_stale && (
          <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mt-2">
            Signals have changed since this bank was generated. Click Regenerate to pick up the latest.
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5 text-xs" aria-live="polite">
          {saveFailed ? (
            <>
              <AlertCircle className="w-3.5 h-3.5 text-red-500" aria-hidden="true" />
              <span className="text-red-600">Save failed</span>
            </>
          ) : isSaving ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-400" aria-hidden="true" />
              <span className="text-zinc-500">Saving…</span>
            </>
          ) : hasQuestions ? (
            <>
              <Check className="w-3.5 h-3.5 text-emerald-500" aria-hidden="true" />
              <span className="text-zinc-500">All changes saved</span>
            </>
          ) : null}
        </div>

        {!hasQuestions && bank.status === 'draft' && (
          <Button onClick={onGenerate} size="sm">Generate questions</Button>
        )}
        {hasQuestions && (
          <>
            <Button variant="outline" size="sm" onClick={onRegenerate}>
              <RefreshCcw className="w-3.5 h-3.5 mr-1" />
              Regenerate all
            </Button>
            <Button variant="outline" size="sm" onClick={onAddCustom}>
              + Add custom
            </Button>
            {canConfirm && (
              <Button size="sm" onClick={onConfirm}>
                Confirm bank
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create `QuestionsMainPane.tsx`** and **`QuestionsReviewContent.tsx`** — split view container. These compose the sidebar + main pane and hold the `selectedStageId` state. Implementation pattern mirrors Phase 2C.1's `QuestionsReviewContent`-style split views.

```typescript
// QuestionsReviewContent.tsx
'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'

import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
import { QuestionSidebar } from './QuestionSidebar'
import { QuestionsMainPane } from './QuestionsMainPane'

export function QuestionsReviewContent() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null)

  const { data: overview, isLoading } = useBanksOverview(jobId)
  useQuestionsStatusStream(jobId, selectedStageId)

  // Auto-select first stage on initial load
  useEffect(() => {
    if (!selectedStageId && overview?.banks.length) {
      setSelectedStageId(overview.banks[0].stage_id)
    }
  }, [overview, selectedStageId])

  if (isLoading) {
    return <div className="p-8 text-sm text-zinc-500">Loading banks…</div>
  }

  return (
    <div className="flex h-full min-h-[600px]">
      <QuestionSidebar
        banks={overview?.banks ?? []}
        selectedStageId={selectedStageId}
        onSelect={setSelectedStageId}
      />
      <div className="flex-1 overflow-y-auto">
        {selectedStageId ? (
          <QuestionsMainPane jobId={jobId} stageId={selectedStageId} />
        ) : (
          <div className="p-8 text-sm text-zinc-500">
            Select a stage from the sidebar to view its question bank.
          </div>
        )}
      </div>
    </div>
  )
}
```

```typescript
// QuestionsMainPane.tsx
'use client'

import { useState } from 'react'
import { useBankWithQuestions } from '@/lib/hooks/use-bank-with-questions'
import {
  useGenerateStageQuestions,
} from '@/lib/hooks/use-generate-questions'
import { useConfirmBank } from '@/lib/hooks/use-confirm-bank'
import { BankHeader } from './BankHeader'
import { QuestionList } from './QuestionList'
import { AddCustomQuestionDialog } from './AddCustomQuestionDialog'
import { ConfirmBankDialog } from './ConfirmBankDialog'

type Props = {
  jobId: string
  stageId: string
}

export function QuestionsMainPane({ jobId, stageId }: Props) {
  const { data: bank, isLoading } = useBankWithQuestions(jobId, stageId)
  const generateMutation = useGenerateStageQuestions(jobId, stageId)
  const confirmMutation = useConfirmBank(jobId, stageId)

  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false)

  if (isLoading || !bank) {
    return <div className="p-8 text-sm text-zinc-500">Loading bank…</div>
  }

  return (
    <div className="p-6">
      <BankHeader
        bank={bank}
        isSaving={generateMutation.isPending}
        saveFailed={generateMutation.isError}
        onGenerate={() => generateMutation.mutate()}
        onRegenerate={() => generateMutation.mutate()}
        onConfirm={() => setConfirmDialogOpen(true)}
        onAddCustom={() => setAddDialogOpen(true)}
      />
      <div className="mt-6">
        <QuestionList
          jobId={jobId}
          stageId={stageId}
          bank={bank}
        />
      </div>

      {addDialogOpen && (
        <AddCustomQuestionDialog
          jobId={jobId}
          stageId={stageId}
          bank={bank}
          onClose={() => setAddDialogOpen(false)}
        />
      )}

      {confirmDialogOpen && (
        <ConfirmBankDialog
          bank={bank}
          onConfirm={() => {
            confirmMutation.mutate()
            setConfirmDialogOpen(false)
          }}
          onCancel={() => setConfirmDialogOpen(false)}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 5: tsc + lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
```

Expected: zero errors. (QuestionList, AddCustomQuestionDialog, ConfirmBankDialog are imported but don't exist yet — they land in Task 15. tsc will error on these imports. That's expected until Task 15. Alternatively, stub them as `export function X() { return null }` placeholders to keep the build green, and fill them in Task 15.)

**Decision:** stub the 3 components now as placeholder exports so Task 14 commits cleanly. Task 15 fills them in.

Create placeholder stubs:

```typescript
// QuestionList.tsx
'use client'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
type Props = { jobId: string; stageId: string; bank: BankWithQuestionsResponse }
export function QuestionList(_: Props) { return null }
```

```typescript
// AddCustomQuestionDialog.tsx
'use client'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
type Props = { jobId: string; stageId: string; bank: BankWithQuestionsResponse; onClose: () => void }
export function AddCustomQuestionDialog(_: Props) { return null }
```

```typescript
// ConfirmBankDialog.tsx
'use client'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
type Props = { bank: BankWithQuestionsResponse; onConfirm: () => void; onCancel: () => void }
export function ConfirmBankDialog(_: Props) { return null }
```

Re-run tsc — expected: zero errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/question-bank/
git commit -m "feat(question-bank): status badge + sidebar + main pane + header (layout primitives)"
```

---

## Task 15: Frontend Components — QuestionCard + EditForm + RubricExpanded + Dialogs

**Files:**
- Rewrite: `frontend/app/components/dashboard/question-bank/QuestionList.tsx`
- Create: `frontend/app/components/dashboard/question-bank/QuestionCard.tsx`
- Create: `frontend/app/components/dashboard/question-bank/QuestionEditForm.tsx`
- Create: `frontend/app/components/dashboard/question-bank/QuestionRubricExpanded.tsx`
- Rewrite: `frontend/app/components/dashboard/question-bank/AddCustomQuestionDialog.tsx`
- Rewrite: `frontend/app/components/dashboard/question-bank/ConfirmBankDialog.tsx`

- [ ] **Step 1: Rewrite `QuestionList.tsx`**

```typescript
'use client'

import { useState } from 'react'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { QuestionCard } from './QuestionCard'

type Props = {
  jobId: string
  stageId: string
  bank: BankWithQuestionsResponse
}

export function QuestionList({ jobId, stageId, bank }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  if (bank.questions.length === 0) {
    return (
      <div className="bg-white border border-dashed border-zinc-300 rounded-lg p-12 text-center">
        <div className="text-sm text-zinc-500">
          No questions yet. Click &quot;Generate questions&quot; above to start.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {bank.questions.map((question) => (
        <QuestionCard
          key={question.id}
          jobId={jobId}
          stageId={stageId}
          question={question}
          expanded={expandedId === question.id}
          onToggleExpand={() =>
            setExpandedId(expandedId === question.id ? null : question.id)
          }
        />
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Create `QuestionCard.tsx`**

```typescript
'use client'

import { useRef, useState } from 'react'
import { ChevronDown, MoreVertical, Trash2, RefreshCcw } from 'lucide-react'
import type { QuestionResponse } from '@/lib/api/question-banks'
import { useDeleteQuestion } from '@/lib/hooks/use-save-question'
import { useRegenerateQuestion } from '@/lib/hooks/use-regenerate-question'
import { QuestionEditForm } from './QuestionEditForm'
import { QuestionRubricExpanded } from './QuestionRubricExpanded'

type Props = {
  jobId: string
  stageId: string
  question: QuestionResponse
  expanded: boolean
  onToggleExpand: () => void
}

export function QuestionCard({
  jobId,
  stageId,
  question,
  expanded,
  onToggleExpand,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const deleteMutation = useDeleteQuestion(jobId, stageId)
  const regenMutation = useRegenerateQuestion(jobId, stageId, question.id)

  const sourceBadge = question.source === 'recruiter'
    ? { bg: 'bg-purple-50', text: 'text-purple-700', label: 'CUSTOM' }
    : question.source === 'ai_regenerated'
    ? { bg: 'bg-blue-50', text: 'text-blue-700', label: 'REGENERATED' }
    : null

  return (
    <div className="bg-white border border-zinc-200 rounded-xl shadow-sm overflow-visible">
      <div
        className="p-4 cursor-pointer"
        onClick={onToggleExpand}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="text-xs font-semibold text-zinc-500">
                Q{question.position + 1}
              </span>
              {question.is_mandatory && (
                <span className="bg-red-50 text-red-700 text-[9px] font-bold px-2 py-0.5 rounded">
                  MANDATORY
                </span>
              )}
              {sourceBadge && (
                <span className={`${sourceBadge.bg} ${sourceBadge.text} text-[9px] font-bold px-2 py-0.5 rounded`}>
                  {sourceBadge.label}
                </span>
              )}
              {question.edited_by_recruiter && question.source !== 'recruiter' && (
                <span className="bg-amber-50 text-amber-700 text-[9px] font-bold px-2 py-0.5 rounded">
                  EDITED
                </span>
              )}
              <span className="text-[10px] text-zinc-400">
                probes: {question.signal_values.join(', ')}
              </span>
              <span className="text-[10px] text-zinc-400">
                · {question.estimated_minutes} min
              </span>
            </div>
            <div className="text-sm text-zinc-900">{question.text}</div>
            {!expanded && (
              <div className="text-xs text-zinc-500 mt-2 italic">
                {question.evaluation_hint}
              </div>
            )}
          </div>

          <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            <div ref={menuRef} className="relative">
              <button
                type="button"
                aria-label="Question actions"
                onClick={(e) => {
                  e.stopPropagation()
                  setMenuOpen((v) => !v)
                }}
                className="p-1.5 rounded-md hover:bg-zinc-100 text-zinc-500"
              >
                <MoreVertical className="w-4 h-4" />
              </button>
              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-full mt-1 w-48 bg-white border border-zinc-200 rounded-lg shadow-lg py-1 z-20"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false)
                      if (confirm('Replace this question with a new AI-generated one? Your edits will be lost.')) {
                        regenMutation.mutate({})
                      }
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-700 hover:bg-zinc-50 text-left"
                  >
                    <RefreshCcw className="w-4 h-4" />
                    Regenerate
                  </button>
                  <div className="my-1 border-t border-zinc-100" />
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false)
                      if (confirm('Delete this question? Cannot be undone.')) {
                        deleteMutation.mutate(question.id)
                      }
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 text-left"
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete
                  </button>
                </div>
              )}
            </div>
            <button
              type="button"
              aria-label={expanded ? 'Collapse' : 'Expand'}
              className="p-1.5 rounded-md hover:bg-zinc-100 text-zinc-500"
            >
              <ChevronDown
                className={`w-4 h-4 transition-transform ${expanded ? 'rotate-180' : ''}`}
              />
            </button>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-zinc-100 p-4 bg-zinc-50 space-y-4">
          <QuestionEditForm
            jobId={jobId}
            stageId={stageId}
            question={question}
          />
          <QuestionRubricExpanded question={question} />
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Create `QuestionEditForm.tsx`**

Inline editable text + evaluation_hint, auto-save with 800ms debounce — same pattern as the pipeline editor auto-save.

```typescript
'use client'

import { useEffect, useRef, useState } from 'react'
import type { QuestionResponse } from '@/lib/api/question-banks'
import { useUpdateQuestion } from '@/lib/hooks/use-save-question'

type Props = {
  jobId: string
  stageId: string
  question: QuestionResponse
}

const DEBOUNCE_MS = 800

export function QuestionEditForm({ jobId, stageId, question }: Props) {
  const updateMutation = useUpdateQuestion(jobId, stageId, question.id)
  const [text, setText] = useState(question.text)
  const [hint, setHint] = useState(question.evaluation_hint)
  const saveTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
      }
    }
  }, [])

  function schedule(body: { text?: string; evaluation_hint?: string }) {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(() => {
      updateMutation.mutate(body)
      saveTimerRef.current = null
    }, DEBOUNCE_MS)
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-[10px] font-semibold uppercase text-zinc-500 mb-1">
          Question text
        </label>
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            schedule({ text: e.target.value })
          }}
          className="w-full text-sm border border-zinc-200 rounded px-3 py-2 resize-none"
          rows={3}
        />
      </div>
      <div>
        <label className="block text-[10px] font-semibold uppercase text-zinc-500 mb-1">
          Evaluation hint
        </label>
        <textarea
          value={hint}
          onChange={(e) => {
            setHint(e.target.value)
            schedule({ evaluation_hint: e.target.value })
          }}
          className="w-full text-xs border border-zinc-200 rounded px-3 py-2 resize-none"
          rows={2}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create `QuestionRubricExpanded.tsx`**

```typescript
'use client'

import type { QuestionResponse } from '@/lib/api/question-banks'

type Props = { question: QuestionResponse }

export function QuestionRubricExpanded({ question }: Props) {
  return (
    <div className="space-y-4 text-xs">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="font-semibold text-emerald-700 mb-1">✓ Listen for:</div>
          <ul className="list-disc pl-4 space-y-0.5 text-zinc-600">
            {question.positive_evidence.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="font-semibold text-red-700 mb-1">⚠ Red flags:</div>
          <ul className="list-disc pl-4 space-y-0.5 text-zinc-600">
            {question.red_flags.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      </div>

      {question.follow_ups.length > 0 && (
        <div>
          <div className="font-semibold text-zinc-900 mb-1">Follow-up probes:</div>
          <ul className="space-y-0.5 text-zinc-600">
            {question.follow_ups.map((item, i) => (
              <li key={i}>→ {item}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="font-semibold text-zinc-900 mb-1">Rubric:</div>
        <div className="space-y-1.5">
          <div className="bg-emerald-50 border border-emerald-200 rounded px-2 py-1">
            <span className="font-semibold text-emerald-700">Excellent:</span>{' '}
            <span className="text-zinc-700">{question.rubric.excellent}</span>
          </div>
          <div className="bg-amber-50 border border-amber-200 rounded px-2 py-1">
            <span className="font-semibold text-amber-700">Meets bar:</span>{' '}
            <span className="text-zinc-700">{question.rubric.meets_bar}</span>
          </div>
          <div className="bg-red-50 border border-red-200 rounded px-2 py-1">
            <span className="font-semibold text-red-700">Below bar:</span>{' '}
            <span className="text-zinc-700">{question.rubric.below_bar}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Rewrite `AddCustomQuestionDialog.tsx`**

Modal with full question form. Uses React Hook Form + Zod (same pattern as Phase 2A job form).

```typescript
'use client'

import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { Button } from '@/components/ui/button'
import { useCreateQuestion } from '@/lib/hooks/use-save-question'

const schema = z.object({
  text: z.string().min(10).max(500),
  signal_values: z.array(z.string()).min(1).max(3),
  estimated_minutes: z.number().gt(0).le(15),
  is_mandatory: z.boolean(),
  follow_ups: z.array(z.string()).max(3),
  positive_evidence: z.array(z.string()).max(5),
  red_flags: z.array(z.string()).max(3),
  rubric: z.object({
    excellent: z.string().min(20).max(300),
    meets_bar: z.string().min(20).max(300),
    below_bar: z.string().min(20).max(300),
  }),
  evaluation_hint: z.string().min(10).max(200),
})

type Form = z.infer<typeof schema>

type Props = {
  jobId: string
  stageId: string
  bank: BankWithQuestionsResponse
  onClose: () => void
}

export function AddCustomQuestionDialog({ jobId, stageId, bank, onClose }: Props) {
  const createMutation = useCreateQuestion(jobId, stageId)
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: {
      text: '',
      signal_values: [],
      estimated_minutes: 5,
      is_mandatory: false,
      follow_ups: [],
      positive_evidence: ['', '', ''],
      red_flags: ['', ''],
      rubric: { excellent: '', meets_bar: '', below_bar: '' },
      evaluation_hint: '',
    },
  })

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  function onSubmit(data: Form) {
    createMutation.mutate(data, {
      onSuccess: () => onClose(),
    })
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-question-heading"
        className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h3 id="add-question-heading" className="text-base font-semibold">Add custom question</h3>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-900 text-xl leading-none"
          >×</button>
        </div>

        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="p-5 space-y-4 overflow-y-auto"
        >
          {/* Basic fields */}
          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">Question text</label>
            <textarea
              {...form.register('text')}
              rows={3}
              className="w-full text-sm border border-zinc-200 rounded px-3 py-2"
            />
            {form.formState.errors.text && (
              <p className="text-xs text-red-600 mt-1">{form.formState.errors.text.message}</p>
            )}
          </div>

          {/* Signal picker — multi-select */}
          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">
              Signal values (1–3)
            </label>
            <div className="text-[11px] text-zinc-500 mb-2">
              Select up to 3 signals from the job's pinned snapshot. These must be signals
              whose type matches this stage's signal filter.
            </div>
            {/* NOTE: the signal picker needs the snapshot's signals list.
                Option A (pragmatic): fetch the job's signals via useJob() and render as
                checkboxes filtered by stage.signal_filter.include_types
                Option B (deferred): defer full signal picker to a follow-up commit,
                allow free-text entry here as MVP. We recommend Option A.
                See task notes for implementation guidance. */}
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">Estimated minutes</label>
            <input
              type="number"
              min="1"
              max="15"
              step="0.5"
              {...form.register('estimated_minutes', { valueAsNumber: true })}
              className="w-full text-sm border border-zinc-200 rounded px-3 py-2"
            />
          </div>

          <div>
            <label className="inline-flex items-center gap-2 text-xs">
              <input type="checkbox" {...form.register('is_mandatory')} />
              Mandatory (must be asked during the interview)
            </label>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">Evaluation hint</label>
            <textarea
              {...form.register('evaluation_hint')}
              rows={2}
              className="w-full text-xs border border-zinc-200 rounded px-3 py-2"
              placeholder="1–2 sentences summarizing what a strong answer contains"
            />
          </div>

          {/* Rubric */}
          <div className="space-y-2">
            <div className="text-xs font-medium text-zinc-700">Rubric anchors</div>
            {(['excellent', 'meets_bar', 'below_bar'] as const).map((level) => (
              <div key={level}>
                <label className="block text-[10px] uppercase text-zinc-500 mb-1">{level}</label>
                <textarea
                  {...form.register(`rubric.${level}` as const)}
                  rows={2}
                  className="w-full text-xs border border-zinc-200 rounded px-3 py-2"
                />
              </div>
            ))}
          </div>

          {/* Positive evidence, red flags, follow ups — shown as simple
              CSV inputs for MVP (recruiter can enter comma-separated items).
              Full array-of-textareas UI is a nice-to-have for Phase 2D. */}

          <div className="flex justify-end gap-2 pt-3 border-t border-zinc-100">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating…' : 'Create question'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Rewrite `ConfirmBankDialog.tsx`**

```typescript
'use client'

import { useEffect } from 'react'
import { Check, AlertCircle } from 'lucide-react'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { Button } from '@/components/ui/button'

type Props = {
  bank: BankWithQuestionsResponse
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmBankDialog({ bank, onConfirm, onCancel }: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onCancel])

  const mandatory_count = bank.questions.filter(q => q.is_mandatory).length
  const total_minutes = bank.total_minutes
  // Coverage warnings are computed server-side at confirm time; here we just
  // show a summary. The POST will return 409 if coverage is missing, and the
  // caller handles that via the mutation's onError toast.

  return (
    <div
      className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-bank-heading"
        className="bg-white rounded-xl shadow-2xl w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h3 id="confirm-bank-heading" className="text-base font-semibold">Confirm bank</h3>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onCancel}
            className="text-zinc-400 hover:text-zinc-900 text-xl leading-none"
          >×</button>
        </div>

        <div className="p-5 space-y-3 text-sm">
          <p className="text-zinc-700">
            Confirming this bank locks it for interview sessions. After confirmation,
            editing any question will revert the bank to reviewing and require re-confirmation.
          </p>

          <div className="bg-zinc-50 border border-zinc-200 rounded-lg p-3 space-y-1.5 text-xs text-zinc-700">
            <div className="flex items-center gap-2">
              <Check className="w-3.5 h-3.5 text-emerald-500" />
              <span>{bank.questions.length} questions · {total_minutes.toFixed(0)} min total</span>
            </div>
            <div className="flex items-center gap-2">
              <Check className="w-3.5 h-3.5 text-emerald-500" />
              <span>{mandatory_count} mandatory questions</span>
            </div>
          </div>

          <p className="text-xs text-zinc-500">
            The server will validate knockout coverage and duration budget before confirming.
            If anything is missing, you'll see an error and can fix it before re-confirming.
          </p>
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-zinc-100">
          <Button variant="outline" onClick={onCancel}>Cancel</Button>
          <Button onClick={onConfirm}>Confirm bank</Button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 7: tsc + lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
```

Expected: zero errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/app/components/dashboard/question-bank/
git commit -m "feat(question-bank): card + edit form + rubric expanded + dialogs"
```

---

## Task 16: Questions Page Route + Pipeline Page Link

**Files:**
- Create: `frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`

- [ ] **Step 1: Create the questions page route**

```typescript
// frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx
'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft } from 'lucide-react'
import { QuestionsReviewContent } from '@/components/dashboard/question-bank/QuestionsReviewContent'

export default function QuestionsReviewPage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  return (
    <div>
      <div className="mb-4">
        <Link
          href={`/jobs/${jobId}/pipeline`}
          className="text-sm text-zinc-500 hover:text-zinc-900 inline-flex items-center gap-1"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to pipeline
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900 mt-2">Interview Questions</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Review, edit, and confirm the question bank for each pipeline stage. Confirmed banks are ready for Phase 3 interview sessions.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-zinc-200 overflow-hidden">
        <QuestionsReviewContent />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add "Review questions →" link to the pipeline page header**

Open `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`. Find the `JobPipelineEditor` component's header block (where "Back to job", title, and action buttons live). Add a new button that links to `/jobs/{jobId}/questions` with a chip showing confirmation progress:

```typescript
// At the top of JobPipelineEditor, fetch banks overview for the chip:
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'

// Inside JobPipelineEditor:
const { data: overview } = useBanksOverview(jobId)
const confirmed_count = overview?.banks.filter(b => b.status === 'confirmed').length ?? 0
const total_banks = overview?.banks.length ?? 0
```

In the JSX, next to the existing Save status / Swap template / Reset to source row, add:

```tsx
<Link href={`/jobs/${jobId}/questions`}>
  <Button variant="outline" size="sm">
    Review questions →
    {total_banks > 0 && (
      <span
        className={`ml-2 text-[10px] font-bold px-1.5 py-0.5 rounded ${
          confirmed_count === total_banks && total_banks > 0
            ? 'bg-emerald-100 text-emerald-700'
            : 'bg-zinc-100 text-zinc-600'
        }`}
      >
        {confirmed_count} of {total_banks} confirmed
      </span>
    )}
  </Button>
</Link>
```

Place this element next to the Swap template / Reset to source buttons so all actions are in one row.

- [ ] **Step 3: tsc + lint + build**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
npm run build
```

Expected: zero errors, build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/jobs/\[jobId\]/questions/page.tsx \
        frontend/app/app/\(dashboard\)/jobs/\[jobId\]/pipeline/page.tsx
git commit -m "feat(question-bank): questions page route + pipeline page link"
```

---

## Task 17: Frontend Tests + Final Verification

**Files:**
- Create: `frontend/app/tests/components/QuestionCard.test.tsx`
- Create: `frontend/app/tests/components/BankStatusBadge.test.tsx`

- [ ] **Step 1: Create `BankStatusBadge.test.tsx` (~5 tests)**

```typescript
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BankStatusBadge } from '@/components/dashboard/question-bank/BankStatusBadge'
import type { BankStatus } from '@/lib/api/question-banks'

describe('BankStatusBadge', () => {
  const statuses: BankStatus[] = ['draft', 'generating', 'reviewing', 'confirmed', 'failed']

  statuses.forEach((status) => {
    it(`renders ${status} with correct label`, () => {
      const { getByText } = render(<BankStatusBadge status={status} />)
      expect(getByText(status.toUpperCase())).toBeInTheDocument()
    })
  })

  it('renders generating with an animated spinner', () => {
    const { container } = render(<BankStatusBadge status="generating" />)
    expect(container.querySelector('.animate-spin')).not.toBeNull()
  })

  it('renders small variant with smaller text class', () => {
    const { container } = render(<BankStatusBadge status="confirmed" small />)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toContain('text-[9px]')
  })
})
```

- [ ] **Step 2: Create `QuestionCard.test.tsx` (~4 tests)**

```typescript
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { QuestionCard } from '@/components/dashboard/question-bank/QuestionCard'
import type { QuestionResponse } from '@/lib/api/question-banks'

function makeQuestion(overrides: Partial<QuestionResponse> = {}): QuestionResponse {
  return {
    id: 'q1',
    bank_id: 'b1',
    position: 0,
    source: 'ai_generated',
    text: 'Walk me through a production incident.',
    signal_values: ['Incident response'],
    estimated_minutes: 5,
    is_mandatory: false,
    follow_ups: ['What tools did you use?'],
    positive_evidence: ['Names specific tools', 'Describes hypothesis-verify', 'Mentions post-mortem'],
    red_flags: ['No specific tools', 'Blames team'],
    rubric: {
      excellent: 'Strong answer with specific tooling and hypothesis-verify approach.',
      meets_bar: 'Acceptable answer with some structure and tools mentioned.',
      below_bar: 'Vague answer with no specific tools or structure.',
    },
    evaluation_hint: 'Strong = names observability tools + structured debugging.',
    edited_by_recruiter: false,
    created_at: '2026-04-12T00:00:00Z',
    updated_at: '2026-04-12T00:00:00Z',
    ...overrides,
  }
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('QuestionCard', () => {
  it('renders the question text and signals', () => {
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion()}
        expanded={false}
        onToggleExpand={() => {}}
      />,
    )
    expect(screen.getByText('Walk me through a production incident.')).toBeInTheDocument()
    expect(screen.getByText(/Incident response/)).toBeInTheDocument()
  })

  it('shows MANDATORY badge for mandatory questions', () => {
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion({ is_mandatory: true })}
        expanded={false}
        onToggleExpand={() => {}}
      />,
    )
    expect(screen.getByText('MANDATORY')).toBeInTheDocument()
  })

  it('shows CUSTOM badge for recruiter-sourced questions', () => {
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion({ source: 'recruiter' })}
        expanded={false}
        onToggleExpand={() => {}}
      />,
    )
    expect(screen.getByText('CUSTOM')).toBeInTheDocument()
  })

  it('calls onToggleExpand when the card is clicked', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    renderWithClient(
      <QuestionCard
        jobId="j1"
        stageId="s1"
        question={makeQuestion()}
        expanded={false}
        onToggleExpand={onToggle}
      />,
    )
    await user.click(screen.getByText('Walk me through a production incident.'))
    expect(onToggle).toHaveBeenCalled()
  })
})
```

- [ ] **Step 3: Run frontend tests**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run test -- --run
```

Expected: all tests pass. Count should be baseline 13 + 9 new = 22.

- [ ] **Step 4: Full verification sweep**

```bash
# Backend
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest -x -q

# Frontend
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
npm run test -- --run
npm run build
```

Expected: all green.
- Backend pytest: ~261 passed
- Frontend tsc: zero errors
- Frontend lint: zero errors (3 pre-existing warnings OK)
- Frontend tests: 22 passed
- Frontend build: all routes compile

- [ ] **Step 5: Manual smoke test (definition of done — from spec Section "Smoke test checklist")**

Run through this manually:

1. Create new job → paste real JD → wait for extraction
2. Confirm signals on the new job
3. Auto-apply fires → pipeline applied from org unit default template
4. Navigate to `/jobs/{id}/pipeline` → see "Review questions →" button with "0 of N banks confirmed"
5. Click → `/jobs/{id}/questions` loads
6. Sidebar shows all stages in "DRAFT"
7. Click "Generate all" → SSE shows transitions through generating → reviewing
8. After 60–150s, all stages show REVIEWING with question counts
9. Click each stage → question cards render with full rubric on expand
10. Edit a question text → auto-save fires → status stays at reviewing
11. ⋯ menu → "Regenerate this question" → spinner → new question arrives
12. Add a custom question via the dialog → CUSTOM badge displayed
13. Click "Confirm bank" on stage 1 → coverage summary dialog → confirm → green CONFIRMED badge
14. Navigate back to pipeline page → header chip now shows "1 of N confirmed"
15. Delete the mandatory question covering a knockout in stage 2 → try to confirm stage 2 → 409 error with specific message
16. Edit a stage's duration in the pipeline editor → navigate back to questions → bank still exists (tests the 2C.1 fix)
17. Swap the pipeline template → old banks cascade-deleted → new empty banks appear

Any failure in 1–17 blocks the phase.

- [ ] **Step 6: Commit tests + final merge commit**

```bash
git add frontend/app/tests/components/QuestionCard.test.tsx \
        frontend/app/tests/components/BankStatusBadge.test.tsx
git commit -m "test(question-bank): frontend component tests"
```

---

## End of Plan

Total: 17 tasks, ~90 new tests, 2 new tables, 11 endpoints, 11 frontend components, 7 hooks, 7 prompt files, 3 Dramatiq actors, 1 new route.

Total file change estimate:
- Backend: ~15 new files (migration, 2 prompt stubs that already exist, service/router/actors/authz/errors/state_machine/sse, plus 7 new test files)
- Frontend: ~19 new files (1 API client, 7 hooks, 11 components, 1 page, 2 tests) + 3 modified files

After all 17 tasks, the baseline grows from 180 backend + 13 frontend tests to ~261 + 22 tests. The backend `question_bank` module becomes one of the largest modules in the codebase, on par with `jd` and `pipelines` from earlier phases.
