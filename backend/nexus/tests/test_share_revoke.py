"""Tests for the authenticated revoke endpoint + its effect on the public link.

POST /api/reports/session/{session_id}/shares/{share_id}/revoke
  - reports.view (or super-admin) only,
  - sets revoked_at,
  - after which GET /api/public/recordings/{token} returns 404.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.main import app
from app.modules.reporting.models import ReportShare
from app.modules.reporting.share_tokens import generate_share_token, hash_share_token
from tests.conftest import seed_minimal_session
from tests.reporting.test_router import (
    _get_user_for_tenant,
    _seed_report,
    _setup_test_context,
)


async def _seed_share(
    db: AsyncSession, *, tenant_id, session_id, report_id, token, revoked=False
) -> ReportShare:
    share = ReportShare(
        tenant_id=tenant_id,
        session_id=session_id,
        report_id=report_id,
        recipient_email="external@example.com",
        status="sent",
        share_token_hash=hash_share_token(token),
        share_expires_at=datetime.now(UTC) + timedelta(days=365),
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    db.add(share)
    await db.flush()
    return share


def _bypass_override(db: AsyncSession):
    async def _override():
        yield db
    app.dependency_overrides[get_bypass_db] = _override


@pytest.mark.asyncio
async def test_revoke_then_public_404(db: AsyncSession):
    session, tenant_id = await seed_minimal_session(db, state="completed")
    report = await _seed_report(db, session, tenant_id, status="ready")
    token = generate_share_token()
    share = await _seed_share(
        db, tenant_id=tenant_id, session_id=session.id,
        report_id=report.id, token=token)

    user = await _get_user_for_tenant(db, tenant_id)
    headers, restore = _setup_test_context(db, user, tenant_id)
    _bypass_override(db)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                f"/api/reports/session/{session.id}/shares/{share.id}/revoke",
                headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["revoked"] is True

            pub = await client.get(f"/api/public/recordings/{token}")
            assert pub.status_code == 404
    finally:
        restore()
        app.dependency_overrides.pop(get_bypass_db, None)

    await db.refresh(share)
    assert share.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_unknown_share_404(db: AsyncSession):
    session, tenant_id = await seed_minimal_session(db, state="completed")
    user = await _get_user_for_tenant(db, tenant_id)
    headers, restore = _setup_test_context(db, user, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                f"/api/reports/session/{session.id}/shares/{uuid.uuid4()}/revoke",
                headers=headers)
            assert r.status_code == 404
    finally:
        restore()


@pytest.mark.asyncio
async def test_revoke_without_reports_view_403(db: AsyncSession):
    session, tenant_id = await seed_minimal_session(db, state="completed")
    report = await _seed_report(db, session, tenant_id, status="ready")
    token = generate_share_token()
    share = await _seed_share(
        db, tenant_id=tenant_id, session_id=session.id,
        report_id=report.id, token=token)

    user = await _get_user_for_tenant(db, tenant_id)
    headers, restore = _setup_test_context(db, user, tenant_id, permissions=())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                f"/api/reports/session/{session.id}/shares/{share.id}/revoke",
                headers=headers)
            assert r.status_code == 403
    finally:
        restore()
