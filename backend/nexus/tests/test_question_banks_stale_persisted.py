"""Persisted is_stale flag — set on writes, read directly from column.

Tests:
1. recompute_and_persist_stale flips is_stale=True and persists to DB.
2. A confirmed bank drops back to 'generated' when it goes stale.
3. stage_config_snapshot drift triggers stale.
4. No drift when snapshot and config match → is_stale stays False.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy
from sqlalchemy import select

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestionBank,
)
from app.modules.question_bank.service import recompute_and_persist_stale
from app.modules.question_bank.state_machine import transition_to_confirmed
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
# Shared helpers
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db, tenant_id) -> None:
    await db.execute(sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))


async def _setup_tenant_user_unit(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)
    return tenant, user, company


def _signal(*, value: str) -> dict:
    return {
        "value": value,
        "type": "competency",
        "priority": "required",
        "weight": 2,
        "knockout": False,
        "stage": "screen",
        "evaluation_method": "verification",
        "evaluation_hint": None,
        "source": "ai_extracted",
        "inference_basis": None,
    }


async def _make_job_and_confirmed_snapshot(
    db,
    tenant_id: UUID,
    org_unit_id: UUID,
    user_id: UUID,
    *,
    signals: list[dict],
    version: int = 1,
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=version,
        signals=signals,
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user_id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()
    return job, snapshot


async def _make_pipeline_and_stage(
    db,
    *,
    job: JobPosting,
    signal_filter: dict | None = None,
    difficulty: str = "medium",
) -> tuple[JobPipelineInstance, JobPipelineStage]:
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
        difficulty=difficulty,
        signal_filter=signal_filter or {"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()
    return instance, stage


async def _make_bank(
    db,
    *,
    job: JobPosting,
    stage: JobPipelineStage,
    snapshot: JobPostingSignalSnapshot,
    stage_config_snapshot: dict | None = None,
    status: str = "generated",
    is_stale: bool = False,
) -> StageQuestionBank:
    bank = StageQuestionBank(
        tenant_id=job.tenant_id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status=status,
        prompt_version="v1",
        stage_config_snapshot=stage_config_snapshot,
        is_stale=is_stale,
    )
    db.add(bank)
    await db.flush()
    return bank


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_persists_to_column(db):
    """recompute_and_persist_stale flips is_stale=True and persists to DB."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot_v1 = await _make_job_and_confirmed_snapshot(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")], version=1,
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await _make_bank(db, job=job, stage=stage, snapshot=snapshot_v1, is_stale=False)

    assert bank.is_stale is False

    # Add a newer confirmed snapshot — now v1 is superseded
    snapshot_v2 = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=2,
        signals=[_signal(value="Python"), _signal(value="Go")],
        seniority_level="senior",
        role_summary="Updated.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot_v2)
    await db.flush()

    new_stale = await recompute_and_persist_stale(db, bank)
    assert new_stale is True

    # Reload from DB to confirm persistence
    fresh = await db.get(StageQuestionBank, bank.id)
    assert fresh.is_stale is True


@pytest.mark.asyncio
async def test_confirmed_bank_drops_to_generated_on_stale(db):
    """Per §11.5: a confirmed bank drops back to 'generated' when it goes stale."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot_v1 = await _make_job_and_confirmed_snapshot(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")], version=1,
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)

    # Create bank in confirmed state
    bank = await _make_bank(
        db, job=job, stage=stage, snapshot=snapshot_v1, status="reviewing",
    )
    # Manually transition to confirmed (mimic confirm_bank logic)
    bank.status = "confirmed"
    bank.confirmed_at = datetime.now(UTC)
    bank.confirmed_by = user.id
    await db.flush()

    assert bank.status == "confirmed"
    assert bank.confirmed_at is not None

    # Add newer confirmed snapshot to make the bank stale
    snapshot_v2 = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=2,
        signals=[_signal(value="Python"), _signal(value="Go")],
        seniority_level="senior",
        role_summary="Updated.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot_v2)
    await db.flush()

    await recompute_and_persist_stale(db, bank)

    fresh = await db.get(StageQuestionBank, bank.id)
    assert fresh.is_stale is True
    # Stale confirmed banks drop back to the post-generation 'reviewing' state
    # so the recruiter is prompted to re-review before re-confirming.
    assert fresh.status == "reviewing"
    assert fresh.confirmed_at is None
    assert fresh.confirmed_by is None


@pytest.mark.asyncio
async def test_stage_config_drift_triggers_stale(db):
    """If stage_config_snapshot differs from current signal_filter/difficulty, bank is stale."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot_v1 = await _make_job_and_confirmed_snapshot(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")], version=1,
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, signal_filter={"include_types": ["competency"]}, difficulty="medium",
    )

    # Bank was generated with old config (difficulty=easy)
    bank = await _make_bank(
        db,
        job=job,
        stage=stage,
        snapshot=snapshot_v1,
        stage_config_snapshot={
            "signal_filter": {"include_types": ["competency"]},
            "difficulty": "easy",  # <-- differs from current "medium"
        },
        is_stale=False,
    )

    current_config = {
        "signal_filter": stage.signal_filter,
        "difficulty": stage.difficulty,  # "medium"
    }
    new_stale = await recompute_and_persist_stale(db, bank, current_stage_config=current_config)
    assert new_stale is True

    fresh = await db.get(StageQuestionBank, bank.id)
    assert fresh.is_stale is True


@pytest.mark.asyncio
async def test_no_drift_stays_not_stale(db):
    """When snapshot and config match, recompute leaves is_stale=False."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot_v1 = await _make_job_and_confirmed_snapshot(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")], version=1,
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, signal_filter={"include_types": ["competency"]}, difficulty="medium",
    )

    bank = await _make_bank(
        db,
        job=job,
        stage=stage,
        snapshot=snapshot_v1,
        stage_config_snapshot={
            "signal_filter": {"include_types": ["competency"]},
            "difficulty": "medium",
        },
        is_stale=False,
    )

    current_config = {
        "signal_filter": stage.signal_filter,
        "difficulty": stage.difficulty,
    }
    new_stale = await recompute_and_persist_stale(db, bank, current_stage_config=current_config)
    assert new_stale is False

    fresh = await db.get(StageQuestionBank, bank.id)
    assert fresh.is_stale is False
