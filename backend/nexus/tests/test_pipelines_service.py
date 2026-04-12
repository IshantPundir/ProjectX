"""Service layer tests for pipelines.

Tests cover template CRUD, job pipeline instances, and error conditions.
All tests use the `db` fixture for an RLS-configured AsyncSession."""

import uuid

import pytest
import sqlalchemy
from sqlalchemy import select

from app.models import (
    JobPipelineStage,
    JobPosting,
    PipelineTemplateStage,
)
from app.modules.pipelines import service as pipelines_service
from app.modules.pipelines.errors import (
    CannotDeleteDefaultError,
    JobNotInConfirmedStateError,
    NoSourceTemplateError,
    PipelineAlreadyExistsError,
    StarterKeyNotFoundError,
)
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
    name: str = "Phone Screen",
    stage_type: str = "phone_screen",
) -> PipelineStageInput:
    return PipelineStageInput(
        position=position,
        name=name,
        stage_type=stage_type,  # type: ignore[arg-type]
        duration_minutes=10,
        difficulty="easy",
        signal_filter=SignalFilter(
            include_types=["competency", "experience", "credential", "behavioral"],
            include_stages=["screen"],
            include_weights=[1, 2, 3],
            include_priority=["required", "preferred"],
        ),
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior="auto_advance",
    )


async def _setup_tenant_user_unit(db):
    """Create a tenant + user + company org unit, set RLS, return all three."""
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
    db, tenant_id: uuid.UUID, org_unit_id: uuid.UUID, user_id: uuid.UUID,
    *, status: str = "signals_confirmed",
) -> JobPosting:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status=status,
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()
    return job


# ---------------------------------------------------------------------------
# Template creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template_from_scratch_persists_stages(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Test Template",
        description=None,
        is_default=False,
        stages=[
            _make_stage(0, "Phone Screen", "phone_screen"),
            _make_stage(1, "AI Interview", "ai_interview"),
        ],
    )

    pair = await pipelines_service.get_template_with_stages(db, template.id)
    assert pair is not None
    tpl, stages = pair
    assert tpl.name == "Test Template"
    assert tpl.from_starter is None
    assert tpl.is_default is False
    assert len(stages) == 2
    assert stages[0].position == 0
    assert stages[0].name == "Phone Screen"
    assert stages[1].position == 1
    assert stages[1].name == "AI Interview"
    assert stages[1].stage_type == "ai_interview"


@pytest.mark.asyncio
async def test_create_template_from_starter_uses_standard_technical(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_starter(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        starter_key="standard_technical",
        name="My Standard",
        description=None,
        is_default=False,
    )

    pair = await pipelines_service.get_template_with_stages(db, template.id)
    assert pair is not None
    tpl, stages = pair
    assert tpl.from_starter == "standard_technical"
    assert tpl.name == "My Standard"
    assert len(stages) == 3
    assert stages[0].stage_type == "phone_screen"
    assert stages[1].stage_type == "ai_interview"
    assert stages[2].stage_type == "panel_interview"


@pytest.mark.asyncio
async def test_create_template_from_starter_unknown_key_raises_error(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    with pytest.raises(StarterKeyNotFoundError):
        await pipelines_service.create_template_from_starter(
            db,
            tenant_id=tenant.id,
            org_unit_id=company.id,
            created_by=user.id,
            starter_key="not_a_real_key",
            name="Whatever",
            description=None,
            is_default=False,
        )


@pytest.mark.asyncio
async def test_create_template_with_is_default_clears_previous_default(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    a = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Template A",
        description=None,
        is_default=True,
        stages=[_make_stage(0)],
    )
    assert a.is_default is True

    b = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Template B",
        description=None,
        is_default=True,
        stages=[_make_stage(0)],
    )

    await db.refresh(a)
    assert b.is_default is True
    assert a.is_default is False


@pytest.mark.asyncio
async def test_set_template_as_default_atomic_toggle(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    a = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="A",
        description=None,
        is_default=True,
        stages=[_make_stage(0)],
    )
    b = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="B",
        description=None,
        is_default=False,
        stages=[_make_stage(0)],
    )

    await pipelines_service.set_template_as_default(db, b, actor_id=user.id)
    await db.refresh(a)
    await db.refresh(b)
    assert b.is_default is True
    assert a.is_default is False


