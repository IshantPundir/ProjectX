"""Tests for server-side intake/debrief bookend seeding on pipeline creation.

Covers:
  - from_template with NO bookends in template → bookends added
  - from_starter with NO bookends in starter → bookends added
  - from_scratch with NO bookends in stages → bookends added
  - from_template with bookends ALREADY in template → no doubling
  - from_scratch with bookends ALREADY in stages → no doubling
  - reset_to_source with NO bookends in template → bookends added
  - Positions are contiguous 0..N-1 after seeding
"""

import pytest
import sqlalchemy
from sqlalchemy import select

from app.models import (
    JobPipelineStage,
    JobPosting,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.pipelines import service as pipelines_service
from app.modules.pipelines.schemas import (
    PassCriteriaKnockout,
    PipelineStageInput,
    SignalFilter,
)
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stage(
    position: int = 0,
    name: str = "AI Screen",
    stage_type: str = "ai_screening",
) -> PipelineStageInput:
    return PipelineStageInput(
        position=position,
        name=name,
        stage_type=stage_type,  # type: ignore[arg-type]
        duration_minutes=30,
        difficulty="medium",
        signal_filter=SignalFilter(include_types=["competency", "experience"]),
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior="auto_advance",
    )


async def _setup(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant.id}'")
    )
    return tenant, user, company


async def _make_confirmed_job(
    db, tenant_id, org_unit_id, user_id, *, title: str = "Test Job"
) -> JobPosting:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title=title,
        description_raw="A" * 200,
        description_enriched="Enriched test description.",
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()
    return job


def _assert_bookends_and_contiguous(stages: list[JobPipelineStage]) -> None:
    """Common assertions: bookends present, positions 0..N-1."""
    assert stages[0].stage_type == "intake", (
        f"Expected intake at position 0, got {stages[0].stage_type}"
    )
    assert stages[-1].stage_type == "debrief", (
        f"Expected debrief at last position, got {stages[-1].stage_type}"
    )
    for i, s in enumerate(stages):
        assert s.position == i, (
            f"Stage at index {i} has position {s.position} — expected contiguous"
        )


