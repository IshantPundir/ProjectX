"""Smoke test for the Candidate / CandidateJobAssignment / CandidateStageProgress ORM models."""
import pytest

from app.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
)
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


@pytest.mark.asyncio
async def test_candidate_round_trip(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Alice Example",
        email="alice@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assert candidate.id is not None
    assert candidate.pii_redacted_at is None
    assert candidate.created_at is not None


@pytest.mark.asyncio
async def test_assignment_and_progress_round_trip(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit.id,
        title="Senior Engineer",
        description_raw="R" * 60,
        created_by=user.id,
        status="draft",
    )
    db.add(job)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="ai_interview",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
    )
    db.add(stage)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Bob",
        email="bob@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    progress = CandidateStageProgress(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
    )
    db.add(progress)
    await db.flush()

    assert assignment.status == "active"
    assert assignment.id is not None
    assert progress.override is False
    assert progress.exited_at is None
