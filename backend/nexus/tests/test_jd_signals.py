"""End-to-end router integration tests for Phase 2B signal editing,
confirmation, and re-enrichment endpoints.

Auth pattern: identical to test_jd_router.py — dependency overrides
+ verify_access_token patch. See that file for the rationale.

Covers:
- PATCH /api/jobs/{id}/signals — save recruiter edits (new snapshot version)
- PATCH /api/jobs/{id}/signals — editing after confirm clears confirmation
- POST /api/jobs/{id}/signals/confirm — transitions to signals_confirmed
- POST /api/jobs/{id}/signals/confirm — rejects non-extracted state (409)
- POST /api/jobs/{id}/enrich — triggers re-enrichment (202)
- POST /api/jobs/{id}/enrich — rejects when already streaming (409)
"""

import uuid
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import JobPosting, JobPostingSignalSnapshot
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
# Sample signal payloads
# ---------------------------------------------------------------------------

def _save_signals_body(**overrides) -> dict:
    """Return a valid SaveSignalsRequest body with sensible defaults."""
    base = {
        "signals": [
            {"value": "Python", "type": "competency", "priority": "required", "weight": 2, "knockout": False, "stage": "interview", "source": "ai_extracted", "inference_basis": None},
            {"value": "FastAPI", "type": "competency", "priority": "required", "weight": 2, "knockout": False, "stage": "interview", "source": "ai_extracted", "inference_basis": None},
            {"value": "Docker", "type": "competency", "priority": "preferred", "weight": 1, "knockout": False, "stage": "interview", "source": "ai_extracted", "inference_basis": None},
            {"value": "5+ years backend", "type": "experience", "priority": "required", "weight": 2, "knockout": True, "stage": "screen", "source": "ai_extracted", "inference_basis": None},
            {"value": "Kubernetes", "type": "competency", "priority": "preferred", "weight": 1, "knockout": False, "stage": "interview", "source": "recruiter", "inference_basis": None},
        ],
        "seniority_level": "senior",
        "role_summary": "A senior backend engineer owning the platform end-to-end.",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Dependency override + middleware patch helpers
# (identical pattern to test_jd_router.py)
# ---------------------------------------------------------------------------

def _setup_test_context(
    db: AsyncSession,
    user,
    tenant_id: uuid.UUID,
    is_super_admin: bool = False,
):
    """Install all overrides needed for a test request.

    Returns (headers, restore_fn).
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
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    verify_patch = patch("app.middleware.auth.verify_access_token", side_effect=_fake_verify)
    verify_patch.start()

    headers = {"Authorization": f"Bearer {_TEST_BEARER}"}

    def restore():
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    return headers, restore


# ---------------------------------------------------------------------------
# Helpers — create a job + v1 snapshot directly in the DB
# ---------------------------------------------------------------------------

async def _make_job_with_snapshot(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_unit_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: str = "signals_extracted",
    enrichment_status: str = "idle",
    confirmed: bool = False,
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    """Insert a job and its v1 snapshot directly — bypasses the actor."""
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched job description content for testing purposes.",
        status=status,
        enrichment_status=enrichment_status,
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {"value": "Python", "type": "competency", "priority": "required", "weight": 2, "knockout": False, "stage": "interview", "source": "ai_extracted", "inference_basis": None},
            {"value": "5+ years backend", "type": "experience", "priority": "required", "weight": 2, "knockout": True, "stage": "screen", "source": "ai_extracted", "inference_basis": None},
            {"value": "CS degree", "type": "credential", "priority": "preferred", "weight": 1, "knockout": False, "stage": "screen", "source": "ai_extracted", "inference_basis": None},
            {"value": "System Design", "type": "competency", "priority": "required", "weight": 3, "knockout": False, "stage": "interview", "source": "ai_inferred", "inference_basis": "Senior role implies architectural ownership"},
            {"value": "Mentoring", "type": "behavioral", "priority": "preferred", "weight": 1, "knockout": False, "stage": "interview", "source": "ai_inferred", "inference_basis": "Senior role at growth-stage company"},
        ],
        seniority_level="senior",
        role_summary="A senior backend engineer at a fintech startup.",
        confirmed_by=user_id if confirmed else None,
        confirmed_at=sqlalchemy.func.now() if confirmed else None,
    )
    db.add(snapshot)
    await db.flush()

    return job, snapshot


# ---------------------------------------------------------------------------
# Test: PATCH /api/jobs/{id}/signals — creates a new snapshot version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_signals_creates_new_snapshot(db: AsyncSession, monkeypatch):
    """PATCH /api/jobs/{id}/signals on a signals_extracted job with a v1
    snapshot creates a v2 snapshot with the recruiter's edited data."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job, _snap = await _make_job_with_snapshot(
        db, tenant.id, company.id, user.id, status="signals_extracted",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            body = _save_signals_body()
            response = await ac.patch(
                f"/api/jobs/{job.id}/signals",
                json=body,
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["version"] == 2
    assert len(data["signals"]) == 5
    assert data["signals"][0]["value"] == "Python"
    assert data["signals"][0]["source"] == "ai_extracted"
    assert data["seniority_level"] == "senior"
    assert data["role_summary"] == body["role_summary"]


# ---------------------------------------------------------------------------
# Test: PATCH /api/jobs/{id}/signals — editing after confirm clears it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_signals_clears_confirmation(db: AsyncSession, monkeypatch):
    """Editing signals after confirmation transitions job back to
    signals_extracted and the new snapshot has no confirmed_by/at."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job, _snap = await _make_job_with_snapshot(
        db, tenant.id, company.id, user.id,
        status="signals_confirmed",
        confirmed=True,
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # PATCH signals
            patch_resp = await ac.patch(
                f"/api/jobs/{job.id}/signals",
                json=_save_signals_body(),
                headers=headers,
            )
            assert patch_resp.status_code == 200, patch_resp.text

            # GET the job and verify is_confirmed is now false
            get_resp = await ac.get(f"/api/jobs/{job.id}", headers=headers)
            assert get_resp.status_code == 200, get_resp.text
    finally:
        restore()

    job_data = get_resp.json()
    assert job_data["status"] == "signals_extracted"
    assert job_data["is_confirmed"] is False


# ---------------------------------------------------------------------------
# Test: POST /api/jobs/{id}/signals/confirm — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_signals(db: AsyncSession, monkeypatch):
    """POST /api/jobs/{id}/signals/confirm transitions job to signals_confirmed."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job, _snap = await _make_job_with_snapshot(
        db, tenant.id, company.id, user.id, status="signals_extracted",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/jobs/{job.id}/signals/confirm",
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "signals_confirmed"


# ---------------------------------------------------------------------------
# Test: POST /api/jobs/{id}/signals/confirm — wrong state (409)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_requires_extracted_state(db: AsyncSession, monkeypatch):
    """POST /api/jobs/{id}/signals/confirm on a signals_extraction_failed job
    returns 409 Conflict."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    # Insert job directly in signals_extraction_failed state
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Failed Job",
        description_raw="A" * 200,
        status="signals_extraction_failed",
        status_error="Some extraction error",
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
            response = await ac.post(
                f"/api/jobs/{job.id}/signals/confirm",
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Test: POST /api/jobs/{id}/enrich — returns 202
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_returns_202(db: AsyncSession, monkeypatch):
    """POST /api/jobs/{id}/enrich on a signals_extracted job returns 202 and
    sets enrichment_status to streaming."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.modules.jd.actors.reenrich_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job, _snap = await _make_job_with_snapshot(
        db, tenant.id, company.id, user.id, status="signals_extracted",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/jobs/{job.id}/enrich",
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 202, response.text
    data = response.json()
    assert data["status"] == "accepted"


# ---------------------------------------------------------------------------
# Test: POST /api/jobs/{id}/enrich — rejects when already streaming (409)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_rejects_while_streaming(db: AsyncSession, monkeypatch):
    """POST /api/jobs/{id}/enrich when enrichment_status is already 'streaming'
    returns 409 Conflict."""
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.modules.jd.actors.reenrich_jd.send",
        lambda *a, **k: None,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job, _snap = await _make_job_with_snapshot(
        db, tenant.id, company.id, user.id,
        status="signals_extracted",
        enrichment_status="streaming",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/jobs/{job.id}/enrich",
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 409, response.text
