"""End-to-end router integration tests for the pipelines module.

Auth pattern: identical to test_jd_signals.py — dependency overrides plus
verify_access_token patch. See that file for the rationale.

Covers:
- GET /api/pipeline-templates/starter-pack
- GET/POST /api/org-units/{unit_id}/pipeline-templates
- POST /api/pipeline-templates/{id}/set-default
- DELETE /api/pipeline-templates/{id}
- GET/POST/PATCH /api/jobs/{id}/pipeline
- POST /api/jobs/{id}/pipeline/reset
"""

import uuid
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
    PipelineTemplate,
    PipelineTemplateStage,
)
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

_TEST_BEARER = "test-integration-token"


# ---------------------------------------------------------------------------
# Auth + db override helpers (mirrors test_jd_signals.py)
# ---------------------------------------------------------------------------


def _setup_test_context(
    db: AsyncSession,
    user,
    tenant_id: uuid.UUID,
    is_super_admin: bool = False,
):
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
# Sample request bodies
# ---------------------------------------------------------------------------


def _stage_dict(position: int = 0, name: str = "Phone Screen", stage_type: str = "phone_screen") -> dict:
    return {
        "position": position,
        "name": name,
        "stage_type": stage_type,
        "duration_minutes": 10,
        "difficulty": "easy",
        "signal_filter": {
            "include_types": ["competency", "experience", "credential", "behavioral"],
        },
        "pass_criteria": {"type": "all_knockouts_pass"},
        "advance_behavior": "auto_advance",
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _setup_org(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    return tenant, user, company


async def _make_confirmed_job(
    db, tenant_id, org_unit_id, user_id, *, status: str = "signals_confirmed"
) -> JobPosting:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched test description.",
        status=status,
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()
    return job


# ---------------------------------------------------------------------------
# Starter pack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_starter_pack_returns_six_templates(db: AsyncSession):
    tenant, user, _company = await _setup_org(db)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                "/api/pipeline-templates/starter-pack", headers=headers
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 6
    keys = {item["key"] for item in data}
    assert "standard_technical" in keys


# ---------------------------------------------------------------------------
# List templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_templates_empty_library(db: AsyncSession):
    tenant, user, company = await _setup_org(db)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                f"/api/org-units/{company.id}/pipeline-templates", headers=headers
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    assert response.json() == []


# ---------------------------------------------------------------------------
# Create from starter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template_from_starter_returns_201_with_stages(db: AsyncSession):
    tenant, user, company = await _setup_org(db)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/org-units/{company.id}/pipeline-templates",
                json={
                    "source": "starter",
                    "starter_key": "standard_technical",
                    "name": "Our Standard",
                    "description": None,
                    "is_default": False,
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "Our Standard"
    assert data["from_starter"] == "standard_technical"
    assert len(data["stages"]) == 3


# ---------------------------------------------------------------------------
# Create from scratch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template_from_scratch_returns_201(db: AsyncSession):
    tenant, user, company = await _setup_org(db)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/org-units/{company.id}/pipeline-templates",
                json={
                    "source": "scratch",
                    "name": "From Scratch",
                    "description": "A scratch template",
                    "is_default": False,
                    "stages": [
                        _stage_dict(0, "Phone Screen", "phone_screen"),
                        _stage_dict(1, "AI Interview", "ai_screening"),
                    ],
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "From Scratch"
    assert data["from_starter"] is None
    assert len(data["stages"]) == 2


# ---------------------------------------------------------------------------
# Validation: non-sequential positions → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template_rejects_non_sequential_positions(db: AsyncSession):
    tenant, user, company = await _setup_org(db)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/org-units/{company.id}/pipeline-templates",
                json={
                    "source": "scratch",
                    "name": "Bad",
                    "is_default": False,
                    "stages": [
                        _stage_dict(0, "S0", "phone_screen"),
                        _stage_dict(2, "S2", "ai_screening"),  # gap
                    ],
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Set default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_default_clears_previous_default(db: AsyncSession):
    tenant, user, company = await _setup_org(db)

    a = PipelineTemplate(
        tenant_id=tenant.id, org_unit_id=company.id, name="A",
        is_default=True, created_by=user.id,
    )
    b = PipelineTemplate(
        tenant_id=tenant.id, org_unit_id=company.id, name="B",
        is_default=False, created_by=user.id,
    )
    db.add(a)
    db.add(b)
    await db.flush()
    db.add(PipelineTemplateStage(
        tenant_id=tenant.id, template_id=a.id, position=0, name="x",
        stage_type="phone_screen", duration_minutes=10, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    db.add(PipelineTemplateStage(
        tenant_id=tenant.id, template_id=b.id, position=0, name="y",
        stage_type="phone_screen", duration_minutes=10, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/pipeline-templates/{b.id}/set-default", headers=headers
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    assert response.json()["is_default"] is True

    await db.refresh(a)
    assert a.is_default is False


# ---------------------------------------------------------------------------
# Delete default → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_default_returns_409(db: AsyncSession):
    tenant, user, company = await _setup_org(db)

    tpl = PipelineTemplate(
        tenant_id=tenant.id, org_unit_id=company.id, name="Default",
        is_default=True, created_by=user.id,
    )
    db.add(tpl)
    await db.flush()
    db.add(PipelineTemplateStage(
        tenant_id=tenant.id, template_id=tpl.id, position=0, name="x",
        stage_type="phone_screen", duration_minutes=10, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.delete(
                f"/api/pipeline-templates/{tpl.id}", headers=headers
            )
    finally:
        restore()

    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Delete non-default → 204
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_non_default_returns_204(db: AsyncSession):
    tenant, user, company = await _setup_org(db)

    tpl = PipelineTemplate(
        tenant_id=tenant.id, org_unit_id=company.id, name="Disposable",
        is_default=False, created_by=user.id,
    )
    db.add(tpl)
    await db.flush()
    db.add(PipelineTemplateStage(
        tenant_id=tenant.id, template_id=tpl.id, position=0, name="x",
        stage_type="phone_screen", duration_minutes=10, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.delete(
                f"/api/pipeline-templates/{tpl.id}", headers=headers
            )
    finally:
        restore()

    assert response.status_code == 204, response.text


# ---------------------------------------------------------------------------
# GET job pipeline → 404 when none
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_pipeline_returns_404_when_none(db: AsyncSession):
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                f"/api/jobs/{job.id}/pipeline", headers=headers
            )
    finally:
        restore()

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Create job pipeline rejects non-confirmed → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_pipeline_from_starter_rejects_non_confirmed_returns_409(
    db: AsyncSession,
):
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(
        db, tenant.id, company.id, user.id, status="signals_extracted"
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/jobs/{job.id}/pipeline",
                json={"source": "starter", "starter_key": "standard_technical"},
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Update job pipeline replaces stages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_pipeline_replaces_stages(db: AsyncSession):
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()
    db.add(JobPipelineStage(
        tenant_id=tenant.id, instance_id=instance.id, position=0, name="OldA",
        stage_type="phone_screen", duration_minutes=10, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    db.add(JobPipelineStage(
        tenant_id=tenant.id, instance_id=instance.id, position=1, name="OldB",
        stage_type="ai_screening", duration_minutes=30, difficulty="medium",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "score_threshold", "threshold": 70},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json={
                    "stages": [
                        _stage_dict(0, "NewOnly", "phone_screen"),
                    ],
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["stages"]) == 1
    assert data["stages"][0]["name"] == "NewOnly"


# ---------------------------------------------------------------------------
# Reset returns 409 when built from scratch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_returns_409_when_built_from_scratch(db: AsyncSession):
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()
    db.add(JobPipelineStage(
        tenant_id=tenant.id, instance_id=instance.id, position=0, name="A",
        stage_type="phone_screen", duration_minutes=10, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/jobs/{job.id}/pipeline/reset", headers=headers
            )
    finally:
        restore()

    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Swap job pipeline — scratch → starter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swap_job_pipeline_replaces_instance(db: AsyncSession):
    """POST /swap atomically replaces an existing pipeline instance.

    Scenario: pipeline was created from scratch; swap to a starter pack.
    Asserts: response has new stages (from starter), no source_template_id,
    and the old instance id is gone (new id differs).
    """
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    # Create an initial pipeline from scratch so we have something to swap
    initial_instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(initial_instance)
    await db.flush()
    db.add(JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=initial_instance.id,
        position=0,
        name="OldStage",
        stage_type="phone_screen",
        duration_minutes=10,
        difficulty="easy",
        signal_filter={
            "include_types": ["competency"],
        },
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    old_instance_id = initial_instance.id

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/jobs/{job.id}/pipeline/swap",
                json={"source": "starter", "starter_key": "standard_technical"},
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    # New instance has a different id
    assert data["id"] != str(old_instance_id)
    # Came from a starter — no source_template_id
    assert data["source_template_id"] is None
    # standard_technical starter has 3 middle stages; bookend seeding adds intake + debrief = 5
    assert len(data["stages"]) == 5
    assert data["stages"][0]["stage_type"] == "intake"
    assert data["stages"][-1]["stage_type"] == "debrief"
    stage_names = [s["name"] for s in data["stages"]]
    assert "OldStage" not in stage_names


# ---------------------------------------------------------------------------
# Picker: POST /jobs/{id}/pipeline → state transition tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_pipeline_starter_transitions_job_to_pipeline_built(db: AsyncSession):
    """Picker starter path: creates instance + transitions job to pipeline_built."""
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline",
                json={"source": "starter", "starter_key": "standard_technical"},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            # Reload job and verify status
            job_resp = await ac.get(f"/api/jobs/{job.id}", headers=headers)
    finally:
        restore()

    assert job_resp.status_code == 200, job_resp.text
    assert job_resp.json()["status"] == "pipeline_built"


@pytest.mark.asyncio
async def test_post_pipeline_scratch_with_intake_debrief_only(db: AsyncSession):
    """Blank pipeline path: creates intake + debrief, transitions to pipeline_built.

    This is the primary I1 regression test — intake/debrief have FORBIDDEN
    fields (signal_filter, duration_minutes, difficulty) that the validator
    sets to None. The service layer must guard .model_dump() calls against None
    or this test will crash with AttributeError.
    """
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline",
                json={
                    "source": "scratch",
                    "stages": [
                        {"position": 0, "name": "Intake", "stage_type": "intake"},
                        {"position": 1, "name": "Debrief", "stage_type": "debrief"},
                    ],
                },
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            # Reload job and verify status
            job_resp = await ac.get(f"/api/jobs/{job.id}", headers=headers)
    finally:
        restore()

    assert len(body["stages"]) == 2
    assert body["stages"][0]["stage_type"] == "intake"
    assert body["stages"][1]["stage_type"] == "debrief"
    assert job_resp.status_code == 200, job_resp.text
    assert job_resp.json()["status"] == "pipeline_built"


@pytest.mark.asyncio
async def test_post_pipeline_template_transitions(db: AsyncSession):
    """Picker template path: creates instance + transitions job to pipeline_built."""
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    template = PipelineTemplate(
        tenant_id=tenant.id, org_unit_id=company.id, name="Tpl",
        is_default=False, created_by=user.id,
    )
    db.add(template)
    await db.flush()
    db.add(PipelineTemplateStage(
        tenant_id=tenant.id, template_id=template.id, position=0, name="Phone",
        stage_type="phone_screen", duration_minutes=15, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline",
                json={"source": "template", "template_id": str(template.id)},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            job_resp = await ac.get(f"/api/jobs/{job.id}", headers=headers)
    finally:
        restore()

    assert job_resp.status_code == 200, job_resp.text
    assert job_resp.json()["status"] == "pipeline_built"


@pytest.mark.asyncio
async def test_post_pipeline_when_already_exists_returns_409(db: AsyncSession):
    """Duplicate creation returns 409 — no state transition attempted."""
    tenant, user, company = await _setup_org(db)
    job = await _make_confirmed_job(db, tenant.id, company.id, user.id)

    # Pre-create a pipeline instance so the second POST hits PipelineAlreadyExistsError
    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()
    db.add(JobPipelineStage(
        tenant_id=tenant.id, instance_id=instance.id, position=0, name="Existing",
        stage_type="phone_screen", duration_minutes=10, difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline",
                json={"source": "starter", "starter_key": "standard_technical"},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 409, resp.text
