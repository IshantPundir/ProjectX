"""Stage pause/unpause endpoint tests — Task 11."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.jd.models import JobPosting
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.database import get_tenant_db
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_TEST_BEARER = "test-pause-token"

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


async def _make_job_with_pipeline(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    org_unit_id: uuid.UUID,
    user_id: uuid.UUID,
    job_status: str = "pipeline_built",
) -> JobPosting:
    """Create a job with a 3-stage pipeline: intake (pos 0) → phone_screen (pos 1) → debrief (pos 2).

    Returns the JobPosting so callers can fetch the pipeline via the HTTP API.
    """
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Pause Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for pause tests.",
        status=job_status,
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stage_defs = [
        {
            "position": 0, "name": "Intake", "stage_type": "intake",
            "duration_minutes": None, "difficulty": None,
            "signal_filter": None, "pass_criteria": None,
            "advance_behavior": "auto_advance",
        },
        {
            "position": 1, "name": "Phone Screen", "stage_type": "phone_screen",
            "duration_minutes": 30, "difficulty": "easy",
            "signal_filter": {"include_types": ["competency"]},
            "pass_criteria": {"type": "all_knockouts_pass"},
            "advance_behavior": "auto_advance",
        },
        {
            "position": 2, "name": "Debrief", "stage_type": "debrief",
            "duration_minutes": None, "difficulty": None,
            "signal_filter": None, "pass_criteria": None,
            "advance_behavior": "manual_review",
        },
    ]
    for s in stage_defs:
        db.add(
            JobPipelineStage(
                tenant_id=tenant_id,
                instance_id=instance.id,
                position=s["position"],
                name=s["name"],
                stage_type=s["stage_type"],
                duration_minutes=s["duration_minutes"],
                difficulty=s["difficulty"],
                signal_filter=s["signal_filter"],
                pass_criteria=s["pass_criteria"],
                advance_behavior=s["advance_behavior"],
            )
        )

    await db.flush()
    return job


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_intake_returns_409(db: AsyncSession):
    """POST /pipeline/stages/{id}/pause on an intake stage must return 409."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            stages = (await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)).json()["stages"]
            intake = next(s for s in stages if s["stage_type"] == "intake")
            r = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{intake['id']}/pause",
                headers=headers,
            )
            assert r.status_code == 409, r.text
            detail = r.json().get("detail", {})
            assert "stage_pause_forbidden" in (
                detail.get("code") if isinstance(detail, dict) else str(detail)
            )
    finally:
        restore()


@pytest.mark.asyncio
async def test_pause_debrief_returns_409(db: AsyncSession):
    """POST /pipeline/stages/{id}/pause on a debrief stage must return 409."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            stages = (await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)).json()["stages"]
            debrief = next(s for s in stages if s["stage_type"] == "debrief")
            r = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{debrief['id']}/pause",
                headers=headers,
            )
            assert r.status_code == 409, r.text
    finally:
        restore()


@pytest.mark.asyncio
async def test_pause_phone_screen_succeeds(db: AsyncSession):
    """POST /pipeline/stages/{id}/pause on a phone_screen stage must set paused_at."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            stages = (await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)).json()["stages"]
            middle = next(s for s in stages if s["stage_type"] == "phone_screen")
            r = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/pause",
                headers=headers,
            )
            assert r.status_code == 200, r.text
            refreshed_stages = r.json()["stages"]
            paused = next(s for s in refreshed_stages if s["id"] == middle["id"])
            assert paused["paused_at"] is not None, "paused_at must be set after pause"
    finally:
        restore()


@pytest.mark.asyncio
async def test_unpause_clears_paused_at(db: AsyncSession):
    """POST /pipeline/stages/{id}/unpause must clear paused_at."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            stages = (await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)).json()["stages"]
            middle = next(s for s in stages if s["stage_type"] == "phone_screen")

            # First pause
            r_pause = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/pause",
                headers=headers,
            )
            assert r_pause.status_code == 200, r_pause.text

            # Then unpause
            r_unpause = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/unpause",
                headers=headers,
            )
            assert r_unpause.status_code == 200, r_unpause.text
            refreshed_stages = r_unpause.json()["stages"]
            revived = next(s for s in refreshed_stages if s["id"] == middle["id"])
            assert revived["paused_at"] is None, "paused_at must be None after unpause"
    finally:
        restore()


@pytest.mark.asyncio
async def test_pause_bumps_pipeline_version(db: AsyncSession):
    """Pausing a stage must increment pipeline_version by 1."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            assert r0.status_code == 200, r0.text
            v0 = r0.json()["pipeline_version"]
            stages = r0.json()["stages"]
            middle = next(s for s in stages if s["stage_type"] == "phone_screen")

            r1 = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/pause",
                headers=headers,
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["pipeline_version"] == v0 + 1, (
                f"Expected version {v0 + 1}, got {r1.json()['pipeline_version']}"
            )
    finally:
        restore()
