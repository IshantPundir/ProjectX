"""Tests for require_candidate_access() — candidate ancestry permission check."""

import uuid

import pytest
from fastapi import HTTPException

from app.models import (
    Candidate,
    CandidateJobAssignment,
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
)
from app.modules.auth.context import RoleAssignment, UserContext
from app.modules.candidates.authz import require_candidate_access
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


def _make_ctx(user, assignments, is_super=False):
    return UserContext(user=user, is_super_admin=is_super, assignments=assignments)


async def _make_candidate(db, tenant_id, created_by) -> Candidate:
    c = Candidate(
        tenant_id=tenant_id,
        name="Test Candidate",
        email=f"c-{uuid.uuid4().hex[:8]}@example.com",
        source="manual",
        created_by=created_by,
    )
    db.add(c)
    await db.flush()
    return c


async def _make_job_in_unit(db, tenant_id, org_unit_id, created_by) -> JobPosting:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="JD",
        description_raw="R" * 60,
        created_by=created_by,
        status="draft",
    )
    db.add(job)
    await db.flush()
    return job


async def _assign_candidate_to_job(db, tenant_id, candidate_id, job_id, assigned_by):
    # Build a pipeline stage so the assignment has a valid current_stage_id.
    instance = JobPipelineInstance(tenant_id=tenant_id, job_posting_id=job_id)
    db.add(instance)
    await db.flush()
    stage = JobPipelineStage(
        tenant_id=tenant_id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="ai_interview",
        duration_minutes=30,
        difficulty="easy",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
    )
    db.add(stage)
    await db.flush()
    a = CandidateJobAssignment(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        job_posting_id=job_id,
        current_stage_id=stage.id,
        assigned_by=assigned_by,
    )
    db.add(a)
    await db.flush()
    return a


@pytest.mark.asyncio
async def test_super_admin_bypasses_check(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)
    ctx = _make_ctx(user, assignments=[], is_super=True)
    got = await require_candidate_access(db, candidate.id, ctx, "view")
    assert got.id == candidate.id


@pytest.mark.asyncio
async def test_user_with_perm_on_job_org_unit_passes(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)
    job = await _make_job_in_unit(db, tenant.id, unit.id, user.id)
    await _assign_candidate_to_job(db, tenant.id, candidate.id, job.id, user.id)
    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=unit.id,
                org_unit_name=unit.name,
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["candidates.view"],
            )
        ],
    )
    got = await require_candidate_access(db, candidate.id, ctx, "view")
    assert got.id == candidate.id


@pytest.mark.asyncio
async def test_user_with_perm_on_parent_unit_inherits_to_child_job(db):
    """Ancestry walk: perm on division should reach a job assigned through a child team.

    Mirrors test_jd_authz.test_grant_on_parent_allows_access_to_child_unit_job."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    parent = await create_test_org_unit(db, tenant.id, name="Parent", unit_type="division")
    child = await create_test_org_unit(
        db, tenant.id, name="Child", unit_type="team", parent_unit_id=parent.id
    )
    candidate = await _make_candidate(db, tenant.id, user.id)
    job = await _make_job_in_unit(db, tenant.id, child.id, user.id)
    await _assign_candidate_to_job(db, tenant.id, candidate.id, job.id, user.id)
    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=parent.id,
                org_unit_name=parent.name,
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["candidates.view"],
            )
        ],
    )
    got = await require_candidate_access(db, candidate.id, ctx, "view")
    assert got.id == candidate.id


@pytest.mark.asyncio
async def test_user_without_permission_is_403(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)
    job = await _make_job_in_unit(db, tenant.id, unit.id, user.id)
    await _assign_candidate_to_job(db, tenant.id, candidate.id, job.id, user.id)
    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=unit.id,
                org_unit_name=unit.name,
                role_id=uuid.uuid4(),
                role_name="Observer",
                permissions=["reports.view"],
            )
        ],
    )
    with pytest.raises(HTTPException) as exc:
        await require_candidate_access(db, candidate.id, ctx, "view")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unassigned_candidate_visible_when_user_has_view_anywhere(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    other_unit = await create_test_org_unit(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)  # no assignments
    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=other_unit.id,
                org_unit_name=other_unit.name,
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["candidates.view"],
            )
        ],
    )
    got = await require_candidate_access(db, candidate.id, ctx, "view")
    assert got.id == candidate.id


@pytest.mark.asyncio
async def test_unassigned_candidate_403_when_user_lacks_view_anywhere(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    other_unit = await create_test_org_unit(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)
    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=other_unit.id,
                org_unit_name=other_unit.name,
                role_id=uuid.uuid4(),
                role_name="Observer",
                permissions=["reports.view"],
            )
        ],
    )
    with pytest.raises(HTTPException) as exc:
        await require_candidate_access(db, candidate.id, ctx, "view")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_candidate_not_found_is_404(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, assignments=[])
    with pytest.raises(HTTPException) as exc:
        await require_candidate_access(db, uuid.uuid4(), ctx, "view")
    assert exc.value.status_code == 404
