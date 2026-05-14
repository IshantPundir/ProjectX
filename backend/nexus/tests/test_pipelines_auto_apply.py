"""Tests for auto_apply_pipeline_on_confirmation hook.

Verifies the resolution order:
  1. Last-used template in this org unit
  2. Org unit's default template
  3. System fallback starter (standard_technical)

Plus:
  - Skip when an instance already exists
  - confirm_signals must succeed even if auto_apply raises
"""

import pytest
import sqlalchemy
from sqlalchemy import select

from app.modules.jd.models import (
    JobPosting,
    JobPostingSignalSnapshot,
)
from app.modules.pipelines.models import JobPipelineInstance
from app.modules.jd.service import confirm_signals
from app.modules.pipelines import service as pipelines_service
from app.modules.pipelines.schemas import (
    PassCriteriaKnockout,
    PipelineStageInput,
    SignalFilter,
)
from app.modules.pipelines.starter_pack import SYSTEM_FALLBACK_STARTER
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "Fintech / Financial Services",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stage(position: int = 0, name: str = "Phone Screen") -> PipelineStageInput:
    return PipelineStageInput(
        position=position,
        name=name,
        stage_type="phone_screen",
        duration_minutes=10,
        difficulty="easy",
        signal_filter=SignalFilter(
            include_types=["competency", "experience", "credential", "behavioral"],
        ),
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior="auto_advance",
    )


async def _setup(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
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


async def _make_extracted_job_with_snapshot(
    db, tenant_id, org_unit_id, user_id
) -> JobPosting:
    """Create a job in signals_extracted state with a v1 snapshot."""
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="To Be Confirmed",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_extracted",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {
                "value": "Python", "type": "competency", "priority": "required",
                "weight": 2, "knockout": False, "stage": "interview",
                "source": "ai_extracted", "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="A senior backend engineer.",
    )
    db.add(snapshot)
    await db.flush()
    return job


# ---------------------------------------------------------------------------
# Resolution 1: last-used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_apply_uses_last_used_template_when_available(db):
    tenant, user, company = await _setup(db)

    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Last Used",
        description=None,
        is_default=False,
        stages=[_make_stage(0, "T1")],
    )

    # Job 1 — link via template
    job1 = await _make_confirmed_job(db, tenant.id, company.id, user.id, title="Job 1")
    await pipelines_service.create_job_pipeline_from_template(
        db, job=job1, template_id=template.id,
    )

    # Job 2 — auto-apply should pick up template
    job2 = await _make_confirmed_job(db, tenant.id, company.id, user.id, title="Job 2")
    instance2 = await pipelines_service.auto_apply_pipeline_on_confirmation(
        db, job=job2, actor_id=user.id,
    )

    assert instance2 is not None
    assert instance2.source_template_id == template.id


# ---------------------------------------------------------------------------
# Resolution 2: org default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_apply_falls_back_to_org_default_when_no_last_used(db):
    tenant, user, company = await _setup(db)

    default_tpl = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant.id,
        org_unit_id=company.id,
        created_by=user.id,
        name="Org Default",
        description=None,
        is_default=True,
        stages=[_make_stage(0, "D1")],
    )

    # No prior jobs / instances → no last-used template
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    instance = await pipelines_service.auto_apply_pipeline_on_confirmation(
        db, job=job, actor_id=user.id,
    )

    assert instance is not None
    assert instance.source_template_id == default_tpl.id


# ---------------------------------------------------------------------------
# Resolution 3: system fallback starter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_apply_falls_back_to_system_starter_when_no_templates_exist(db):
    tenant, user, company = await _setup(db)

    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    instance = await pipelines_service.auto_apply_pipeline_on_confirmation(
        db, job=job, actor_id=user.id,
    )

    assert instance is not None
    # System fallback uses starter pack directly → no source template id
    assert instance.source_template_id is None

    pair = await pipelines_service.get_job_pipeline_with_stages(db, job.id)
    assert pair is not None
    _inst, stages, _src, _pbs = pair
    # standard_technical has 3 middle stages; bookend seeding adds intake + debrief = 5 total
    assert len(stages) == 5
    assert stages[0].stage_type == "intake"
    assert stages[1].stage_type == "phone_screen"
    assert stages[-1].stage_type == "debrief"


# ---------------------------------------------------------------------------
# Skip when an instance already exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_apply_skipped_when_instance_already_exists(db):
    tenant, user, company = await _setup(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    # Pre-create instance manually
    existing = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(existing)
    await db.flush()

    result = await pipelines_service.auto_apply_pipeline_on_confirmation(
        db, job=job, actor_id=user.id,
    )
    assert result is None

    # Verify only the original instance exists
    rows = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    instances = list(rows.scalars().all())
    assert len(instances) == 1
    assert instances[0].id == existing.id


# ---------------------------------------------------------------------------
# confirm_signals must not break when auto_apply raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_signals_succeeds_when_auto_apply_fails(db, monkeypatch):
    tenant, user, company = await _setup(db)
    job = await _make_extracted_job_with_snapshot(
        db, tenant.id, company.id, user.id,
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("intentional auto-apply failure for test")

    monkeypatch.setattr(
        "app.modules.pipelines.service.auto_apply_pipeline_on_confirmation",
        _boom,
    )

    confirmed = await confirm_signals(
        db,
        job=job,
        actor_id=user.id,
        correlation_id="corr-test",
    )

    assert confirmed.status == "signals_confirmed"


# ---------------------------------------------------------------------------
# After design: confirm_signals does NOT auto-apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_signals_does_not_auto_apply_pipeline(db):
    """After this design lands, confirm_signals leaves the pipeline empty.
    The recruiter creates one explicitly via the picker (POST /api/jobs/{id}/pipeline).
    Pipeline-build via picker is a separate step (Task 6)."""
    tenant, user, company = await _setup(db)
    job = await _make_extracted_job_with_snapshot(
        db, tenant.id, company.id, user.id,
    )

    await confirm_signals(
        db,
        job=job,
        actor_id=user.id,
        correlation_id="cid",
    )

    result = await db.execute(
        select(JobPipelineInstance).where(JobPipelineInstance.job_posting_id == job.id)
    )
    assert result.scalar_one_or_none() is None
    await db.refresh(job)
    assert job.status == "signals_confirmed"
