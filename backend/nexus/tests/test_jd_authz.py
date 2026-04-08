"""Tests for require_job_access() — org unit ancestry permission check."""

import uuid

import pytest
from fastapi import HTTPException

from app.models import JobPosting
from app.modules.auth.context import RoleAssignment, UserContext
from app.modules.jd.authz import require_job_access
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


def _make_ctx(user, assignments, is_super=False):
    return UserContext(
        user=user,
        is_super_admin=is_super,
        assignments=assignments,
    )


@pytest.mark.asyncio
async def test_super_admin_bypasses_ancestry_check(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id)
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=unit.id,
        title="T", description_raw="R" * 60,
        created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    ctx = _make_ctx(user, assignments=[], is_super=True)
    result = await require_job_access(db, job.id, ctx, "view")
    assert result.id == job.id


@pytest.mark.asyncio
async def test_grant_on_parent_allows_access_to_child_unit_job(db):
    """The critical case: jobs.view granted on a parent unit must allow
    access to a job in a child unit. This is the exact scenario that
    Day-1 Task 1 proved has_permission_in_unit() does NOT handle alone."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    parent = await create_test_org_unit(db, tenant.id, name="Parent", unit_type="division")
    child = await create_test_org_unit(
        db, tenant.id, name="Child", unit_type="team", parent_unit_id=parent.id,
    )
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=child.id,
        title="T", description_raw="R" * 60,
        created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=parent.id,
                org_unit_name="Parent",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["jobs.view"],
            ),
        ],
    )
    result = await require_job_access(db, job.id, ctx, "view")
    assert result.id == job.id


@pytest.mark.asyncio
async def test_grant_on_sibling_unit_does_not_allow_access(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit_a = await create_test_org_unit(db, tenant.id, name="A")
    unit_b = await create_test_org_unit(db, tenant.id, name="B")
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=unit_a.id,
        title="T", description_raw="R" * 60,
        created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=unit_b.id,
                org_unit_name="B",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["jobs.view"],
            ),
        ],
    )
    with pytest.raises(HTTPException) as exc_info:
        await require_job_access(db, job.id, ctx, "view")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_nonexistent_job_returns_404(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, assignments=[], is_super=True)

    with pytest.raises(HTTPException) as exc_info:
        await require_job_access(db, uuid.uuid4(), ctx, "view")
    assert exc_info.value.status_code == 404
