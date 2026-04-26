"""Router-level smoke tests for /api/candidates/*.

Auth pattern mirrors test_jd_router.py / test_pipelines_router.py:
  1. Patch `app.middleware.auth.verify_access_token` to accept a sentinel
     bearer token so the AuthMiddleware passes.
  2. Override `get_current_user_roles` to return a prebuilt UserContext.
  3. Override `get_tenant_db` to yield the test's own `db` session so the
     rows the test flushes are visible to router code.

Task 15 will register the candidates routers in main.py. For now, the
tests self-register them (idempotent — no-op if Task 15 has already
landed).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.main import app
from app.models import Candidate
from app.modules.auth.context import RoleAssignment, UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.modules.candidates.router import kanban_router, router as candidates_router
from tests.conftest import create_test_client, create_test_user

_TEST_BEARER = "test-integration-token"


# ---------------------------------------------------------------------------
# Router registration shim — remove once Task 15 lands
# ---------------------------------------------------------------------------

def _ensure_routers_registered() -> None:
    """Idempotently include candidates_router and kanban_router on the app.

    Task 15 does this in main.py. Until that lands, the test file does it
    itself so the endpoints are reachable. Checks by prefix so repeated
    runs (or running after Task 15 merges) don't double-register.
    """
    existing_prefixes = {
        getattr(r, "path_format", "") or getattr(r, "path", "")
        for r in app.routes
    }
    # Use a sentinel path from each router that will never collide with
    # another module.
    if not any(p.startswith("/api/candidates") for p in existing_prefixes):
        app.include_router(candidates_router)
    if not any(
        p.startswith("/api/jobs") and p.endswith("/candidates/kanban")
        for p in existing_prefixes
    ):
        app.include_router(kanban_router)


_ensure_routers_registered()


# ---------------------------------------------------------------------------
# Dependency override helpers
# ---------------------------------------------------------------------------

def _user_ctx(
    user,
    *,
    is_super: bool = False,
    permissions: tuple[str, ...] = ("candidates.view", "candidates.manage"),
) -> UserContext:
    assignments: list[RoleAssignment] = []
    if permissions:
        assignments.append(
            RoleAssignment(
                org_unit_id=uuid.uuid4(),
                org_unit_name="Root",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=list(permissions),
            )
        )
    return UserContext(
        user=user,
        is_super_admin=is_super,
        workspace_mode="enterprise",
        assignments=assignments,
    )


def _setup_test_context(
    db: AsyncSession,
    user,
    tenant_id: uuid.UUID,
    *,
    is_super: bool = False,
    permissions: tuple[str, ...] = ("candidates.view", "candidates.manage"),
):
    """Install overrides + patch verify_access_token; return (headers, restore)."""
    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )

    ctx = _user_ctx(user, is_super=is_super, permissions=permissions)

    def _fake_verify(token: str):
        if token == _TEST_BEARER:
            return fake_payload
        return None

    async def _user_override() -> UserContext:
        return ctx

    async def _db_override():
        # The router is tenant-scoped — set the GUC so RLS policies (even
        # disabled in tests) and any future SQL checks see the right tenant.
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()

    headers = {"Authorization": f"Bearer {_TEST_BEARER}"}

    def restore():
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    return headers, restore


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_candidates_creates_candidate(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/api/candidates",
                json={"name": "Alice", "email": "alice@example.com"},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Alice"
    assert body["email"] == "alice@example.com"
    assert "id" in body


@pytest.mark.asyncio
async def test_post_candidates_forbidden_without_manage(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    headers, restore = _setup_test_context(
        db, user, tenant.id, permissions=("candidates.view",)
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/api/candidates",
                json={"name": "X", "email": "x@example.com"},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_get_candidates_list_returns_shape(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/candidates", headers=headers)
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert "total" in body
    assert "offset" in body
    assert "limit" in body


@pytest.mark.asyncio
async def test_get_candidate_detail_returns_404_for_missing(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/candidates/{uuid.uuid4()}", headers=headers)
    finally:
        restore()

    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_get_candidate_detail_returns_candidate(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Bob",
        email="bob@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/candidates/{candidate.id}", headers=headers)
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(candidate.id)
    assert body["name"] == "Bob"


@pytest.mark.asyncio
async def test_redact_pii_requires_super_admin(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id,
        name="C",
        email="c@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=False)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/api/candidates/{candidate.id}/redact-pii",
                json={"confirmation": "I understand this permanently removes PII"},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_get_candidate_assignments_empty_when_none(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Solo",
        email="solo@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(
                f"/api/candidates/{candidate.id}/assignments", headers=headers
            )
    finally:
        restore()

    assert r.status_code == 200, r.text
    assert r.json() == []


@pytest.mark.asyncio
async def test_get_candidate_assignments_lists_enriched(db: AsyncSession):
    from app.models import (
        CandidateJobAssignment,
        JobPipelineInstance,
        JobPipelineStage,
        JobPosting,
    )
    from tests.conftest import create_test_org_unit

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Assigned",
        email="assigned@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit.id,
        title="Staff Engineer",
        description_raw="R" * 60,
        created_by=user.id,
        status="draft",
    )
    db.add(job)
    await db.flush()

    pipeline = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(pipeline)
    await db.flush()
    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=pipeline.id,
        position=0,
        name="Screening",
        stage_type="ai_interview",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
    )
    db.add(stage)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        status="active",
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(
                f"/api/candidates/{candidate.id}/assignments", headers=headers
            )
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert row["id"] == str(assignment.id)
    assert row["job_posting_id"] == str(job.id)
    assert row["job_title"] == "Staff Engineer"
    assert row["current_stage_id"] == str(stage.id)
    assert row["current_stage_name"] == "Screening"
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_redact_pii_super_admin_succeeds(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id,
        name="D",
        email="d@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/api/candidates/{candidate.id}/redact-pii",
                json={"confirmation": "I understand this permanently removes PII"},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 204, r.text
    await db.refresh(candidate)
    assert candidate.name is None
    assert candidate.email is None


# ---------------------------------------------------------------------------
# Assignment creation: active-state gate + pipeline-version stamp
# ---------------------------------------------------------------------------


async def _make_job_with_pipeline(db, tenant_id, user_id, org_unit_id, *, status="active"):
    """Create a JobPosting + pipeline instance + one stage. Returns (job, pipeline)."""
    from app.models import JobPipelineInstance, JobPipelineStage, JobPosting

    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Gate Test Job",
        description_raw="X" * 60,
        created_by=user_id,
        status=status,
    )
    db.add(job)
    await db.flush()

    pipeline = JobPipelineInstance(tenant_id=tenant_id, job_posting_id=job.id)
    db.add(pipeline)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant_id,
        instance_id=pipeline.id,
        position=0,
        name="Intake",
        stage_type="intake",
        duration_minutes=30,
        difficulty="easy",
        signal_filter={},
        pass_criteria={},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()

    return job, pipeline


@pytest.mark.asyncio
async def test_create_assignment_rejects_when_job_not_active(db: AsyncSession):
    """Job must be 'active' to accept new candidate assignments — spec §7.3."""
    from tests.conftest import create_test_org_unit

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)

    # pipeline_built status — not active
    job, _pipeline = await _make_job_with_pipeline(
        db, tenant.id, user.id, org_unit.id, status="pipeline_built"
    )

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Gated",
        email="gated@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/api/candidates/{candidate.id}/assignments",
                json={"job_posting_id": str(job.id)},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 409, r.text
    detail = r.json().get("detail", {})
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "job_not_active"


@pytest.mark.asyncio
async def test_create_assignment_stamps_pipeline_version(db: AsyncSession):
    """Assignment response must carry entered_at_pipeline_version >= 1 when job is active."""
    from tests.conftest import create_test_org_unit

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)

    job, _pipeline = await _make_job_with_pipeline(
        db, tenant.id, user.id, org_unit.id, status="active"
    )

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Versioned",
        email="versioned@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/api/candidates/{candidate.id}/assignments",
                json={"job_posting_id": str(job.id)},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["entered_at_pipeline_version"] is not None
    assert body["entered_at_pipeline_version"] >= 1