# ---------------------------------------------------------------------------
# Template deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_default_template_raises_cannot_delete(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Default",
        description=None,
        is_default=True,
        stages=[_make_stage(0)],
    )

    with pytest.raises(CannotDeleteDefaultError):
        await pipelines_service.delete_template(db, template)


@pytest.mark.asyncio
async def test_delete_non_default_template_succeeds_and_cascades_stages(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Disposable",
        description=None,
        is_default=False,
        stages=[_make_stage(0), _make_stage(1, "Stage 1", "ai_interview")],
    )
    template_id = template.id

    await pipelines_service.delete_template(db, template)

    pair = await pipelines_service.get_template_with_stages(db, template_id)
    assert pair is None

    leftover = await db.execute(
        select(PipelineTemplateStage).where(
            PipelineTemplateStage.template_id == template_id
        )
    )
    assert leftover.scalars().first() is None


# ---------------------------------------------------------------------------
# Template update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_template_replaces_stages_atomically(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Original",
        description=None,
        is_default=False,
        stages=[
            _make_stage(0, "Stage A", "phone_screen"),
            _make_stage(1, "Stage B", "ai_interview"),
            _make_stage(2, "Stage C", "panel_interview"),
        ],
    )

    await pipelines_service.update_template(
        db,
        template=template,
        name=None,
        description=None,
        stages=[
            _make_stage(0, "New Stage 1", "phone_screen"),
            _make_stage(1, "New Stage 2", "ai_interview"),
        ],
        actor_id=user.id,
    )

    pair = await pipelines_service.get_template_with_stages(db, template.id)
    assert pair is not None
    _tpl, stages = pair
    assert len(stages) == 2
    assert stages[0].name == "New Stage 1"
    assert stages[1].name == "New Stage 2"


@pytest.mark.asyncio
async def test_update_template_name_without_stages_preserves_stages(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Old Name",
        description=None,
        is_default=False,
        stages=[
            _make_stage(0, "Original A", "phone_screen"),
            _make_stage(1, "Original B", "ai_interview"),
        ],
    )

    await pipelines_service.update_template(
        db,
        template=template,
        name="New Name",
        description="A new description",
        stages=None,
        actor_id=user.id,
    )

    pair = await pipelines_service.get_template_with_stages(db, template.id)
    assert pair is not None
    tpl, stages = pair
    assert tpl.name == "New Name"
    assert tpl.description == "A new description"
    assert len(stages) == 2
    assert stages[0].name == "Original A"
    assert stages[1].name == "Original B"


# ---------------------------------------------------------------------------
# Job pipeline instance creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_pipeline_from_template_copies_stages(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Source Template",
        description=None,
        is_default=False,
        stages=[
            _make_stage(0, "S1", "phone_screen"),
            _make_stage(1, "S2", "ai_interview"),
        ],
    )

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = await pipelines_service.create_job_pipeline_from_template(
        db, job=job, template_id=template.id,
    )

    assert instance.source_template_id == template.id

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    inst, stages, src = result
    assert src is not None
    assert src.id == template.id
    assert len(stages) == 2
    assert stages[0].name == "S1"
    assert stages[1].name == "S2"