# ---------------------------------------------------------------------------
# from_template: no bookends in template → added server-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_template_without_bookends_seeds_intake_and_debrief(db):
    """A template with only ai_screening yields intake + ai_screening + debrief."""
    tenant, user, company = await _setup(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="No Bookends Template",
        description=None,
        is_default=False,
        stages=[_make_stage(0, "AI Screen", "ai_screening")],
    )

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    await pipelines_service.create_job_pipeline_from_template(
        db, job=job, template_id=template.id,
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    assert len(stages) == 3
    _assert_bookends_and_contiguous(stages)
    assert stages[1].stage_type == "ai_screening"
    assert stages[1].name == "AI Screen"


@pytest.mark.asyncio
async def test_from_template_positions_are_contiguous(db):
    """Multi-stage template without bookends gets re-indexed correctly."""
    tenant, user, company = await _setup(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Three Stage Template",
        description=None,
        is_default=False,
        stages=[
            _make_stage(0, "Phone Screen", "phone_screen"),
            _make_stage(1, "AI Interview", "ai_screening"),
            _make_stage(2, "Panel", "human_interview"),
        ],
    )

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    await pipelines_service.create_job_pipeline_from_template(
        db, job=job, template_id=template.id,
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    # 3 template stages + intake + debrief = 5
    assert len(stages) == 5
    _assert_bookends_and_contiguous(stages)
    assert stages[1].stage_type == "phone_screen"
    assert stages[2].stage_type == "ai_screening"
    assert stages[3].stage_type == "human_interview"


# ---------------------------------------------------------------------------
# from_template: template already has intake/debrief → no doubling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_template_with_bookends_does_not_double_seed(db):
    """A template that already has intake at 0 and debrief at end must NOT get
    duplicate bookends."""
    tenant, user, company = await _setup(db)

    # Build a template that already has bookends — insert directly so Pydantic
    # field-rules don't strip forbidden fields.
    tpl = PipelineTemplate(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        name="Pre-bookended Template",
        is_default=False,
        created_by=user.id,
    )
    db.add(tpl)
    await db.flush()

    for pos, (name, stype) in enumerate([
        ("Intake", "intake"),
        ("AI Screen", "ai_screening"),
        ("Debrief", "debrief"),
    ]):
        row = PipelineTemplateStage(
            tenant_id=tenant.id,
            template_id=tpl.id,
            position=pos,
            name=name,
            stage_type=stype,
            duration_minutes=None if stype in ("intake", "debrief") else 30,
            difficulty=None if stype in ("intake", "debrief") else "medium",
            signal_filter=None if stype in ("intake", "debrief") else {"include_types": ["competency"]},
            pass_criteria=None,
            advance_behavior="auto_advance" if stype == "intake" else "manual_review",
        )
        db.add(row)
    await db.flush()

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    await pipelines_service.create_job_pipeline_from_template(
        db, job=job, template_id=tpl.id,
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    # Exactly 3 stages — no doubling
    assert len(stages) == 3
    assert stages[0].stage_type == "intake"
    assert stages[1].stage_type == "ai_screening"
    assert stages[2].stage_type == "debrief"
    _assert_bookends_and_contiguous(stages)


# ---------------------------------------------------------------------------
# from_starter: no bookends in any starter pack entry → added server-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_starter_seeds_bookends(db):
    """standard_technical (3 middle stages) must become intake + 3 + debrief = 5."""
    tenant, user, company = await _setup(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    await pipelines_service.create_job_pipeline_from_starter(
        db, job=job, starter_key="standard_technical",
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    assert len(stages) == 5
    _assert_bookends_and_contiguous(stages)
    assert stages[1].stage_type == "phone_screen"
    assert stages[-1].stage_type == "debrief"


@pytest.mark.asyncio
async def test_from_starter_single_stage_seeds_bookends(db):
    """volume_hiring (1 stage) must become intake + phone_screen + debrief = 3."""
    tenant, user, company = await _setup(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    await pipelines_service.create_job_pipeline_from_starter(
        db, job=job, starter_key="volume_hiring",
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    assert len(stages) == 3
    _assert_bookends_and_contiguous(stages)
    assert stages[1].stage_type == "phone_screen"


# ---------------------------------------------------------------------------
# from_scratch: no bookends → added server-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_scratch_without_bookends_seeds_intake_and_debrief(db):
    """from_scratch with [ai_screening] yields intake + ai_screening + debrief."""
    tenant, user, company = await _setup(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    await pipelines_service.create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[_make_stage(0, "AI Screen", "ai_screening")],
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    assert len(stages) == 3
    _assert_bookends_and_contiguous(stages)
    assert stages[1].stage_type == "ai_screening"


@pytest.mark.asyncio
async def test_from_scratch_with_bookends_does_not_double_seed(db):
    """from_scratch with explicit [intake, ai_screening, debrief] must NOT duplicate."""
    tenant, user, company = await _setup(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    intake_stage = PipelineStageInput(
        position=0,
        name="Intake",
        stage_type="intake",  # type: ignore[arg-type]
        duration_minutes=None,
        difficulty=None,
        signal_filter=None,
        pass_criteria=None,
        advance_behavior="auto_advance",  # type: ignore[arg-type]
    )
    debrief_stage = PipelineStageInput(
        position=2,
        name="Debrief",
        stage_type="debrief",  # type: ignore[arg-type]
        duration_minutes=None,
        difficulty=None,
        signal_filter=None,
        pass_criteria=None,
        advance_behavior="manual_review",  # type: ignore[arg-type]
    )

    await pipelines_service.create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[
            intake_stage,
            _make_stage(1, "AI Screen", "ai_screening"),
            debrief_stage,
        ],
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    # Exactly 3 — no extra bookends
    assert len(stages) == 3
    assert stages[0].stage_type == "intake"
    assert stages[1].stage_type == "ai_screening"
    assert stages[2].stage_type == "debrief"
    _assert_bookends_and_contiguous(stages)


# ---------------------------------------------------------------------------
# reset_to_source seeds bookends on the reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_to_source_seeds_bookends(db):
    """reset_job_pipeline_to_source re-seeds bookends from a template without them."""
    tenant, user, company = await _setup(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="No Bookend Template",
        description=None,
        is_default=False,
        stages=[_make_stage(0, "AI Screen", "ai_screening")],
    )

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    instance = await pipelines_service.create_job_pipeline_from_template(
        db, job=job, template_id=template.id,
    )

    # Edit to just one stage (no bookends — simulating a pre-migration pipeline)
    existing = list(
        (
            await db.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
        ).scalars().all()
    )
    for s in existing:
        await db.delete(s)
    await db.flush()

    bare_stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Only Stage",
        stage_type="ai_screening",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(bare_stage)
    await db.flush()

    # Now reset — should re-copy from template (which has no bookends) and seed them
    await pipelines_service.reset_job_pipeline_to_source(db, instance=instance)

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    assert len(stages) == 3
    _assert_bookends_and_contiguous(stages)
    assert stages[1].stage_type == "ai_screening"


# ---------------------------------------------------------------------------
# Intake bookend has canonical field values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seeded_intake_has_correct_locked_fields(db):
    """Seeded intake stage must have auto_advance behavior and None for forbidden cols."""
    tenant, user, company = await _setup(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    await pipelines_service.create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[_make_stage(0, "AI Screen", "ai_screening")],
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    intake = stages[0]
    assert intake.stage_type == "intake"
    assert intake.name == "Intake"
    assert intake.advance_behavior == "auto_advance"
    assert intake.duration_minutes is None
    assert intake.difficulty is None
    assert intake.signal_filter is None


@pytest.mark.asyncio
async def test_seeded_debrief_has_correct_locked_fields(db):
    """Seeded debrief stage must have manual_review behavior and None for forbidden cols."""
    tenant, user, company = await _setup(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    await pipelines_service.create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[_make_stage(0, "AI Screen", "ai_screening")],
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _, stages, _, _ = result

    debrief = stages[-1]
    assert debrief.stage_type == "debrief"
    assert debrief.name == "Debrief"
    assert debrief.advance_behavior == "manual_review"
    assert debrief.duration_minutes is None
    assert debrief.difficulty is None
    assert debrief.signal_filter is None
