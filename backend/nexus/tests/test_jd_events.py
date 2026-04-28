"""Integration tests for pub/sub event emission from JD mutations.

Auth pattern: identical to test_jd_router.py — dependency overrides
+ verify_access_token patch. See that file for the rationale.

Covers (one test per task):
  J2 — create_job publishes initial status
  J3 — confirm_signals publishes
  J4 — save_signals publishes (new behavior — was silent for SSE before)
  J5 — retry_extraction publishes
  J6 — enrich_job publishes
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import pubsub
from app.database import get_tenant_db
from app.main import app
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

pytestmark = pytest.mark.asyncio

_TEST_BEARER = "test-jd-events-token"

_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Dependency override + middleware patch helpers
# ---------------------------------------------------------------------------

def _setup_test_context(
    db: AsyncSession,
    user,
    tenant_id: uuid.UUID,
    is_super_admin: bool = True,
):
    """Install all overrides needed for a test request.

    Returns (headers, restore_fn). All three layers:
      1. Patch verify_access_token to accept _TEST_BEARER.
      2. Override get_current_user_roles → pre-built UserContext.
      3. Override get_tenant_db → reuse same db session (stays in the
         per-test connection-level transaction so rollback works).
    """
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
# Seed helpers — build jobs in specific states directly in the DB
# ---------------------------------------------------------------------------

async def _make_job_extracted(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_unit_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: str = "signals_extracted",
    enrichment_status: str = "idle",
    confirmed: bool = False,
    status_error: str | None = None,
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    """Create a job in signals_extracted state with a v1 snapshot."""
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Events Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched job description for testing purposes.",
        status=status,
        enrichment_status=enrichment_status,
        status_error=status_error,
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
            {
                "value": "Python",
                "type": "competency",
                "priority": "required",
                "weight": 2,
                "knockout": False,
                "stage": "interview",
                "source": "ai_extracted",
                "inference_basis": None,
            },
            {
                "value": "5+ years backend",
                "type": "experience",
                "priority": "required",
                "weight": 2,
                "knockout": True,
                "stage": "screen",
                "source": "ai_extracted",
                "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="A senior backend engineer at a fintech startup.",
        confirmed_by=user_id if confirmed else None,
        confirmed_at=datetime.now(UTC) if confirmed else None,
    )
    db.add(snapshot)
    await db.flush()

    return job, snapshot


def _save_signals_body() -> dict:
    """Return a valid SaveSignalsRequest body matching SignalSchemaV2."""
    return {
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
            },
            {
                "value": "FastAPI",
                "type": "competency",
                "priority": "required",
                "weight": 2,
                "knockout": False,
                "stage": "interview",
                "source": "ai_extracted",
                "inference_basis": None,
            },
            {
                "value": "5+ years backend",
                "type": "experience",
                "priority": "required",
                "weight": 2,
                "knockout": True,
                "stage": "screen",
                "source": "ai_extracted",
                "inference_basis": None,
            },
        ],
        "seniority_level": "senior",
        "role_summary": "A senior backend engineer owning the platform end-to-end.",
    }


# ---------------------------------------------------------------------------
# J2: create_job publishes initial status
# ---------------------------------------------------------------------------

async def test_create_job_publishes_initial_status(
    db: AsyncSession, monkeypatch, capture_publishes
):
    """POST /api/jobs publishes jd.status_changed with status='signals_extracting'."""
    # Stub actor dispatch so nothing is enqueued to Redis/Dramatiq
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
            resp = await ac.post(
                "/api/jobs",
                json={
                    "title": "Senior Engineer",
                    "description_raw": "We're hiring a senior engineer with 5+ years of Python experience.",
                    "org_unit_id": str(company.id),
                },
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code in (200, 201), resp.text
    job_id = resp.json()["id"]

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job_id}"
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["job_id"] == job_id
    assert pub.payload["status"] == "signals_extracting"
    assert pub.correlation_id


# ---------------------------------------------------------------------------
# J3: confirm_signals publishes
# ---------------------------------------------------------------------------

async def test_confirm_signals_publishes_status_changed(
    db: AsyncSession, monkeypatch, capture_publishes
):
    """POST /api/jobs/{id}/signals/confirm publishes jd.status_changed."""
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

    job, _snap = await _make_job_extracted(
        db, tenant.id, company.id, user.id, status="signals_extracted",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/signals/confirm",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["status"] == "signals_confirmed"
    assert pub.payload["is_confirmed"] is True


# ---------------------------------------------------------------------------
# J4: save_signals publishes (new SSE behavior)
# ---------------------------------------------------------------------------

async def test_save_signals_publishes_status_changed(
    db: AsyncSession, monkeypatch, capture_publishes
):
    """PATCH /api/jobs/{id}/signals publishes jd.status_changed.

    This was previously silent for SSE — only status/enrichment_status diffs
    emitted. Now every save emits so SSE subscribers see the new snapshot
    version without waiting for the 5s backstop poll.
    """
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

    job, _snap = await _make_job_extracted(
        db, tenant.id, company.id, user.id, status="signals_extracted",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.patch(
                f"/api/jobs/{job.id}/signals",
                json=_save_signals_body(),
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert isinstance(pub.payload["signal_snapshot_version"], int)
    assert pub.payload["signal_snapshot_version"] >= 1


# ---------------------------------------------------------------------------
# J5: retry_extraction publishes
# ---------------------------------------------------------------------------

async def test_retry_extraction_publishes_status_changed(
    db: AsyncSession, monkeypatch, capture_publishes
):
    """POST /api/jobs/{id}/retry publishes jd.status_changed with status='signals_extracting'."""
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

    job, _snap = await _make_job_extracted(
        db,
        tenant.id,
        company.id,
        user.id,
        status="signals_extraction_failed",
        status_error="Some extraction error from a previous attempt",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(f"/api/jobs/{job.id}/retry", headers=headers)
    finally:
        restore()

    assert resp.status_code == 202, resp.text

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["status"] == "signals_extracting"
    assert pub.payload["error"] is None


# ---------------------------------------------------------------------------
# J6: enrich_job publishes
# ---------------------------------------------------------------------------

async def test_enrich_job_publishes_status_changed(
    db: AsyncSession, monkeypatch, capture_publishes
):
    """POST /api/jobs/{id}/enrich publishes jd.status_changed with enrichment_status='streaming'."""
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

    job, _snap = await _make_job_extracted(
        db, tenant.id, company.id, user.id, status="signals_extracted",
    )
    await db.commit()

    headers, restore = _setup_test_context(db, user, tenant.id, is_super_admin=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(f"/api/jobs/{job.id}/enrich", headers=headers)
    finally:
        restore()

    assert resp.status_code == 202, resp.text

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["enrichment_status"] == "streaming"


# ---------------------------------------------------------------------------
# J7: extract_and_enhance_jd actor publishes post-commit
# ---------------------------------------------------------------------------

async def test_extract_actor_publishes_on_success(
    db: AsyncSession, capture_publishes, monkeypatch
):
    """The extract actor publishes jd.status_changed after its commit.

    Actors don't have FastAPI BackgroundTasks — publish fires inline after
    the bypass_session context exits (post-commit). The test monkeypatches
    get_bypass_session to reuse the per-test rollback transaction and mocks
    the LLM call so nothing hits the network.
    """
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from app.ai.schemas import (
        EnrichmentOutput,
        ExtractedSignals,
        SignalExtractionOutput,
        SignalItemV2,
    )
    from app.modules.jd import actors

    # ---- Seed data -----------------------------------------------------------
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Sr Engineer",
        description_raw="A" * 200,
        status="signals_extracting",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    # ---- Mock get_bypass_session to return the test db session ---------------
    # The actor calls get_bypass_session() twice: once for phase 1 and once for
    # phase 2. Both yield the same test db so everything stays inside the
    # per-test rollback transaction.
    @asynccontextmanager
    async def _fake_bypass_session():
        yield db

    monkeypatch.setattr(
        "app.modules.jd.actors.get_bypass_session",
        _fake_bypass_session,
    )

    # ---- Mock the two-phase LLM calls ----------------------------------------
    # Phase 1 returns EnrichmentOutput; phase 2 returns SignalExtractionOutput.
    fake_enrichment = EnrichmentOutput(enriched_jd="A" * 80)
    fake_signals = SignalExtractionOutput(
        signals=ExtractedSignals(
            signals=[
                SignalItemV2(value="Python", type="competency", priority="required", weight=2, knockout=False, stage="interview", source="ai_extracted", inference_basis=None),
                SignalItemV2(value="5+ years backend", type="experience", priority="required", weight=2, knockout=True, stage="screen", source="ai_extracted", inference_basis=None),
                SignalItemV2(value="CS degree", type="credential", priority="preferred", weight=1, knockout=False, stage="screen", source="ai_extracted", inference_basis=None),
                SignalItemV2(value="System Design", type="competency", priority="required", weight=3, knockout=False, stage="interview", source="ai_inferred", inference_basis="Senior role implies architectural ownership"),
                SignalItemV2(value="Mentoring", type="behavioral", priority="preferred", weight=1, knockout=False, stage="interview", source="ai_inferred", inference_basis="Senior role at growth-stage company"),
            ],
            seniority_level="senior",
            role_summary="A senior backend engineer.",
        ),
    )
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        side_effect=[fake_enrichment, fake_signals]
    )
    monkeypatch.setattr(
        "app.modules.jd.actors.get_openai_client",
        lambda: fake_client,
    )

    # ---- Invoke the actor directly (bypass Dramatiq broker/middleware) -------
    # .fn.__wrapped__ is the actual async coroutine under Dramatiq's AsyncIO
    # wrapper — calling it directly lets us await it in the test event loop.
    await actors.extract_and_enhance_jd.fn.__wrapped__(
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id="test-corr-extract",
    )

    # ---- Assert publish -------------------------------------------------------
    # The two-phase actor now publishes THREE times for a non-skipped job:
    #   1. Pre-mark: enrichment_status='streaming', status='signals_extracting'
    #   2. Phase 1 complete: enrichment_status='completed', status='signals_extracting'
    #   3. Phase 2 complete: status='signals_extracted'
    jd_events = [
        p for p in capture_publishes if p.event == pubsub.Events.JD_STATUS_CHANGED
    ]
    assert len(jd_events) == 3, f"expected 3 publishes (pre-mark + phase-1 + phase-2), got {len(jd_events)}"

    # First event: pre-mark streaming state visible to FE loading UI.
    pre_mark_pub = jd_events[0]
    assert pre_mark_pub.channel == f"job:{job.id}"
    assert pre_mark_pub.payload["enrichment_status"] == "streaming"
    assert pre_mark_pub.payload["status"] == "signals_extracting"
    assert pre_mark_pub.correlation_id == "test-corr-extract"

    # Final event: must reflect fully completed extraction.
    final_pub = jd_events[-1]
    assert final_pub.channel == f"job:{job.id}"
    assert final_pub.payload["status"] == "signals_extracted"
    assert final_pub.correlation_id == "test-corr-extract"


# ---------------------------------------------------------------------------
# J8: reenrich_jd actor publishes post-commit
# ---------------------------------------------------------------------------

async def test_reenrich_actor_publishes_on_success(
    db: AsyncSession, capture_publishes, monkeypatch
):
    """The reenrich actor publishes jd.status_changed after its commit.

    Seeds a job in signals_extracted state with enrichment_status='streaming'
    and a confirmed snapshot (reenrich requires a prior snapshot), then
    asserts the publish event reflects enrichment_status='completed'.
    """
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from app.ai.schemas import ReEnrichmentOutput
    from app.modules.jd import actors

    # ---- Seed data -----------------------------------------------------------
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )

    # Job must be in signals_extracted with enrichment_status='streaming'
    # so _run_reenrichment's precondition check passes.
    job, snapshot = await _make_job_extracted(
        db,
        tenant.id,
        company.id,
        user.id,
        status="signals_extracted",
        enrichment_status="streaming",
        confirmed=True,
    )

    # ---- Mock get_bypass_session to return the test db session ---------------
    # The actor calls get_bypass_session() twice: once for the main session and
    # once for the post-commit read. Both yield the same test db so everything
    # stays inside the per-test rollback transaction.
    @asynccontextmanager
    async def _fake_bypass_session():
        yield db

    monkeypatch.setattr(
        "app.modules.jd.actors.get_bypass_session",
        _fake_bypass_session,
    )

    # ---- Mock the LLM call ---------------------------------------------------
    fake_output = ReEnrichmentOutput(enriched_jd="Re-enriched JD content. " * 10)
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_output)
    monkeypatch.setattr(
        "app.modules.jd.actors.get_openai_client",
        lambda: fake_client,
    )

    # ---- Invoke the actor directly (bypass Dramatiq broker/middleware) -------
    await actors.reenrich_jd.fn.__wrapped__(
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id="test-corr-reenrich",
    )

    # ---- Assert publish -------------------------------------------------------
    jd_events = [
        p for p in capture_publishes if p.event == pubsub.Events.JD_STATUS_CHANGED
    ]
    assert len(jd_events) == 1, f"expected 1 publish, got {len(jd_events)}"
    pub = jd_events[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.payload["enrichment_status"] == "completed"
    assert pub.correlation_id == "test-corr-reenrich"

