"""Tests for reset_banks_for_job — bulk clear+draft of all banks for a job.

Used by JD re-extraction: when signals are re-extracted, every bank
generated from the prior snapshot is invalidated and reset to draft so
the questions UI shows the "Generate" call-to-action again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy
from sqlalchemy import func, select

from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.question_bank import reset_banks_for_job
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

pytestmark = pytest.mark.asyncio

_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "Fintech / Financial Services",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Helpers (mirrored from tests/test_question_banks_service.py)
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db, tenant_id) -> None:
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


async def _setup_tenant_user_unit(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)
    return tenant, user, company


async def _make_job(db, tenant_id, org_unit_id, user_id):
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=1,
        signals=[{
            "value": "Python",
            "type": "competency",
            "priority": "required",
            "weight": 2,
            "knockout": False,
            "stage": "screen",
            "evaluation_method": "verification",
            "evaluation_hint": None,
            "source": "ai_extracted",
            "inference_basis": None,
        }],
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user_id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()
    return job, snapshot


async def _make_pipeline_and_stage(db, *, job: JobPosting) -> tuple[JobPipelineInstance, JobPipelineStage]:
    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=job.tenant_id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "experience", "credential", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()
    return instance, stage


def _make_ai_question(bank: StageQuestionBank) -> StageQuestion:
    return StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=0,
        source="ai_generated",
        text="Describe a production incident you handled end-to-end.",
        primary_signal="Python",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=["names tools", "describes impact", "post-mortem"],
        red_flags=["no specifics", "blames team"],
        rubric={
            "excellent": "Strong answer with concrete specifics.",
            "meets_bar": "Acceptable with some structure.",
            "below_bar": "Vague with no tools named.",
        },
        evaluation_hint="Look for ownership and structured analysis.",
        question_kind="technical_scenario",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reset_banks_for_job_wipes_questions_and_drafts_bank(db):
    """A confirmed bank with stale generation_error + is_stale=True is fully reset:
    status→draft, all timestamps/notes→None, generation_error→None, is_stale→False,
    and all AI questions wiped."""
    tenant, user, company = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job(db, tenant.id, company.id, user.id)
    _instance, stage = await _make_pipeline_and_stage(db, job=job)

    # Create bank in confirmed status with stale generation_error and is_stale set —
    # the primary scenario reset_banks_for_job must unlock (a previously confirmed bank
    # that went stale and had a generation failure on a re-run).
    now = datetime.now(UTC)
    bank = StageQuestionBank(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        stage_id=stage.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        prompt_version="v2",
        generated_at=now,
        generated_by=user.id,
        coverage_notes="Covers 3 signals.",
        confirmed_at=now,
        confirmed_by=user.id,
        pipeline_version_at_generation=1,
        is_stale=True,
        generation_error="boom",
    )
    db.add(bank)
    await db.flush()

    q = _make_ai_question(bank)
    db.add(q)
    await db.flush()

    # Confirm at least one question exists before the reset
    q_count_before = (await db.execute(
        select(func.count()).where(StageQuestion.bank_id == bank.id)
    )).scalar_one()
    assert q_count_before >= 1

    # Act
    reset_count = await reset_banks_for_job(db, job_id=job.id)

    # Return value
    assert reset_count >= 1

    # Reload the bank from the session (flush already happened inside reset_banks_for_job)
    await db.refresh(bank)

    assert bank.status == "draft"
    assert bank.generated_at is None
    assert bank.generated_by is None
    assert bank.coverage_notes is None
    assert bank.confirmed_at is None
    assert bank.confirmed_by is None
    assert bank.generation_error is None
    assert bank.is_stale is False

    # All AI questions must be gone
    q_count_after = (await db.execute(
        select(func.count()).where(StageQuestion.bank_id == bank.id)
    )).scalar_one()
    assert q_count_after == 0


async def test_reset_banks_for_job_no_banks_returns_zero(db):
    """A job with no banks at all → reset_banks_for_job returns 0, no error."""
    tenant, user, company = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job(db, tenant.id, company.id, user.id)

    # No pipeline, no banks — just a fresh job_posting
    fresh_job_id = job.id

    reset_count = await reset_banks_for_job(db, job_id=fresh_job_id)
    assert reset_count == 0
