"""Activation gate predicate tests + endpoint tests — Task 12 / spec §7."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.main import app
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    PipelineStageParticipant,
    StageQuestionBank,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_TEST_BEARER = "test-activate-token"

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
    *,
    tenant_id: uuid.UUID,
    org_unit_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str = "pipeline_built",
) -> JobPosting:
    """Create a bare job in the given status."""
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Activation Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description.",
        status=status,
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()
    return job


async def _make_snapshot(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
) -> JobPostingSignalSnapshot:
    """Insert a confirmed signal snapshot for the job."""
    from datetime import datetime, UTC

    snap = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job_id,
        version=1,
        signals=[
            {
                "value": "Python",
                "type": "competency",
                "priority": "required",
                "weight": 3,
                "knockout": False,
                "stage": "screen",
                "source": "ai_extracted",
                "evaluation_method": "verbal_response",
            }
        ],
        seniority_level="mid",
        role_summary="Test role",
        confirmed_by=None,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snap)
    await db.flush()
    return snap


async def _make_pipeline(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    stage_defs: list[dict],
) -> tuple[JobPipelineInstance, list[JobPipelineStage]]:
    """Create a pipeline instance + stages from a list of stage definition dicts."""
    instance = JobPipelineInstance(
        tenant_id=tenant_id,
        job_posting_id=job_id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stages = []
    for s in stage_defs:
        stage = JobPipelineStage(
            tenant_id=tenant_id,
            instance_id=instance.id,
            position=s["position"],
            name=s["name"],
            stage_type=s["stage_type"],
            duration_minutes=s.get("duration_minutes"),
            difficulty=s.get("difficulty"),
            signal_filter=s.get("signal_filter"),
            pass_criteria=s.get("pass_criteria"),
            advance_behavior=s.get("advance_behavior", "manual_review"),
        )
        db.add(stage)
        stages.append(stage)
    await db.flush()
    return instance, stages


def _std_stage_defs() -> list[dict]:
    """Standard 3-stage pipeline: intake → phone_screen → debrief."""
    return [
        {
            "position": 0, "name": "Intake", "stage_type": "intake",
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
            "advance_behavior": "manual_review",
        },
    ]


async def _add_participant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    stage_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    assigned_by: uuid.UUID,
) -> PipelineStageParticipant:
    p = PipelineStageParticipant(
        tenant_id=tenant_id,
        stage_id=stage_id,
        user_id=user_id,
        role=role,
        assigned_by=assigned_by,
    )
    db.add(p)
    await db.flush()
    return p


async def _add_bank(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    stage_id: uuid.UUID,
    job_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    status: str = "generated",
) -> StageQuestionBank:
    bank = StageQuestionBank(
        tenant_id=tenant_id,
        stage_id=stage_id,
        job_posting_id=job_id,
        signal_snapshot_id=snapshot_id,
        status=status,
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()
    return bank


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _job_in_signals_confirmed(db: AsyncSession):
    """Job in signals_confirmed (no pipeline) — should return 409."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant_id=tenant.id, org_unit_id=company.id,
                          user_id=user.id, status="signals_confirmed")
    await db.commit()
    return job, (tenant, user)


@pytest_asyncio.fixture
async def _pipeline_built_job_no_participants(db: AsyncSession):
    """pipeline_built + intake/phone_screen/debrief + bank on phone_screen, but NO participants."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant_id=tenant.id, org_unit_id=company.id,
                          user_id=user.id, status="pipeline_built")
    snap = await _make_snapshot(db, tenant_id=tenant.id, job_id=job.id)
    _instance, stages = await _make_pipeline(db, tenant_id=tenant.id, job_id=job.id,
                                              stage_defs=_std_stage_defs())

    # Find the phone_screen stage and add a bank so the bank predicate passes.
    phone_stage = next(s for s in stages if s.stage_type == "phone_screen")
    await _add_bank(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                    job_id=job.id, snapshot_id=snap.id, status="generated")

    # Find debrief stage and add a reviewer participant so only missing_interviewer fires.
    debrief_stage = next(s for s in stages if s.stage_type == "debrief")
    await _add_participant(db, tenant_id=tenant.id, stage_id=debrief_stage.id,
                           user_id=user.id, role="reviewer", assigned_by=user.id)

    await db.commit()
    return job


@pytest_asyncio.fixture
async def _pipeline_built_job_no_reviewer(db: AsyncSession):
    """pipeline_built + phone_screen with interviewer + bank, but debrief has NO reviewer."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant_id=tenant.id, org_unit_id=company.id,
                          user_id=user.id, status="pipeline_built")
    snap = await _make_snapshot(db, tenant_id=tenant.id, job_id=job.id)
    _instance, stages = await _make_pipeline(db, tenant_id=tenant.id, job_id=job.id,
                                              stage_defs=_std_stage_defs())

    phone_stage = next(s for s in stages if s.stage_type == "phone_screen")
    await _add_bank(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                    job_id=job.id, snapshot_id=snap.id, status="generated")
    # interviewer on phone_screen
    await _add_participant(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                           user_id=user.id, role="interviewer", assigned_by=user.id)
    # No reviewer on debrief — this is the gap we're testing

    await db.commit()
    return job


