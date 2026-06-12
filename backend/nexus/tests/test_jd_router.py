"""End-to-end router integration tests for the JD module.

Auth pattern: dependency overrides + verify_access_token patch.

The existing Phase 1 tests only exercise unauthenticated paths — there is
no JWT issuance helper in Phase 1. The auth middleware uses JWKS (ES256)
against a local Supabase endpoint, which is not accessible inside the
test container without a live Supabase session.

Strategy used here:
  1. Patch `app.middleware.auth.verify_access_token` to accept a synthetic
     test token and return a TokenPayload. This lets the middleware pass
     and set request.state correctly.
  2. Override `get_current_user_roles` to return a UserContext built from the
     test user (bypasses the DB lookup in the auth module).
  3. Override `get_tenant_db` to open a session on the test engine with
     RLS tenant set (bypasses the production engine).

The router, service, state machine, exception handlers, and DB are all
exercised against real Postgres — only the JWT cryptography is stubbed.

Covers — per the unified job-creation flow (docs/superpowers/specs/
2026-05-14-unified-job-creation-flow-design.md):
- POST /api/jobs lands a job in `draft`, no actor dispatch, no profile gate
- POST /api/jobs/{id}/extract-signals transitions draft → signals_extracting
- POST /api/jobs/{id}/enrich dispatches enrich_jd on draft jobs
- 422 EmptyRawJDError when description_raw is empty
- 422 CompanyProfileIncompleteError when no ancestor has a complete profile
- PATCH /api/jobs/{id} updates draft fields, 409 when not draft
- POST /api/jobs/{id}/retry on a non-failed job → 409
- GET non-existent → 404
"""

import uuid
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.auth.models import User
from app.modules.jd.models import (
    JobPosting,
    JobPostingSignalSnapshot,
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
    "industry": "Fintech / Financial Services",
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


def _stub_all_dispatches(monkeypatch) -> dict[str, dict]:
    """Stub every actor's .send() so tests don't enqueue real work.

    Returns a dict mapping actor name → captured kwargs of the last call
    (empty if not dispatched). Tests can assert which actor was triggered."""
    captured: dict[str, dict] = {
        "extract_and_enhance_jd": {},
        "enrich_jd": {},
        "reenrich_jd": {},
    }

    def _make(actor_name: str):
        def _send(*args, **kwargs):
            captured[actor_name] = {"args": args, "kwargs": kwargs}
        return _send

    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        _make("extract_and_enhance_jd"),
    )
    monkeypatch.setattr(
        "app.modules.jd.actors.enrich_jd.send",
        _make("enrich_jd"),
    )
    monkeypatch.setattr(
        "app.modules.jd.actors.reenrich_jd.send",
        _make("reenrich_jd"),
    )
    return captured


# ---------------------------------------------------------------------------
# POST /api/jobs — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_job_lands_in_draft_no_actor(db: AsyncSession, monkeypatch):
    """Super admin creates a job; response is 201 with status='draft' and no
    actor dispatched. Profile completion is not checked at create time."""
    captured = _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    # NOTE: deliberately NO _VALID_PROFILE — proves profile check is gone.
    company = await create_test_org_unit(db, tenant.id, unit_type="company")
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
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "draft"
    assert data["latest_snapshot"] is None
    assert data["title"] == "Sr. Python Engineer"
    assert data["description_raw"] == ""
    # No actor should have been dispatched.
    assert captured["extract_and_enhance_jd"] == {}
    assert captured["enrich_jd"] == {}
    assert captured["reenrich_jd"] == {}


