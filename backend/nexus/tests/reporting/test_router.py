"""Router-level tests for /api/reports/*.

Auth pattern mirrors tests/test_candidates_router.py:
  1. Patch app.middleware.auth.verify_access_token to accept a sentinel bearer.
  2. Override get_current_user_roles to return a pre-built UserContext.
  3. Override get_tenant_db to yield the test's own db session so rows
     flushed in the test are visible to router code.

DB safety:
  - All tests use the projectx_test DB via the conftest harness (per-test
    transaction rollback).
  - No real LLM calls — score_session_report.send is patched to a no-op.
  - No real Dramatiq enqueue — same patch.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.main import app
from app.modules.auth.context import RoleAssignment, UserContext, get_current_user_roles
from app.modules.auth.models import User as UserModel
from app.modules.auth.schemas import TokenPayload
from app.modules.reporting.models import SessionReport
from tests.conftest import create_test_client, create_test_user, seed_minimal_session

_TEST_BEARER = "test-report-router-token"


# ---------------------------------------------------------------------------
# Router registration shim (idempotent — no-op once main.py registers it)
# ---------------------------------------------------------------------------


def _ensure_router_registered() -> None:
    from app.modules.reporting.router import router as reporting_router

    existing = {
        getattr(r, "path_format", "") or getattr(r, "path", "")
        for r in app.routes
    }
    if not any(p.startswith("/api/reports") for p in existing):
        app.include_router(reporting_router)


_ensure_router_registered()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _get_user_for_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> UserModel:
    """Fetch the first user belonging to tenant_id (seeded by seed_minimal_session)."""
    result = await db.execute(
        sqlalchemy.select(UserModel)
        .where(UserModel.tenant_id == tenant_id)
        .limit(1)
    )
    return result.scalar_one()


def _user_ctx(
    user: UserModel,
    *,
    is_super: bool = False,
    permissions: tuple[str, ...] = ("reports.view",),
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
        assignments=assignments,
    )


def _setup_test_context(
    db: AsyncSession,
    user: UserModel,
    tenant_id: uuid.UUID,
    *,
    is_super: bool = False,
    permissions: tuple[str, ...] = ("reports.view",),
) -> tuple[dict[str, str], Any]:
    """Install overrides + patch verify_access_token; return (headers, restore_fn)."""
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
# Seed helper — inserts a SessionReport row
# ---------------------------------------------------------------------------


async def _seed_report(
    db: AsyncSession,
    session_row,
    tenant_id: uuid.UUID,
    *,
    status: str = "ready",
) -> SessionReport:
    """Seed a minimal SessionReport attached to an existing session.

    Uses the new PDF-shaped JSONB layout that _row_to_read and persist_report
    both expect:
      dimension_scores  → scores dict (ScoreOut-shaped per key)
      summary           → {decision, quick_summary, strengths, concerns, methodology}
      question_scorecards / signal_scorecards → lists (may be empty)
    """
    report = SessionReport(
        tenant_id=tenant_id,
        session_id=session_row.id,
        assignment_id=session_row.assignment_id,
        version=1,
        status=status,
        engine_version="v2",
        verdict="advance",
        verdict_reason="Excellent demonstration.",
        overall_score=85,
        overall_coverage=0.9,
        overall_confidence="high",
        # ScoreOut-shaped entries keyed by dimension name
        dimension_scores={
            "overall": {
                "score": 85,
                "tier_label": "Strong",
                "tone": "ok",
                "confidence": "high",
                "coverage": 0.9,
            },
            "technical": {
                "score": 85,
                "tier_label": "Strong",
                "tone": "ok",
                "confidence": "high",
                "coverage": 0.9,
            },
            "behavioral": {
                "score": 80,
                "tier_label": "Strong",
                "tone": "ok",
                "confidence": "medium",
                "coverage": 0.8,
            },
            "communication": {
                "score": 70,
                "tier_label": "Meets Bar",
                "tone": "ok",
                "confidence": "medium",
                "coverage": 1.0,
            },
        },
        signal_scorecards=[],
        question_scorecards=[],
        # summary houses the prose-layer fields that ReportRead unpacks
        summary={
            "decision": {
                "headline": "Strong candidate — recommend advance.",
                "why_positive": {"title": "Clear communicator", "body": "Answered well."},
                "why_negative": {"title": "", "body": ""},
            },
            "quick_summary": "Candidate performed well overall.",
            "strengths": [],
            "concerns": [],
            "methodology": {"note": "", "charity_flags": []},
        },
        generated_at=datetime.now(UTC),
    )
    db.add(report)
    await db.flush()
    return report


# ---------------------------------------------------------------------------
# Tests: GET /api/reports/session/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_by_session_ready(db: AsyncSession):
    """GET session/{session_id} — ready report → 200 with verdict."""
    session_row, tenant_id = await seed_minimal_session(db)
    report = await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/reports/session/{session_row.id}", headers=headers)
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "advance"
    assert body["status"] == "ready"
    assert body["id"] == str(report.id)


@pytest.mark.asyncio
async def test_get_report_by_session_pending_returns_202(db: AsyncSession):
    """GET session/{session_id} — pending report → 202 with status."""
    session_row, tenant_id = await seed_minimal_session(db)
    await _seed_report(db, session_row, tenant_id, status="pending")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/reports/session/{session_row.id}", headers=headers)
    finally:
        restore()

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_get_report_by_session_generating_returns_202(db: AsyncSession):
    """GET session/{session_id} — generating report → 202."""
    session_row, tenant_id = await seed_minimal_session(db)
    await _seed_report(db, session_row, tenant_id, status="generating")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/reports/session/{session_row.id}", headers=headers)
    finally:
        restore()

    assert r.status_code == 202, r.text
    assert r.json()["status"] == "generating"


@pytest.mark.asyncio
async def test_get_report_by_session_not_found(db: AsyncSession):
    """GET session/{session_id} — no report for that session → 404."""
    session_row, tenant_id = await seed_minimal_session(db)
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # session_row exists but has no report
            r = await ac.get(f"/api/reports/session/{session_row.id}", headers=headers)
    finally:
        restore()

    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_get_report_by_session_missing_permission_403(db: AsyncSession):
    """GET session/{session_id} without reports.view → 403."""
    session_row, tenant_id = await seed_minimal_session(db)
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    # Caller has no reports.view permission
    headers, restore = _setup_test_context(
        db, user_row, tenant_id, permissions=("candidates.view",)
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/reports/session/{session_row.id}", headers=headers)
    finally:
        restore()

    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Cross-tenant test: tenant B cannot see tenant A's report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_by_session_cross_tenant_returns_404(db: AsyncSession):
    """A report owned by tenant A must be invisible to a tenant-B request.

    The handler queries SessionReport with an explicit tenant_id filter (not
    relying solely on RLS, which is disabled in tests). So a different tenant
    must get 404, not 200.
    """
    # Tenant A: create session + report
    session_row_a, tenant_id_a = await seed_minimal_session(db)
    await _seed_report(db, session_row_a, tenant_id_a, status="ready")

    # Tenant B: separate client + user
    client_b = await create_test_client(db)
    user_b = await create_test_user(db, client_b.id)

    headers_b, restore_b = _setup_test_context(db, user_b, client_b.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Tenant B tries to read tenant A's session report
            r = await ac.get(
                f"/api/reports/session/{session_row_a.id}", headers=headers_b
            )
    finally:
        restore_b()

    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Tests: GET /api/reports/{report_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_by_id_ready(db: AsyncSession):
    """GET /{report_id} — ready report → 200 with verdict."""
    session_row, tenant_id = await seed_minimal_session(db)
    report = await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/reports/{report.id}", headers=headers)
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(report.id)
    assert body["verdict"] == "advance"


@pytest.mark.asyncio
async def test_get_report_by_id_not_found(db: AsyncSession):
    """GET /{report_id} — non-existent → 404."""
    session_row, tenant_id = await seed_minimal_session(db)
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/reports/{uuid.uuid4()}", headers=headers)
    finally:
        restore()

    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_get_report_by_id_cross_tenant_404(db: AsyncSession):
    """GET /{report_id} cross-tenant → 404 (explicit tenant filter)."""
    session_row_a, tenant_id_a = await seed_minimal_session(db)
    report_a = await _seed_report(db, session_row_a, tenant_id_a, status="ready")

    client_b = await create_test_client(db)
    user_b = await create_test_user(db, client_b.id)

    headers_b, restore_b = _setup_test_context(db, user_b, client_b.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/reports/{report_a.id}", headers=headers_b)
    finally:
        restore_b()

    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Tests: POST /api/reports/session/{session_id}/regenerate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_report_enqueues_and_returns_202(db: AsyncSession):
    """POST regenerate → 202 and score_session_report.send called."""
    session_row, tenant_id = await seed_minimal_session(db)
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    # Regenerate requires is_super_admin (privileged/destructive gate)
    headers, restore = _setup_test_context(
        db, user_row, tenant_id, is_super=True
    )
    try:
        with patch(
            "app.modules.reporting.actors.score_session_report.send"
        ) as mock_send:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    f"/api/reports/session/{session_row.id}/regenerate",
                    headers=headers,
                )
    finally:
        restore()

    assert r.status_code == 202, r.text
    mock_send.assert_called_once()
    # First positional arg must be the session_id string
    assert mock_send.call_args.args[0] == str(session_row.id)


@pytest.mark.asyncio
async def test_regenerate_report_forbidden_without_super_admin(db: AsyncSession):
    """POST regenerate without super-admin → 403."""
    session_row, tenant_id = await seed_minimal_session(db)
    user_row = await _get_user_for_tenant(db, tenant_id)

    # Regular recruiter, not super admin
    headers, restore = _setup_test_context(
        db, user_row, tenant_id, is_super=False, permissions=("reports.view",)
    )
    try:
        with patch("app.modules.reporting.actors.score_session_report.send"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    f"/api/reports/session/{session_row.id}/regenerate",
                    headers=headers,
                )
    finally:
        restore()

    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Tests: POST /api/reports/session/{session_id}/proctoring/retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_proctoring_enqueues_and_returns_202(db: AsyncSession):
    """POST proctoring/retry → 202 and analyze_session_proctoring.send called
    with (session_id, tenant_id)."""
    session_row, tenant_id = await seed_minimal_session(db)
    user_row = await _get_user_for_tenant(db, tenant_id)

    # Super-admin gate (matches report regenerate).
    headers, restore = _setup_test_context(db, user_row, tenant_id, is_super=True)
    try:
        with patch(
            "app.modules.vision.analyze_session_proctoring.send"
        ) as mock_send:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    f"/api/reports/session/{session_row.id}/proctoring/retry",
                    headers=headers,
                )
    finally:
        restore()

    assert r.status_code == 202, r.text
    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == str(session_row.id)
    assert mock_send.call_args.args[1] == str(tenant_id)


@pytest.mark.asyncio
async def test_retry_proctoring_forbidden_without_super_admin(db: AsyncSession):
    """POST proctoring/retry without super-admin → 403."""
    session_row, tenant_id = await seed_minimal_session(db)
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(
        db, user_row, tenant_id, is_super=False, permissions=("reports.view",)
    )
    try:
        with patch("app.modules.vision.analyze_session_proctoring.send") as mock_send:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    f"/api/reports/session/{session_row.id}/proctoring/retry",
                    headers=headers,
                )
    finally:
        restore()

    assert r.status_code == 403, r.text
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: POST /api/reports/{report_id}/decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_decision_persists_and_returns_200(db: AsyncSession):
    """POST /{report_id}/decision → 200, human_decision persisted, audit written."""
    session_row, tenant_id = await seed_minimal_session(db)
    report = await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    captured_log_event_calls: list[dict] = []

    async def _mock_log_event(db_arg, **kwargs):
        captured_log_event_calls.append(kwargs)

    try:
        with patch(
            "app.modules.reporting.router.log_event",
            side_effect=_mock_log_event,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    f"/api/reports/{report.id}/decision",
                    json={"decision": "advance", "rationale": "Candidate is excellent"},
                    headers=headers,
                )
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["human_decision"]["decision"] == "advance"
    assert body["human_decision"]["rationale"] == "Candidate is excellent"
    assert "decided_by" in body["human_decision"]
    assert "decided_at" in body["human_decision"]

    # Verify audit was called
    assert len(captured_log_event_calls) == 1
    call = captured_log_event_calls[0]
    assert call["resource"] == "session_report"
    assert call["resource_id"] == report.id

    # Verify DB was actually updated
    await db.refresh(report)
    assert report.human_decision is not None
    assert report.human_decision["decision"] == "advance"


@pytest.mark.asyncio
async def test_post_decision_missing_permission_403(db: AsyncSession):
    """POST /{report_id}/decision without reports.view → 403."""
    session_row, tenant_id = await seed_minimal_session(db)
    report = await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(
        db, user_row, tenant_id, permissions=("candidates.view",)
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/api/reports/{report.id}/decision",
                json={"decision": "reject", "rationale": "Not a fit"},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_post_decision_report_not_found_404(db: AsyncSession):
    """POST /{report_id}/decision for unknown report_id → 404."""
    session_row, tenant_id = await seed_minimal_session(db)
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/api/reports/{uuid.uuid4()}/decision",
                json={"decision": "hold", "rationale": "Need more info"},
                headers=headers,
            )
    finally:
        restore()

    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Tests: GET /api/reports (index)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_index_lists_completed_with_report(db: AsyncSession):
    """GET /api/reports → completed session with a ready report appears."""
    session_row, tenant_id = await seed_minimal_session(db, state="completed")
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/reports", headers=headers)
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    item = next(
        (it for it in body["items"] if it["session_id"] == str(session_row.id)),
        None,
    )
    assert item is not None, body
    assert item["report_status"] == "ready"
    assert item["verdict"] == "advance"
    assert item["overall_score"] == 8.5  # recruiter-facing 0-10 scale: to_ten(85) = 8.5


@pytest.mark.asyncio
async def test_report_index_missing_permission_403(db: AsyncSession):
    """GET /api/reports without reports.view → 403."""
    session_row, tenant_id = await seed_minimal_session(db, state="completed")
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(
        db, user_row, tenant_id, permissions=("candidates.view",)
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/reports", headers=headers)
    finally:
        restore()

    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_report_index_cross_tenant_excluded(db: AsyncSession):
    """Tenant B's index must not contain tenant A's completed session."""
    session_a, tenant_a = await seed_minimal_session(db, state="completed")
    await _seed_report(db, session_a, tenant_a, status="ready")

    client_b = await create_test_client(db)
    user_b = await create_test_user(db, client_b.id)

    headers_b, restore_b = _setup_test_context(db, user_b, client_b.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/reports", headers=headers_b)
    finally:
        restore_b()

    assert r.status_code == 200, r.text
    sids = [it["session_id"] for it in r.json()["items"]]
    assert str(session_a.id) not in sids