@pytest_asyncio.fixture
async def _pipeline_built_intake_debrief_only(db: AsyncSession):
    """pipeline_built + intake + debrief ONLY (no middle stage)."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant_id=tenant.id, org_unit_id=company.id,
                          user_id=user.id, status="pipeline_built")
    _snap = await _make_snapshot(db, tenant_id=tenant.id, job_id=job.id)

    stage_defs = [
        {"position": 0, "name": "Intake", "stage_type": "intake",
         "advance_behavior": "auto_advance"},
        {"position": 1, "name": "Debrief", "stage_type": "debrief",
         "advance_behavior": "manual_review"},
    ]
    _instance, stages = await _make_pipeline(db, tenant_id=tenant.id, job_id=job.id,
                                              stage_defs=stage_defs)

    # Add reviewer on debrief so only no_middle_stage fires
    debrief_stage = next(s for s in stages if s.stage_type == "debrief")
    await _add_participant(db, tenant_id=tenant.id, stage_id=debrief_stage.id,
                           user_id=user.id, role="reviewer", assigned_by=user.id)

    await db.commit()
    return job


@pytest_asyncio.fixture
async def _pipeline_built_no_banks(db: AsyncSession):
    """pipeline_built + full pipeline with participants but NO banks generated."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant_id=tenant.id, org_unit_id=company.id,
                          user_id=user.id, status="pipeline_built")
    _snap = await _make_snapshot(db, tenant_id=tenant.id, job_id=job.id)
    _instance, stages = await _make_pipeline(db, tenant_id=tenant.id, job_id=job.id,
                                              stage_defs=_std_stage_defs())

    phone_stage = next(s for s in stages if s.stage_type == "phone_screen")
    debrief_stage = next(s for s in stages if s.stage_type == "debrief")

    # Participants are set up correctly — only banks are missing
    await _add_participant(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                           user_id=user.id, role="interviewer", assigned_by=user.id)
    await _add_participant(db, tenant_id=tenant.id, stage_id=debrief_stage.id,
                           user_id=user.id, role="reviewer", assigned_by=user.id)
    # No bank inserted for phone_screen

    await db.commit()
    return job


