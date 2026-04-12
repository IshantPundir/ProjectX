"""Regression test for Phase 2C.1 / 2C.2 — stage IDs must survive edits
so question banks FK'd to stage_id don't get cascade-deleted on every save."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import JobPipelineStage
from app.modules.pipelines.schemas import (
    PassCriteriaKnockout,
    PipelineStageInput,
    PipelineStageUpdateInput,
    SignalFilter,
)
from app.modules.pipelines.service import (
    create_job_pipeline_from_scratch,
    update_job_pipeline_stages,
)

# Reuse helpers from the existing pipelines test file
from tests.test_pipelines_service import (
    _make_confirmed_job,
    _set_tenant_ctx,
    _setup_tenant_user_unit,
)


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
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior=stage.advance_behavior,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_update_preserves_ids_when_all_stages_pass_their_id(
    db,
):
    """Editing existing stages with their IDs preserved leaves row UUIDs intact."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    await _set_tenant_ctx(db, tenant.id)

    job = await _make_confirmed_job(db, tenant.id, unit.id, user.id)

    instance = await create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[
            _make_stage_input(0, "Screen"),
            _make_stage_input(1, "Interview"),
            _make_stage_input(2, "Panel"),
        ],
    )
    await db.flush()

    result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    original_stages = list(result.scalars().all())
    original_ids = [s.id for s in original_stages]

    await update_job_pipeline_stages(
        db,
        instance=instance,
        stages=[_to_update_input(s) for s in original_stages],
    )
    await db.flush()

    result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    new_stages = list(result.scalars().all())
    new_ids = [s.id for s in new_stages]

    assert new_ids == original_ids, "Stage UUIDs must be preserved across update"


@pytest.mark.asyncio
async def test_update_inserts_new_stage_without_touching_existing(
    db,
):
    """Adding a new stage (no id) inserts one row; existing rows unchanged."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    await _set_tenant_ctx(db, tenant.id)

    job = await _make_confirmed_job(db, tenant.id, unit.id, user.id)

    instance = await create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[_make_stage_input(0, "Screen"), _make_stage_input(1, "Interview")],
    )
    await db.flush()

    existing = list(
        (
            await db.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    original_ids = [s.id for s in existing]

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

    await update_job_pipeline_stages(db, instance=instance, stages=updates)
    await db.flush()

    final = list(
        (
            await db.execute(
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
async def test_update_removes_stage_when_id_omitted(db):
    """Deleting a stage from the incoming list drops that row and preserves others."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    await _set_tenant_ctx(db, tenant.id)

    job = await _make_confirmed_job(db, tenant.id, unit.id, user.id)

    instance = await create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[
            _make_stage_input(0, "Screen"),
            _make_stage_input(1, "Interview"),
            _make_stage_input(2, "Panel"),
        ],
    )
    await db.flush()

    existing = list(
        (
            await db.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    screen_id, interview_id, _panel_id = [s.id for s in existing]

    await update_job_pipeline_stages(
        db,
        instance=instance,
        stages=[_to_update_input(existing[0]), _to_update_input(existing[1])],
    )
    await db.flush()

    final = list(
        (
            await db.execute(
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
async def test_update_combines_add_and_remove_in_one_call(db):
    """Diff-and-sync: add one new + remove one existing + update one in place."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    await _set_tenant_ctx(db, tenant.id)

    job = await _make_confirmed_job(db, tenant.id, unit.id, user.id)

    instance = await create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[_make_stage_input(0, "Screen"), _make_stage_input(1, "OldPanel")],
    )
    await db.flush()

    existing = list(
        (
            await db.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        )
        .scalars()
        .all()
    )
    screen_id = existing[0].id

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
        db,
        instance=instance,
        stages=[renamed_screen, new_interview, new_panel],
    )
    await db.flush()

    final = list(
        (
            await db.execute(
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
