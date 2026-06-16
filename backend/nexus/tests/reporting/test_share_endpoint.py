"""Router-level tests for POST /api/reports/session/{session_id}/share.

Reuses the auth + seed helpers from tests/reporting/test_router.py:
  - _setup_test_context: installs verify_access_token patch + dependency
    overrides for get_current_user_roles + get_tenant_db, returns
    (headers, restore_fn).
  - _seed_report: inserts a SessionReport row with a given status.
  - _get_user_for_tenant: fetches the tenant's seeded user.

DB safety: projectx_test DB via the conftest harness (per-test rollback).
No real Dramatiq enqueue — share_report_pdf.send is monkeypatched.
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.reporting.models import ReportShare
from tests.conftest import seed_minimal_session
from tests.reporting.test_router import (
    _get_user_for_tenant,
    _seed_report,
    _setup_test_context,
)


@pytest.mark.asyncio
async def test_share_returns_202_and_enqueues(db: AsyncSession, monkeypatch):
    """POST share on a ready report → 202, ReportShare row inserted, actor enqueued."""
    session_row, tenant_id = await seed_minimal_session(db)
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    enq: dict = {}
    from app.modules.reporting import router as rep_router

    monkeypatch.setattr(
        rep_router.share_report_pdf,
        "send",
        lambda *args: enq.setdefault("args", args),
    )

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/reports/session/{session_row.id}/share",
                json={"recipient_email": "client@acme.com"},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert "share_id" in body

    # The actor was enqueued with (share_id, tenant_id, correlation_id) as strings.
    assert "args" in enq
    assert enq["args"][0] == body["share_id"]
    assert enq["args"][1] == str(tenant_id)
    assert len(enq["args"]) == 3

    # A report_shares row was actually inserted for that session.
    share_row = (
        await db.execute(
            sqlalchemy.select(ReportShare).where(
                ReportShare.session_id == session_row.id,
                ReportShare.tenant_id == tenant_id,
            )
        )
    ).scalar_one()
    assert str(share_row.id) == body["share_id"]
    assert share_row.status == "pending"
    assert share_row.recipient_email == "client@acme.com"


@pytest.mark.asyncio
async def test_share_409_when_report_not_ready(db: AsyncSession, monkeypatch):
    """POST share when the report is pending (not ready) → 409."""
    session_row, tenant_id = await seed_minimal_session(db)
    await _seed_report(db, session_row, tenant_id, status="pending")
    user_row = await _get_user_for_tenant(db, tenant_id)

    from app.modules.reporting import router as rep_router

    monkeypatch.setattr(rep_router.share_report_pdf, "send", lambda *args: None)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/reports/session/{session_row.id}/share",
                json={"recipient_email": "client@acme.com"},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_share_422_on_bad_email(db: AsyncSession):
    """POST share with an invalid recipient_email → 422 (schema validation)."""
    session_row, tenant_id = await seed_minimal_session(db)
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/reports/session/{session_row.id}/share",
                json={"recipient_email": "nope"},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 422, resp.text
