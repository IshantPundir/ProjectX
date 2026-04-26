"""End-to-end happy-path integration tests for Phase 3B candidates.

Exercises the HTTP surface — does NOT call service functions directly —
so this test catches wiring regressions (router prefixes, exception
handlers, dependency overrides) that unit tests miss.

Auth plumbing mirrors tests/test_candidates_router.py verbatim:
  1. Patch `app.middleware.auth.verify_access_token` to accept a sentinel
     bearer token so the AuthMiddleware passes.
  2. Override `get_current_user_roles` to return a prebuilt UserContext.
  3. Override `get_tenant_db` to yield the test's own `db` session so the
     rows the test flushes are visible to router code.

These tests run as super-admin (`is_super=True`) which short-circuits the
ancestry-walking authz in `require_candidate_access` / `require_job_access`.
The goal here is wiring, not authz — authz is covered by test_candidates_authz.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.main import app
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
)
from app.modules.auth.context import RoleAssignment, UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

_TEST_BEARER = "test-integration-token"


# ---------------------------------------------------------------------------
# Dependency override helpers — duplicated from test_candidates_router.py
# (repo convention: duplication over cross-test imports)
# ---------------------------------------------------------------------------


def _user_ctx(
    user,
    *,
    is_super: bool = False,
    permissions: tuple[str, ...] = (
        "candidates.view",
        "candidates.manage",
        "jobs.view",
        "jobs.manage",
    ),
) -> UserContext:
    assignments: list[RoleAssignment] = []
    if permissions:
        assignments.append(
            RoleAssignment(
                org_unit_id=uuid.uuid4(),
                org_unit_name="Root",
                role_id=uuid.uuid4(),
                role_name="Admin",
                permissions=list(permissions),
            )
        )
    return UserContext(
        user=user,
        is_super_admin=is_super,
        assignments=assignments,
    )


def _setup_test_context(
    db: AsyncSession,
    user,
    tenant_id: uuid.UUID,
    *,
    is_super: bool = True,
    permissions: tuple[str, ...] = (
        "candidates.view",
        "candidates.manage",
        "jobs.view",
        "jobs.manage",
    ),
):
    """Install overrides + patch verify_access_token; return (headers, restore).

    Matches the shape of tests/test_candidates_router.py::_setup_test_context.
    """
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
# Fixture helpers — inline, not pytest fixtures (matches repo convention)
# ---------------------------------------------------------------------------


async def _make_job_with_stages(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    stage_names: tuple[str, ...] = ("Screening", "Interview", "Offer"),
) -> tuple[JobPosting, list[JobPipelineStage]]:
    """Create a JD with a pipeline instance and the given ordered stages."""
    org_unit = await create_test_org_unit(db, tenant_id)
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit.id,
        title="Engineer",
        description_raw="R" * 60,
        created_by=user_id,
        status="active",
    )
    db.add(job)
    await db.flush()
    instance = JobPipelineInstance(tenant_id=tenant_id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()
    stages: list[JobPipelineStage] = []
    for i, name in enumerate(stage_names):
        s = JobPipelineStage(
            tenant_id=tenant_id,
            instance_id=instance.id,
            position=i,
            name=name,
            stage_type="ai_interview",
            duration_minutes=30,
            difficulty="medium",
            signal_filter={},
            pass_criteria={},
            advance_behavior="manual",
        )
        db.add(s)
        stages.append(s)
    await db.flush()
    return job, stages


# ---------------------------------------------------------------------------
# Test 1 — end-to-end candidate flow:
#   create → assign → kanban → update → archive → kanban excludes archived
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_end_to_end_happy_path(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    job, stages = await _make_job_with_stages(db, tenant.id, user.id)

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # 1. Create the candidate.
            r = await ac.post(
                "/api/candidates",
                json={"name": "Ada Lovelace", "email": "ada@example.com"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            created = r.json()
            assert created["name"] == "Ada Lovelace"
            assert "id" in created
            candidate_id = created["id"]

            # 2. Assign to the job (defaults to first stage).
            r = await ac.post(
                f"/api/candidates/{candidate_id}/assignments",
                json={"job_posting_id": str(job.id)},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            assignment = r.json()
            assert assignment["candidate_id"] == candidate_id
            assert assignment["job_posting_id"] == str(job.id)
            assert assignment["current_stage_id"] == str(stages[0].id)
            assert assignment["current_stage_name"] == stages[0].name
            assert assignment["job_title"] == job.title
            assignment_id = assignment["id"]

            # 3. Kanban shows the candidate in the first stage's column.
            r = await ac.get(
                f"/api/jobs/{job.id}/candidates/kanban", headers=headers
            )
            assert r.status_code == 200, r.text
            board = r.json()
            assert board["job_posting_id"] == str(job.id)
            first_column = next(
                (col for col in board["stages"] if col["stage_id"] == str(stages[0].id)),
                None,
            )
            assert first_column is not None
            card_ids = [c["candidate_id"] for c in first_column["candidates"]]
            assert candidate_id in card_ids

            # 4. Patch the candidate — set current_title.
            r = await ac.patch(
                f"/api/candidates/{candidate_id}",
                json={"current_title": "Senior Engineer"},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json()["current_title"] == "Senior Engineer"

            # 5. Archive the assignment.
            r = await ac.patch(
                f"/api/candidates/{candidate_id}/assignments/{assignment_id}",
                json={"status": "archived"},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "archived"

            # 6. Kanban no longer lists the archived candidate.
            r = await ac.get(
                f"/api/jobs/{job.id}/candidates/kanban", headers=headers
            )
            assert r.status_code == 200, r.text
            board = r.json()
            for col in board["stages"]:
                ids = [c["candidate_id"] for c in col["candidates"]]
                assert candidate_id not in ids, (
                    f"Archived candidate still present in stage {col['stage_name']}"
                )
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2 — resume upload flow with mocked S3.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_resume_upload_flow(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Seed a candidate via HTTP.
            r = await ac.post(
                "/api/candidates",
                json={"name": "Bob", "email": "bob@example.com"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            candidate_id = r.json()["id"]

            fake_client = MagicMock()
            fake_client.generate_presigned_url.return_value = (
                "https://s3.example.com/signed"
            )
            fake_client.head_object.return_value = {"ContentType": "application/pdf"}

            with patch(
                "app.modules.candidates.resume_service._s3_client",
                return_value=fake_client,
            ):
                # 1. Request pre-signed upload URL.
                r = await ac.post(
                    f"/api/candidates/{candidate_id}/resume", headers=headers
                )
                assert r.status_code == 200, r.text
                upload = r.json()
                assert upload["upload_url"] == "https://s3.example.com/signed"
                assert "s3_key" in upload
                assert upload["expires_in_seconds"] > 0
                s3_key = upload["s3_key"]

                # 2. Confirm the upload — HEAD returns application/pdf.
                r = await ac.post(
                    f"/api/candidates/{candidate_id}/resume/confirm",
                    json={"s3_key": s3_key},
                    headers=headers,
                )
                assert r.status_code == 204, r.text

                # 3. Verify the DB state.
                from app.models import Candidate

                cand = (
                    await db.execute(
                        sqlalchemy.select(Candidate).where(
                            Candidate.id == uuid.UUID(candidate_id)
                        )
                    )
                ).scalar_one()
                await db.refresh(cand)
                assert cand.resume_s3_key == s3_key
                assert cand.resume_uploaded_at is not None

                # 4. Delete the resume.
                r = await ac.delete(
                    f"/api/candidates/{candidate_id}/resume", headers=headers
                )
                assert r.status_code == 204, r.text

                # 5. Columns cleared.
                await db.refresh(cand)
                assert cand.resume_s3_key is None
                assert cand.resume_uploaded_at is None
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3 — GDPR redaction flow (super-admin-only endpoint).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_redact_pii_flow(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    headers, restore = _setup_test_context(db, user, tenant.id, is_super=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Create a candidate.
            r = await ac.post(
                "/api/candidates",
                json={
                    "name": "Carol",
                    "email": "carol@example.com",
                    "phone": "+1-555-0199",
                    "location": "NYC",
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text
            candidate_id = r.json()["id"]

            # Redact PII.
            r = await ac.post(
                f"/api/candidates/{candidate_id}/redact-pii",
                json={"confirmation": "I understand this permanently removes PII"},
                headers=headers,
            )
            assert r.status_code == 204, r.text

            # Verify columns nulled and pii_redacted_at is stamped.
            from app.models import Candidate

            cand = (
                await db.execute(
                    sqlalchemy.select(Candidate).where(
                        Candidate.id == uuid.UUID(candidate_id)
                    )
                )
            ).scalar_one()
            await db.refresh(cand)
            assert cand.name is None
            assert cand.email is None
            assert cand.phone is None
            assert cand.location is None
            assert cand.pii_redacted_at is not None
    finally:
        restore()
