"""Integration tests for the assignable-users endpoint.

Exercises GET /api/jobs/{job_id}/pipeline/assignable-users?role=interviewer|observer|reviewer

Covers:
  - interviewer pool includes Interviewer + Hiring Manager, excludes Recruiter
  - reviewer pool is Hiring Manager only
  - observer pool includes Observer/Interviewer/Hiring Manager/Recruiter, excludes Admin
  - sibling org unit user NOT included; ancestor unit user IS included
  - inactive users excluded from all pools
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import User
from tests.conftest import (
    create_test_org_unit,
    create_test_user,
)

# Import shared helpers from test_pipeline_participants — they are module-private
# by convention but importable for cross-file reuse (pattern used here).
from tests.test_pipeline_participants import (
    _assign_role,
    _lookup_role_id,
    _make_job_with_signals,
    _set_tenant_ctx,
    _setup_tenant_user_unit,
    _setup_test_context,
)

_TEST_BEARER = "test-assignable-users-token"


# ---------------------------------------------------------------------------
# Test 1 — interviewer pool includes Hiring Manager, excludes Recruiter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interviewer_pool_includes_hiring_managers(db: AsyncSession):
    """Interviewer + Hiring Manager appear; Recruiter does NOT."""
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    # Create a team unit under company
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=company.id
    )

    # Three users with different roles assigned to the team unit
    interviewer_user = await create_test_user(db, tenant.id)
    hm_user = await create_test_user(db, tenant.id)
    recruiter_user = await create_test_user(db, tenant.id)

    await _assign_role(db, user=interviewer_user, org_unit=team, role_name="Interviewer")
    await _assign_role(db, user=hm_user, org_unit=team, role_name="Hiring Manager")
    await _assign_role(db, user=recruiter_user, org_unit=team, role_name="Recruiter")

    # Job lives in the team unit
    job, _ = await _make_job_with_signals(db, tenant.id, team.id, admin_user.id)

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{job.id}/pipeline/assignable-users",
                params={"role": "interviewer"},
                headers=headers,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)

        user_ids = {entry["user_id"] for entry in data}
        assert str(interviewer_user.id) in user_ids, "Interviewer should be in pool"
        assert str(hm_user.id) in user_ids, "Hiring Manager should be in pool"
        assert str(recruiter_user.id) not in user_ids, "Recruiter should NOT be in interviewer pool"
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2 — reviewer pool is Hiring Manager only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_pool_is_hiring_managers_only(db: AsyncSession):
    """Only Hiring Manager appears in reviewer pool; Interviewer does NOT."""
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    hm_user = await create_test_user(db, tenant.id)
    interviewer_user = await create_test_user(db, tenant.id)

    await _assign_role(db, user=hm_user, org_unit=company, role_name="Hiring Manager")
    await _assign_role(db, user=interviewer_user, org_unit=company, role_name="Interviewer")

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{job.id}/pipeline/assignable-users",
                params={"role": "reviewer"},
                headers=headers,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)

        user_ids = {entry["user_id"] for entry in data}
        assert str(hm_user.id) in user_ids, "Hiring Manager should be in reviewer pool"
        assert str(interviewer_user.id) not in user_ids, "Interviewer should NOT be in reviewer pool"
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3 — observer pool is broad (Observer/Interviewer/HM/Recruiter but NOT Admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_pool_is_broad(db: AsyncSession):
    """Observer pool includes Observer/Interviewer/HM/Recruiter; Admin does NOT appear.

    The gate for observer in _ROLE_GATE is:
        ("Observer", "Interviewer", "Hiring Manager", "Recruiter")
    Confirmed by reading participants.py.
    """
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    observer_user = await create_test_user(db, tenant.id)
    interviewer_user = await create_test_user(db, tenant.id)
    hm_user = await create_test_user(db, tenant.id)
    recruiter_user = await create_test_user(db, tenant.id)
    admin_role_user = await create_test_user(db, tenant.id)

    await _assign_role(db, user=observer_user, org_unit=company, role_name="Observer")
    await _assign_role(db, user=interviewer_user, org_unit=company, role_name="Interviewer")
    await _assign_role(db, user=hm_user, org_unit=company, role_name="Hiring Manager")
    await _assign_role(db, user=recruiter_user, org_unit=company, role_name="Recruiter")
    await _assign_role(db, user=admin_role_user, org_unit=company, role_name="Admin")

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{job.id}/pipeline/assignable-users",
                params={"role": "observer"},
                headers=headers,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)

        user_ids = {entry["user_id"] for entry in data}
        assert str(observer_user.id) in user_ids, "Observer should be in observer pool"
        assert str(interviewer_user.id) in user_ids, "Interviewer should be in observer pool"
        assert str(hm_user.id) in user_ids, "Hiring Manager should be in observer pool"
        assert str(recruiter_user.id) in user_ids, "Recruiter should be in observer pool"
        assert str(admin_role_user.id) not in user_ids, "Admin should NOT be in observer pool"
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 4 — sibling unit user NOT included; ancestor unit user IS included
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sibling_unit_user_not_included(db: AsyncSession):
    """User assigned only in a sibling unit is excluded; user in ancestor IS included."""
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    # Job lives in a team unit under company
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=company.id
    )

    # Sibling: another team also under company, NOT an ancestor of job's team
    sibling_team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=company.id
    )

    # Interviewer in sibling (should NOT appear)
    sibling_interviewer = await create_test_user(db, tenant.id)
    await _assign_role(db, user=sibling_interviewer, org_unit=sibling_team, role_name="Interviewer")

    # Interviewer at ancestor (company) level — should appear
    ancestor_interviewer = await create_test_user(db, tenant.id)
    await _assign_role(db, user=ancestor_interviewer, org_unit=company, role_name="Interviewer")

    job, _ = await _make_job_with_signals(db, tenant.id, team.id, admin_user.id)

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{job.id}/pipeline/assignable-users",
                params={"role": "interviewer"},
                headers=headers,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)

        user_ids = {entry["user_id"] for entry in data}
        assert str(sibling_interviewer.id) not in user_ids, (
            "User from sibling unit should NOT be in pool"
        )
        assert str(ancestor_interviewer.id) in user_ids, (
            "User from ancestor unit (company) should be in pool"
        )
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 5 — inactive users excluded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inactive_users_excluded(db: AsyncSession):
    """Deactivated user does not appear in the assignable pool."""
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    interviewer_user = await create_test_user(db, tenant.id)
    await _assign_role(db, user=interviewer_user, org_unit=company, role_name="Interviewer")

    # Deactivate the user directly via SQL update through the session
    await db.execute(
        sqlalchemy.text(
            "UPDATE users SET is_active = false WHERE id = :uid"
        ).bindparams(uid=interviewer_user.id)
    )
    await db.flush()

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{job.id}/pipeline/assignable-users",
                params={"role": "interviewer"},
                headers=headers,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)

        user_ids = {entry["user_id"] for entry in data}
        assert str(interviewer_user.id) not in user_ids, (
            "Deactivated user should NOT appear in assignable pool"
        )
    finally:
        restore()