@pytest_asyncio.fixture
async def _pipeline_built_ready(db: AsyncSession):
    """All predicates pass: pipeline_built + middle stage + interviewer + reviewer + bank."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant_id=tenant.id, org_unit_id=company.id,
                          user_id=user.id, status="pipeline_built")
    snap = await _make_snapshot(db, tenant_id=tenant.id, job_id=job.id)
    _instance, stages = await _make_pipeline(db, tenant_id=tenant.id, job_id=job.id,
                                              stage_defs=_std_stage_defs())

    phone_stage = next(s for s in stages if s.stage_type == "phone_screen")
    debrief_stage = next(s for s in stages if s.stage_type == "debrief")

    await _add_participant(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                           user_id=user.id, role="interviewer", assigned_by=user.id)
    await _add_participant(db, tenant_id=tenant.id, stage_id=debrief_stage.id,
                           user_id=user.id, role="reviewer", assigned_by=user.id)
    await _add_bank(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                    job_id=job.id, snapshot_id=snap.id, status="generated")

    await db.commit()
    return job


@pytest_asyncio.fixture
async def _active_job_with_pipeline(db: AsyncSession):
    """Job already in active state — should return 409 on re-activate."""
    tenant, user, company = await _setup_tenant(db)
    job = await _make_job(db, tenant_id=tenant.id, org_unit_id=company.id,
                          user_id=user.id, status="active")
    snap = await _make_snapshot(db, tenant_id=tenant.id, job_id=job.id)
    _instance, stages = await _make_pipeline(db, tenant_id=tenant.id, job_id=job.id,
                                              stage_defs=_std_stage_defs())

    phone_stage = next(s for s in stages if s.stage_type == "phone_screen")
    debrief_stage = next(s for s in stages if s.stage_type == "debrief")

    await _add_participant(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                           user_id=user.id, role="interviewer", assigned_by=user.id)
    await _add_participant(db, tenant_id=tenant.id, stage_id=debrief_stage.id,
                           user_id=user.id, role="reviewer", assigned_by=user.id)
    await _add_bank(db, tenant_id=tenant.id, stage_id=phone_stage.id,
                    job_id=job.id, snapshot_id=snap.id, status="generated")

    await db.commit()
    return job


# ---------------------------------------------------------------------------
# Helper to build an auth_client from a db + job
# ---------------------------------------------------------------------------


async def _make_auth_client(db: AsyncSession, job: JobPosting):
    """Build an AsyncClient with auth overrides based on job's tenant."""
    from sqlalchemy import select as _select
    from app.models import User

    user_result = await db.execute(
        _select(User).where(User.tenant_id == job.tenant_id).limit(1)
    )
    user = user_result.scalar_one()
    return user, _setup_test_context(db, user, job.tenant_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cannot_activate_from_signals_confirmed(db: AsyncSession, _job_in_signals_confirmed):
    """Job must be in pipeline_built to activate."""
    job, (tenant, user) = _job_in_signals_confirmed
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/jobs/{job.id}/activate", headers=headers)
        assert r.status_code == 409, r.text
        detail = r.json().get("detail", {})
        assert "job_not_in_pipeline_built_state" in (
            detail.get("code") if isinstance(detail, dict) else str(detail)
        )
    finally:
        restore()


@pytest.mark.asyncio
async def test_activate_fails_when_human_led_has_no_interviewer(
    db: AsyncSession, _pipeline_built_job_no_participants
):
    """Phone screen / human interview need ≥1 interviewer."""
    job = _pipeline_built_job_no_participants
    user, (headers, restore) = await _make_auth_client(db, job)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/jobs/{job.id}/activate", headers=headers)
        assert r.status_code == 422, r.text
        body = r.json()["detail"]
        assert body["code"] == "activation_predicates_failed"
        failures = body["predicates_failed"]
        assert any(f["code"] == "missing_interviewer" for f in failures), failures
    finally:
        restore()


@pytest.mark.asyncio
async def test_activate_fails_when_debrief_has_no_reviewer(
    db: AsyncSession, _pipeline_built_job_no_reviewer
):
    """Debrief stage needs ≥1 reviewer."""
    job = _pipeline_built_job_no_reviewer
    user, (headers, restore) = await _make_auth_client(db, job)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/jobs/{job.id}/activate", headers=headers)
        assert r.status_code == 422, r.text
        failures = r.json()["detail"]["predicates_failed"]
        assert any(f["code"] == "missing_reviewer" for f in failures), failures
    finally:
        restore()


@pytest.mark.asyncio
async def test_activate_fails_when_no_middle_stage(
    db: AsyncSession, _pipeline_built_intake_debrief_only
):
    """Pipeline with only intake + debrief must fail no_middle_stage predicate."""
    job = _pipeline_built_intake_debrief_only
    user, (headers, restore) = await _make_auth_client(db, job)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/jobs/{job.id}/activate", headers=headers)
        assert r.status_code == 422, r.text
        failures = r.json()["detail"]["predicates_failed"]
        assert any(f["code"] == "no_middle_stage" for f in failures), failures
    finally:
        restore()


@pytest.mark.asyncio
async def test_activate_fails_when_bank_missing_for_eligible_stage(
    db: AsyncSession, _pipeline_built_no_banks
):
    """Bank-eligible stages without a generated/confirmed bank must fail."""
    job = _pipeline_built_no_banks
    user, (headers, restore) = await _make_auth_client(db, job)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/jobs/{job.id}/activate", headers=headers)
        assert r.status_code == 422, r.text
        failures = r.json()["detail"]["predicates_failed"]
        assert any(f["code"] == "missing_bank" for f in failures), failures
    finally:
        restore()


@pytest.mark.asyncio
async def test_activate_succeeds_when_checklist_passes(
    db: AsyncSession, _pipeline_built_ready
):
    """All predicates pass → job transitions to active, response status = active."""
    job = _pipeline_built_ready
    user, (headers, restore) = await _make_auth_client(db, job)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/jobs/{job.id}/activate", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "active"
            # Verify job status persisted
            job_resp = await ac.get(f"/api/jobs/{job.id}", headers=headers)
            assert job_resp.status_code == 200, job_resp.text
            assert job_resp.json()["status"] == "active"
    finally:
        restore()


@pytest.mark.asyncio
async def test_already_active_returns_409(
    db: AsyncSession, _active_job_with_pipeline
):
    """Calling activate on an already-active job must return 409."""
    job = _active_job_with_pipeline
    user, (headers, restore) = await _make_auth_client(db, job)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(f"/api/jobs/{job.id}/activate", headers=headers)
        assert r.status_code == 409, r.text
        detail = r.json().get("detail", {})
        assert "job_not_in_pipeline_built_state" in (
            detail.get("code") if isinstance(detail, dict) else str(detail)
        )
    finally:
        restore()