@pytest.mark.asyncio
async def test_create_job_pipeline_from_starter_has_null_source_template_id(db):
    tenant, user, company = await _setup_tenant_user_unit(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = await pipelines_service.create_job_pipeline_from_starter(
        db, job=job, starter_key="standard_technical",
    )

    assert instance.source_template_id is None

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _inst, stages, src = result
    assert src is None
    assert len(stages) == 3


@pytest.mark.asyncio
async def test_create_job_pipeline_from_scratch_has_null_source_template_id(db):
    tenant, user, company = await _setup_tenant_user_unit(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = await pipelines_service.create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[_make_stage(0), _make_stage(1, "AI", "ai_interview")],
    )

    assert instance.source_template_id is None

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _inst, stages, src = result
    assert src is None
    assert len(stages) == 2


@pytest.mark.asyncio
async def test_create_job_pipeline_rejects_non_confirmed_job(db):
    tenant, user, company = await _setup_tenant_user_unit(db)
    job = await _make_confirmed_job(
        db, tenant.id, company.id, user.id, status="signals_extracted"
    )

    with pytest.raises(JobNotInConfirmedStateError):
        await pipelines_service.create_job_pipeline_from_starter(
            db, job=job, starter_key="standard_technical",
        )


@pytest.mark.asyncio
async def test_create_job_pipeline_rejects_duplicate(db):
    tenant, user, company = await _setup_tenant_user_unit(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    await pipelines_service.create_job_pipeline_from_starter(
        db, job=job, starter_key="standard_technical",
    )

    with pytest.raises(PipelineAlreadyExistsError):
        await pipelines_service.create_job_pipeline_from_starter(
            db, job=job, starter_key="fast_track",
        )


# ---------------------------------------------------------------------------
# Job pipeline instance updates / reset / save-as / write-back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_pipeline_replaces_stages(db):
    tenant, user, company = await _setup_tenant_user_unit(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = await pipelines_service.create_job_pipeline_from_starter(
        db, job=job, starter_key="standard_technical",
    )

    await pipelines_service.update_job_pipeline_stages(
        db,
        instance=instance,
        stages=[_make_stage(0, "Only Stage", "phone_screen")],
    )

    result = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert result is not None
    _inst, stages, _src = result
    assert len(stages) == 1
    assert stages[0].name == "Only Stage"


@pytest.mark.asyncio
async def test_reset_job_pipeline_restores_from_source_template(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Source",
        description=None,
        is_default=False,
        stages=[
            _make_stage(0, "TplA", "phone_screen"),
            _make_stage(1, "TplB", "ai_interview"),
        ],
    )

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = await pipelines_service.create_job_pipeline_from_template(
        db, job=job, template_id=template.id,
    )

    await pipelines_service.update_job_pipeline_stages(
        db,
        instance=instance,
        stages=[_make_stage(0, "Local Edit", "phone_screen")],
    )
    pre = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert pre is not None
    assert len(pre[1]) == 1

    await pipelines_service.reset_job_pipeline_to_source(db, instance=instance)

    post = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert post is not None
    _inst, stages, _src = post
    assert len(stages) == 2
    assert stages[0].name == "TplA"
    assert stages[1].name == "TplB"


@pytest.mark.asyncio
async def test_reset_job_pipeline_raises_when_no_source(db):
    tenant, user, company = await _setup_tenant_user_unit(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = await pipelines_service.create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[_make_stage(0)],
    )

    with pytest.raises(NoSourceTemplateError):
        await pipelines_service.reset_job_pipeline_to_source(db, instance=instance)


@pytest.mark.asyncio
async def test_save_job_pipeline_as_template_creates_library_entry(db):
    tenant, user, company = await _setup_tenant_user_unit(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = await pipelines_service.create_job_pipeline_from_scratch(
        db,
        job=job,
        stages=[
            _make_stage(0, "Custom A", "phone_screen"),
            _make_stage(1, "Custom B", "ai_interview"),
        ],
    )

    template = await pipelines_service.save_job_pipeline_as_template(
        db,
        job=job,
        instance=instance,
        name="Saved Template",
        description="Saved from job",
        is_default=False,
        actor_id=user.id,
    )

    pair = await pipelines_service.get_template_with_stages(db, template.id)
    assert pair is not None
    tpl, stages = pair
    assert tpl.name == "Saved Template"
    assert tpl.org_unit_id == company.id
    assert len(stages) == 2
    assert stages[0].name == "Custom A"
    assert stages[1].name == "Custom B"


@pytest.mark.asyncio
async def test_update_source_template_writes_back_stages(db):
    tenant, user, company = await _setup_tenant_user_unit(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Source",
        description=None,
        is_default=False,
        stages=[_make_stage(0, "Old A", "phone_screen")],
    )

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    instance = await pipelines_service.create_job_pipeline_from_template(
        db, job=job, template_id=template.id,
    )

    await pipelines_service.update_job_pipeline_stages(
        db,
        instance=instance,
        stages=[
            _make_stage(0, "Edited A", "phone_screen"),
            _make_stage(1, "New B", "ai_interview"),
        ],
    )

    await pipelines_service.update_source_template_from_job(
        db, instance=instance, actor_id=user.id,
    )

    pair = await pipelines_service.get_template_with_stages(db, template.id)
    assert pair is not None
    _tpl, stages = pair
    assert len(stages) == 2
    assert stages[0].name == "Edited A"
    assert stages[1].name == "New B"