# ---------------------------------------------------------------------------
# POST /api/jobs — 403 missing permission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_job_missing_permission_returns_403(db: AsyncSession, monkeypatch):
    """Non-super-admin with no jobs.create assignment → 403."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(db, tenant.id, unit_type="company")
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=False)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/api/jobs",
                json={"org_unit_id": str(company.id), "title": "Engineer"},
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# PATCH /api/jobs/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_job_updates_draft_fields(db: AsyncSession, monkeypatch):
    """PATCH on a draft job (with a complete-profile ancestor) writes the
    supplied fields."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Initial",
        description_raw="",
        status="draft",
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
            response = await ac.patch(
                f"/api/jobs/{job.id}",
                json={
                    "description_raw": "Full JD text here, plenty of content for the LLM to chew on.",
                    "location": "Bengaluru",
                },
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "draft"
    assert data["description_raw"].startswith("Full JD text")
    assert data["location"] == "Bengaluru"


@pytest.mark.asyncio
async def test_patch_job_rejects_non_draft(db: AsyncSession, monkeypatch):
    """PATCH on a job past draft returns 409 job_not_editable."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Past draft",
        description_raw="A" * 200,
        status="signals_extracted",
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
            response = await ac.patch(
                f"/api/jobs/{job.id}",
                json={"description_raw": "Tweak"},
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "job_not_editable"


@pytest.mark.asyncio
async def test_patch_job_rejects_missing_profile(db: AsyncSession, monkeypatch):
    """PATCH on a draft job whose ancestry has no complete profile returns
    422 company_profile_incomplete. The recruiter can't even edit basics /
    raw JD until the profile is set up."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    # NO _VALID_PROFILE — ancestry has no completed profile.
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=division.id,
        title="Blocked Test",
        description_raw="",
        status="draft",
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
            response = await ac.patch(
                f"/api/jobs/{job.id}",
                json={"description_raw": "Would not stick"},
                headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "company_profile_incomplete"
    assert body["org_unit_id"] == str(division.id)


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/enrich
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_draft_dispatches_enrich_jd(db: AsyncSession, monkeypatch):
    """Enrich on a draft job dispatches the enrich_jd actor (first-time path).
    Lifecycle status stays at draft."""
    captured = _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="draft",
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
            response = await ac.post(f"/api/jobs/{job.id}/enrich", headers=headers)
    finally:
        restore()

    assert response.status_code == 202, response.text
    # enrich_jd (first-time path) was dispatched, not reenrich_jd
    assert captured["enrich_jd"] != {}
    assert captured["reenrich_jd"] == {}


@pytest.mark.asyncio
async def test_enrich_signals_extracted_dispatches_reenrich(db: AsyncSession, monkeypatch):
    """Enrich on a signals_extracted job dispatches reenrich_jd (snapshot-aware)."""
    captured = _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="signals_extracted",
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
            response = await ac.post(f"/api/jobs/{job.id}/enrich", headers=headers)
    finally:
        restore()

    assert response.status_code == 202, response.text
    assert captured["reenrich_jd"] != {}
    assert captured["enrich_jd"] == {}


@pytest.mark.asyncio
async def test_enrich_rejects_empty_raw_jd(db: AsyncSession, monkeypatch):
    """422 EmptyRawJDError when description_raw is empty/whitespace."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="   ",  # whitespace-only
        status="draft",
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
            response = await ac.post(f"/api/jobs/{job.id}/enrich", headers=headers)
    finally:
        restore()

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "empty_raw_jd"


@pytest.mark.asyncio
async def test_enrich_rejects_missing_profile(db: AsyncSession, monkeypatch):
    """422 CompanyProfileIncompleteError when no ancestor has the profile."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    # No _VALID_PROFILE — ancestry has no completed profile.
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=division.id,
        title="Test",
        description_raw="A" * 200,
        status="draft",
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
            response = await ac.post(f"/api/jobs/{job.id}/enrich", headers=headers)
    finally:
        restore()

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "company_profile_incomplete"
    assert body["org_unit_id"] == str(division.id)


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/extract-signals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_signals_transitions_and_dispatches(db: AsyncSession, monkeypatch):
    """Extract-signals transitions draft → signals_extracting and dispatches
    extract_and_enhance_jd with skip_enrichment=True."""
    captured = _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="draft",
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
                f"/api/jobs/{job.id}/extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 202, response.text
    # extract_and_enhance_jd dispatched with skip_enrichment=True
    extract_call = captured["extract_and_enhance_jd"]
    assert extract_call != {}
    assert extract_call["kwargs"].get("skip_enrichment") is True

    # Status now signals_extracting
    await db.refresh(job)
    assert job.status == "signals_extracting"


@pytest.mark.asyncio
async def test_extract_signals_rejects_non_draft(db: AsyncSession, monkeypatch):
    """409 job_not_in_draft_state when the job is past draft."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="signals_extracted",
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
                f"/api/jobs/{job.id}/extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_extract_signals_rejects_empty_raw_jd(db: AsyncSession, monkeypatch):
    """422 EmptyRawJDError on /extract-signals when description_raw is empty."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="",
        status="draft",
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
                f"/api/jobs/{job.id}/extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "empty_raw_jd"


# ---------------------------------------------------------------------------
# 404 / 409 / list / retry — existing coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_nonexistent_job_returns_404(db: AsyncSession):
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


@pytest.mark.asyncio
async def test_retry_on_non_failed_job_returns_409(db: AsyncSession, monkeypatch):
    """POST /api/jobs/{id}/retry on a signals_extracting job → 409 Conflict."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

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

    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_retry_always_skips_enrichment(db: AsyncSession, monkeypatch):
    """/retry only re-runs Phase 2 — Phase 1 enrichment is a separate recruiter
    decision via /enrich. The actor is dispatched with skip_enrichment=True
    regardless of prior enrichment state."""
    captured = _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="signals_extraction_failed",
        source="native",
        created_by=user.id,
        # enrichment_status='failed' would previously have triggered a re-run
        # of Phase 1; under the new model it does not.
        enrichment_status="failed",
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

    assert response.status_code == 202, response.text
    assert captured["extract_and_enhance_jd"]["kwargs"].get("skip_enrichment") is True


@pytest.mark.asyncio
async def test_list_jobs_super_admin_sees_all(db: AsyncSession, monkeypatch):
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    for i in range(2):
        job = JobPosting(
            tenant_id=tenant.id,
            org_unit_id=company.id,
            title=f"Job {i}",
            description_raw="A" * 200,
            status="draft",
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


@pytest.mark.asyncio
async def test_get_job_populates_enrichment_fields(db: AsyncSession):
    """GET /api/jobs/{id} surfaces org_unit_name, emails, signal_count when
    a snapshot exists."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Enrichment Test Job",
        description_raw="A" * 200,
        status="signals_extracted",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snap = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {
                "value": "Python",
                "type": "competency",
                "priority": "required",
                "weight": 2,
                "knockout": False,
                "stage": "screen",
                "evaluation_method": "verbal_response",
                "source": "ai_extracted",
            }
        ],
        seniority_level="mid",
        role_summary="A role summary long enough to pass validation.",
    )
    db.add(snap)
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(f"/api/jobs/{job.id}", headers=headers)
    finally:
        restore()

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["org_unit_name"] == company.name
    assert data["created_by_email"] == user.email
    assert data["signal_count"] == 1


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/re-extract-signals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reextract_from_signals_confirmed_202(db: AsyncSession, monkeypatch):
    """Re-extract from signals_confirmed → 202; job transitions to
    signals_extracting; extraction dispatched with skip_enrichment=True;
    reset_banks_for_job called with the correct job_id."""
    captured = _stub_all_dispatches(monkeypatch)

    reset_calls: list[dict] = []

    async def _fake_reset(db, *, job_id):
        reset_calls.append({"job_id": job_id})
        return 0

    monkeypatch.setattr(
        "app.modules.jd.router.reset_banks_for_job",
        _fake_reset,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="signals_confirmed",
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
                f"/api/jobs/{job.id}/re-extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "accepted"}

    # Banks should have been reset.
    assert len(reset_calls) == 1
    assert reset_calls[0]["job_id"] == job.id

    # Extraction dispatched with skip_enrichment=True.
    extract_call = captured["extract_and_enhance_jd"]
    assert extract_call != {}
    assert extract_call["kwargs"].get("skip_enrichment") is True

    # Status now signals_extracting.
    await db.refresh(job)
    assert job.status == "signals_extracting"


@pytest.mark.asyncio
async def test_reextract_from_active_202(db: AsyncSession, monkeypatch):
    """Re-extract from active → 202; same assertions."""
    captured = _stub_all_dispatches(monkeypatch)
    reset_calls: list[dict] = []

    async def _fake_reset(db, *, job_id):
        reset_calls.append({"job_id": job_id})
        return 0

    monkeypatch.setattr(
        "app.modules.jd.router.reset_banks_for_job",
        _fake_reset,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="active",
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
                f"/api/jobs/{job.id}/re-extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 202, response.text
    assert len(reset_calls) == 1
    extract_call = captured["extract_and_enhance_jd"]
    assert extract_call != {}
    assert extract_call["kwargs"].get("skip_enrichment") is True
    await db.refresh(job)
    assert job.status == "signals_extracting"


@pytest.mark.asyncio
async def test_reextract_from_draft_409(db: AsyncSession, monkeypatch):
    """Re-extract from draft → 409 job_not_re_extractable."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="draft",
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
                f"/api/jobs/{job.id}/re-extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "job_not_re_extractable"


@pytest.mark.asyncio
async def test_reextract_from_archived_409(db: AsyncSession, monkeypatch):
    """Re-extract from archived → 409 job_not_re_extractable."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="A" * 200,
        status="archived",
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
                f"/api/jobs/{job.id}/re-extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "job_not_re_extractable"


@pytest.mark.asyncio
async def test_reextract_rejects_empty_raw_jd(db: AsyncSession, monkeypatch):
    """422 EmptyRawJDError on /re-extract-signals when description_raw is empty."""
    _stub_all_dispatches(monkeypatch)

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test",
        description_raw="",
        status="signals_confirmed",
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
                f"/api/jobs/{job.id}/re-extract-signals", headers=headers,
            )
    finally:
        restore()

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "empty_raw_jd"
