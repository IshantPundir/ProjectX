"""End-to-end router integration tests for the JD module.

Auth pattern: dependency overrides + verify_access_token patch.

The existing Phase 1 tests only exercise unauthenticated paths — there is
no JWT issuance helper in Phase 1. The auth middleware uses JWKS (ES256)
against a local Supabase endpoint, which is not accessible inside the
test container without a live Supabase session.

Strategy used here (documented in task spec escalation path):
  1. Patch `app.middleware.auth.verify_access_token` to accept a synthetic
     test token and return a TokenPayload. This lets the middleware pass
     and set request.state correctly.
  2. Override `get_current_user_roles` to return a UserContext built from the
     test user (bypasses the DB lookup in the auth module).
  3. Override `get_tenant_db` to open a session on the test engine with
     RLS tenant set (bypasses the production engine).

The router, service, state machine, exception handlers, and DB are all
exercised against real Postgres — only the JWT cryptography is stubbed.

Covers:
- create happy path (201, signals_extracting, snapshot=None)
- create blocked by missing company profile (422)
- create missing jobs.create permission (403)
- get non-existent job (404)
- retry on a non-failed job (409)
- list visible to super admin
"""

import uuid
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import JobPosting, User
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
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

# Sentinel token recognised only in tests
_TEST_BEARER = "test-integration-token"


# ---------------------------------------------------------------------------
# Dependency override + middleware patch helpers
# ---------------------------------------------------------------------------

def _setup_test_context(
    db: AsyncSession,
    user: User,
    tenant_id: uuid.UUID,
    is_super_admin: bool = False,
):
    """Install all overrides needed for a test request.

    Returns (headers, restore_fn).

    Three layers:
      1. Patch verify_access_token (in the auth middleware module) to
         return a valid TokenPayload for _TEST_BEARER — middleware passes.
      2. Override get_current_user_roles → return pre-built UserContext.
      3. Override get_tenant_db → reuse the *same* db session so the test's
         pre-committed data is visible (same connection, same transaction).

    Reusing the `db` session is critical: the conftest fixture wraps everything
    in a connection-level savepoint. A new connection would not see rows
    flushed/committed inside that savepoint.
    """
    from app.database import get_tenant_db

    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )

    ctx = UserContext(
        user=user,
        is_super_admin=is_super_admin,
        workspace_mode="enterprise",
        assignments=[],
    )

    def _fake_verify(token: str):
        if token == _TEST_BEARER:
            return fake_payload
        return None

    async def _user_override() -> UserContext:
        return ctx

    async def _db_override():
        """Yield the *same* db session the test uses.

        We need to SET LOCAL app.current_tenant on it before yielding so the
        router's RLS-scoped queries work correctly."""
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    # Patch verify_access_token where it is imported in the middleware
    verify_patch = patch("app.middleware.auth.verify_access_token", side_effect=_fake_verify)
    verify_patch.start()

    headers = {"Authorization": f"Bearer {_TEST_BEARER}"}

    def restore():
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    return headers, restore


# ---------------------------------------------------------------------------
# Happy path — create a job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_job_happy_path(db: AsyncSession, monkeypatch):
    """Super admin creates a job; response is 201, status=signals_extracting,
    latest_snapshot=None (actor was stubbed out)."""
    # Stub actor dispatch so nothing is enqueued
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/api/jobs",
                json={
                    "org_unit_id": str(company.id),
                    "title": "Sr. Python Engineer",
                    "description_raw": "A" * 200,
                    "project_scope_raw": None,
                    "target_headcount": 1,
                    "deadline": None,
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "signals_extracting"
    assert data["latest_snapshot"] is None
    assert data["title"] == "Sr. Python Engineer"
    assert data["org_unit_id"] == str(company.id)


# ---------------------------------------------------------------------------
# 422 — no company profile in ancestry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_job_blocked_without_profile_returns_422(db: AsyncSession, monkeypatch):
    """Creating a JD under an org unit with no profile in ancestry returns 422."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    # division has no company_profile
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    tenant.super_admin_id = user.id
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/api/jobs",
                json={
                    "org_unit_id": str(division.id),
                    "title": "Engineer",
                    "description_raw": "A" * 200,
                    "project_scope_raw": None,
                    "target_headcount": None,
                    "deadline": None,
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 422, response.text
    data = response.json()
    assert "company profile" in data["detail"].lower()
    assert data["org_unit_id"] == str(division.id)


# ---------------------------------------------------------------------------
# 403 — missing jobs.create permission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_job_missing_permission_returns_403(db: AsyncSession, monkeypatch):
    """Non-super-admin with no jobs.create assignment → 403."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        company_profile=_VALID_PROFILE,
    )
    # do NOT set tenant.super_admin_id and do NOT assign any roles
    await db.commit()

    # is_super_admin=False, no assignments → permission check fails
    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=False)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/api/jobs",
                json={
                    "org_unit_id": str(company.id),
                    "title": "Engineer",
                    "description_raw": "A" * 200,
                    "project_scope_raw": None,
                    "target_headcount": None,
                    "deadline": None,
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# 404 — non-existent job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_nonexistent_job_returns_404(db: AsyncSession):
    """GET /api/jobs/{random_uuid} → 404."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    tenant.super_admin_id = user.id
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(f"/api/jobs/{uuid.uuid4()}", headers=headers)
    finally:
        restore()

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# 409 — retry on a non-failed job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_on_non_failed_job_returns_409(db: AsyncSession, monkeypatch):
    """POST /api/jobs/{id}/retry on a signals_extracting job → 409 Conflict."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    # Insert a job directly in signals_extracting state (skipping actor dispatch)
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test Job",
        description_raw="A" * 200,
        status="signals_extracting",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(f"/api/jobs/{job.id}/retry", headers=headers)
    finally:
        restore()

    # signals_extracting → signals_extracting is an illegal transition → 409
    assert response.status_code == 409, response.text
    data = response.json()
    assert "already being processed" in data["detail"].lower()


# ---------------------------------------------------------------------------
# List — super admin sees all jobs in tenant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_jobs_super_admin_sees_all(db: AsyncSession, monkeypatch):
    """Super admin listing jobs gets back all jobs in the tenant (no visibility filter)."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    # Insert two jobs directly
    for i in range(2):
        job = JobPosting(
            tenant_id=tenant.id,
            org_unit_id=company.id,
            title=f"Job {i}",
            description_raw="A" * 200,
            status="signals_extracting",
            source="native",
            created_by=user.id,
        )
        db.add(job)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/api/jobs", headers=headers)
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) >= 2
