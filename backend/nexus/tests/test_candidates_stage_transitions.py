"""Tests for update_assignment_status + transition_stage."""
import uuid

import pytest
from sqlalchemy import select

from app.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
)
from app.modules.auth.context import UserContext
from app.modules.candidates import service
from app.modules.candidates.errors import (
    CandidateNotFoundError,
    StageNotInPipelineError,
)
from app.modules.candidates.schemas import (
    AssignmentCreateRequest,
    AssignmentStatus,
    AssignmentUpdateRequest,
    StageTransitionRequest,
)
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


def _make_ctx(user, assignments=None, is_super=False):
    return UserContext(user=user, is_super_admin=is_super, assignments=assignments or [])


async def _make_job_with_stages(db, tenant_id, user_id, stage_names=("Screening", "Interview", "Offer")):
    org_unit = await create_test_org_unit(db, tenant_id)
    job = JobPosting(
        tenant_id=tenant_id, org_unit_id=org_unit.id, title="Engineer",
        description_raw="R" * 60, created_by=user_id, status="draft",
    )
    db.add(job)
    await db.flush()
    instance = JobPipelineInstance(tenant_id=tenant_id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()
    stages = []
    for i, name in enumerate(stage_names):
        s = JobPipelineStage(
            tenant_id=tenant_id, instance_id=instance.id, position=i, name=name,
            stage_type="ai_interview", duration_minutes=30, difficulty="medium",
            signal_filter={}, pass_criteria={}, advance_behavior="manual",
        )
        db.add(s)
        stages.append(s)
    await db.flush()
    return job, stages


async def _seed_assignment(db, tenant, user):
    candidate = Candidate(
        tenant_id=tenant.id, name="Ada", email=f"ada-{uuid.uuid4().hex[:6]}@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()
    job, stages = await _make_job_with_stages(db, tenant.id, user.id)
    assignment = await service.create_assignment(
        db, candidate.id, AssignmentCreateRequest(job_posting_id=job.id), _make_ctx(user),
    )
    return candidate, job, stages, assignment


@pytest.mark.asyncio
async def test_update_assignment_status_changes_status_and_logs(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    _candidate, _job, _stages, assignment = await _seed_assignment(db, tenant, user)

    updated = await service.update_assignment_status(
        db,
        assignment.id,
        AssignmentUpdateRequest(status=AssignmentStatus.REJECTED),
        _make_ctx(user),
    )
    assert updated.status == "rejected"
    assert updated.status_changed_at is not None


@pytest.mark.asyncio
async def test_update_assignment_status_missing_raises(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    with pytest.raises(CandidateNotFoundError):
        await service.update_assignment_status(
            db,
            uuid.uuid4(),
            AssignmentUpdateRequest(status=AssignmentStatus.ARCHIVED),
            _make_ctx(user),
        )


@pytest.mark.asyncio
async def test_transition_stage_closes_current_and_opens_new(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    _candidate, _job, stages, assignment = await _seed_assignment(db, tenant, user)

    req = StageTransitionRequest(target_stage_id=stages[1].id, reason="moving forward")
    updated = await service.transition_stage(db, assignment.id, req, _make_ctx(user))
    assert updated.current_stage_id == stages[1].id

    rows = (await db.execute(
        select(CandidateStageProgress)
        .where(CandidateStageProgress.assignment_id == assignment.id)
        .order_by(CandidateStageProgress.entered_at)
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].exited_at is not None
    assert rows[0].outcome == "advanced"
    assert rows[1].stage_id == stages[1].id
    assert rows[1].exited_at is None
    assert rows[1].reason == "moving forward"
    assert rows[1].override is False


@pytest.mark.asyncio
async def test_transition_stage_honors_override_flag(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    _, _, stages, assignment = await _seed_assignment(db, tenant, user)

    req = StageTransitionRequest(target_stage_id=stages[2].id, override=True, reason="skip")
    await service.transition_stage(db, assignment.id, req, _make_ctx(user))

    rows = (await db.execute(
        select(CandidateStageProgress)
        .where(CandidateStageProgress.assignment_id == assignment.id)
        .order_by(CandidateStageProgress.entered_at)
    )).scalars().all()
    assert rows[-1].override is True


@pytest.mark.asyncio
async def test_transition_stage_rejects_foreign_stage(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    _, _, stages, assignment = await _seed_assignment(db, tenant, user)

    # Build a stage in a DIFFERENT job/pipeline
    _other_job, other_stages = await _make_job_with_stages(db, tenant.id, user.id)

    with pytest.raises(StageNotInPipelineError):
        await service.transition_stage(
            db,
            assignment.id,
            StageTransitionRequest(target_stage_id=other_stages[0].id),
            _make_ctx(user),
        )


@pytest.mark.asyncio
async def test_transition_stage_missing_assignment_raises(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    with pytest.raises(CandidateNotFoundError):
        await service.transition_stage(
            db,
            uuid.uuid4(),
            StageTransitionRequest(target_stage_id=uuid.uuid4()),
            _make_ctx(user),
        )
