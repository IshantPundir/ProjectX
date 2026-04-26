"""pipeline_version bumps on every save path — Task 7.

Tests prove:
  - PATCH /api/jobs/{id}/pipeline bumps pipeline_version by 1
  - POST /api/jobs/{id}/pipeline/reset bumps pipeline_version by 1
  - PATCH /api/jobs/{id}/signals does NOT affect pipeline_version
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.database import get_tenant_db
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_TEST_BEARER = "test-versioning-token"

_VALID_PROFILE = {
    "about": "We build enterprise recruiting tools at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end.",
}


# ---------------------------------------------------------------------------
# Auth + DB override helpers
# ---------------------------------------------------------------------------


def _setup_test_context(
    db: AsyncSession,
    user,
    tenant_id: uuid.UUID,
    is_super_admin: bool = True,
):
    """Install fake auth + DB overrides. Returns (headers, restore_fn)."""
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
# DB helpers
# ---------------------------------------------------------------------------


async def _setup_tenant(db: AsyncSession):
    """Create tenant + super-admin user + company org unit."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    return tenant, user, company


async def _make_job(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_unit_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: str = "signals_confirmed",
) -> JobPosting:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Versioning Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for versioning tests.",
        status=status,
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()
    return job


async def _make_snapshot(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
) -> JobPostingSignalSnapshot:
    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job_id,
        version=1,
        signals=[
            {
                "value": "Python",
                "type": "competency",
                "priority": "required",
                "weight": 2,
                "knockout": False,
                "stage": "interview",
                "source": "ai_extracted",
                "inference_basis": None,
            }
        ],
        seniority_level="senior",
        role_summary="A senior engineer.",
        prompt_version="v1",
        confirmed_by=user_id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def _make_pipeline_with_stage(
    db: AsyncSession,
    job: JobPosting,
    *,
    source_template_id: uuid.UUID | None = None,
) -> tuple[JobPipelineInstance, JobPipelineStage]:
    """Create a pipeline instance + one phone_screen stage."""
    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=source_template_id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=job.tenant_id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=15,
        difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()
    return instance, stage


def _patch_body_from_stage(stage: JobPipelineStage, name: str | None = None) -> dict:
    """Build a minimal UpdateJobPipelineRequest body for a single existing stage."""
    return {
        "stages": [
            {
                "id": str(stage.id),
                "position": stage.position,
                "name": name or stage.name,
                "stage_type": stage.stage_type,
                "duration_minutes": stage.duration_minutes,
                "difficulty": stage.difficulty,
                "signal_filter": stage.signal_filter,
                "pass_criteria": stage.pass_criteria,
                "advance_behavior": stage.advance_behavior,
            }
        ]
    }


# ---------------------------------------------------------------------------
# Test 1 — PATCH /pipeline bumps pipeline_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_pipeline_bumps_version(db: AsyncSession):
    """PATCH /api/jobs/{id}/pipeline must increment pipeline_version by 1."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant.id, company.id, user.id)
    instance, stage = await _make_pipeline_with_stage(db, job)
    await db.commit()

    # Capture the initial version via GET
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            assert r0.status_code == 200, r0.text
            v0 = r0.json()["pipeline_version"]

            # PATCH — rename the stage (trivial but triggers a write)
            body = _patch_body_from_stage(stage, name="Phone Screen Renamed")
            r1 = await ac.patch(
                f"/api/jobs/{job.id}/pipeline", json=body, headers=headers
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["pipeline_version"] == v0 + 1, (
                f"Expected version {v0 + 1}, got {r1.json()['pipeline_version']}"
            )
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2 — Two consecutive PATCHes produce v0+1, v0+2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_patches_bump_version_twice(db: AsyncSession):
    """Each PATCH call increments pipeline_version once, monotonically."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant.id, company.id, user.id)
    instance, stage = await _make_pipeline_with_stage(db, job)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            v0 = r0.json()["pipeline_version"]

            body1 = _patch_body_from_stage(stage, name="Patch One")
            r1 = await ac.patch(
                f"/api/jobs/{job.id}/pipeline", json=body1, headers=headers
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["pipeline_version"] == v0 + 1

            # Need a fresh stage id from the reload for the second patch
            stage_data = r1.json()["stages"][0]
            body2 = {
                "stages": [
                    {
                        "id": stage_data["id"],
                        "position": stage_data["position"],
                        "name": "Patch Two",
                        "stage_type": stage_data["stage_type"],
                        "duration_minutes": stage_data["duration_minutes"],
                        "difficulty": stage_data["difficulty"],
                        "signal_filter": stage_data["signal_filter"],
                        "pass_criteria": stage_data["pass_criteria"],
                        "advance_behavior": stage_data["advance_behavior"],
                    }
                ]
            }
            r2 = await ac.patch(
                f"/api/jobs/{job.id}/pipeline", json=body2, headers=headers
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["pipeline_version"] == v0 + 2
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3 — Reset to source bumps pipeline_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_to_source_bumps_version(db: AsyncSession):
    """POST /api/jobs/{id}/pipeline/reset must also increment pipeline_version."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant.id, company.id, user.id)

    # Create a template so the instance has a source_template_id
    template = PipelineTemplate(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        name="Test Template",
        is_default=False,
        created_by=user.id,
    )
    db.add(template)
    await db.flush()
    db.add(PipelineTemplateStage(
        tenant_id=tenant.id,
        template_id=template.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=15,
        difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.flush()

    instance, stage = await _make_pipeline_with_stage(
        db, job, source_template_id=template.id
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            assert r0.status_code == 200, r0.text
            v0 = r0.json()["pipeline_version"]

            r1 = await ac.post(
                f"/api/jobs/{job.id}/pipeline/reset", headers=headers
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["pipeline_version"] == v0 + 1, (
                f"Expected reset to bump version to {v0 + 1}, got {r1.json()['pipeline_version']}"
            )
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 4 — Signal edit does NOT bump pipeline_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_edit_does_not_bump_pipeline_version(db: AsyncSession, monkeypatch):
    """PATCH /api/jobs/{id}/signals must not affect pipeline_version.

    Signal snapshots have their own version counter; pipeline_version is
    only incremented by stage-level and participant-level pipeline writes.
    """
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant, user, company = await _setup_tenant(db)
    # Job must be in signals_extracted state for PATCH /signals to accept it
    job = await _make_job(
        db, tenant.id, company.id, user.id, status="signals_extracted"
    )
    await _make_snapshot(db, tenant.id, job.id, user.id)
    instance, stage = await _make_pipeline_with_stage(db, job)
    await db.commit()

    # Capture initial pipeline_version (the job must be in pipeline_built
    # state to pass require_instance_access → but the test DB job is
    # signals_extracted. The pipeline authz check requires job access not
    # pipeline_built status, so GET /pipeline works regardless of status).
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            assert r0.status_code == 200, r0.text
            v0 = r0.json()["pipeline_version"]

            # Edit signals — this bumps snapshot.version, not pipeline_version
            signal_body = {
                "signals": [
                    {
                        "value": "Python",
                        "type": "competency",
                        "priority": "required",
                        "weight": 2,
                        "knockout": False,
                        "stage": "interview",
                        "source": "ai_extracted",
                        "inference_basis": None,
                    }
                ],
                "seniority_level": "senior",
                "role_summary": "Edited role summary.",
            }
            rs = await ac.patch(
                f"/api/jobs/{job.id}/signals",
                json=signal_body,
                headers=headers,
            )
            assert rs.status_code == 200, rs.text

            # pipeline_version must be unchanged
            r1 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            assert r1.status_code == 200, r1.text
            assert r1.json()["pipeline_version"] == v0, (
                f"Signal edit must not bump pipeline_version: "
                f"expected {v0}, got {r1.json()['pipeline_version']}"
            )
    finally:
        restore()
