"""Integration tests for the public recordings share endpoint.

GET /api/public/recordings/{token} — unauthenticated, token-gated. The handler
opens its own bypass-RLS session via Depends(get_bypass_db); we override that
dependency to yield the test's transaction so flushed rows are visible.

Security focus: a valid token returns the full envelope; every invalid form
(unknown / revoked / expired / malformed) returns a UNIFORM 404 — no oracle.
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
from tests.reporting.test_router import _seed_report


def _install_bypass_override(db: AsyncSession):
    async def _override():
        yield db

    app.dependency_overrides[get_bypass_db] = _override

    def restore():
        app.dependency_overrides.pop(get_bypass_db, None)

    return restore


async def _make_share(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    report_id: uuid.UUID,
    token: str,
    expires_at: datetime,
    revoked: bool = False,
) -> ReportShare:
    share = ReportShare(
        tenant_id=tenant_id,
        session_id=session_id,
        report_id=report_id,
        recipient_email="external-recruiter@example.com",
        status="sent",
        share_token_hash=hash_share_token(token),
        share_expires_at=expires_at,
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    db.add(share)
    await db.flush()
    return share


@pytest.mark.asyncio
async def test_valid_token_returns_envelope(db: AsyncSession):
    session, tenant_id = await seed_minimal_session(db, state="completed")
    report = await _seed_report(db, session, tenant_id, status="ready")
    token = generate_share_token()
    await _make_share(
        db, tenant_id=tenant_id, session_id=session.id, report_id=report.id,
        token=token, expires_at=datetime.now(UTC) + timedelta(days=365),
    )
    restore = _install_bypass_override(db)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/public/recordings/{token}")
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_name"]
    assert body["job_title"]
    for key in ("report", "recording", "proctoring", "reel"):
        assert key in body


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["does-not-exist", "x" * 300, "garbage-token"])
async def test_unknown_or_garbage_token_404(db: AsyncSession, bad: str):
    restore = _install_bypass_override(db)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/public/recordings/{bad}")
    finally:
        restore()
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoked_token_404(db: AsyncSession):
    session, tenant_id = await seed_minimal_session(db, state="completed")
    report = await _seed_report(db, session, tenant_id, status="ready")
    token = generate_share_token()
    await _make_share(
        db, tenant_id=tenant_id, session_id=session.id, report_id=report.id,
        token=token, expires_at=datetime.now(UTC) + timedelta(days=365),
        revoked=True,
    )
    restore = _install_bypass_override(db)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/public/recordings/{token}")
    finally:
        restore()
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_expired_token_404(db: AsyncSession):
    session, tenant_id = await seed_minimal_session(db, state="completed")
    report = await _seed_report(db, session, tenant_id, status="ready")
    token = generate_share_token()
    await _make_share(
        db, tenant_id=tenant_id, session_id=session.id, report_id=report.id,
        token=token, expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    restore = _install_bypass_override(db)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/public/recordings/{token}")
    finally:
        restore()
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_token_is_hashed_not_stored_plaintext(db: AsyncSession):
    """The plaintext token must never be persisted — only its HMAC hash."""
    session, tenant_id = await seed_minimal_session(db, state="completed")
    report = await _seed_report(db, session, tenant_id, status="ready")
    token = generate_share_token()
    share = await _make_share(
        db, tenant_id=tenant_id, session_id=session.id, report_id=report.id,
        token=token, expires_at=datetime.now(UTC) + timedelta(days=365),
    )
    assert share.share_token_hash != token
    assert share.share_token_hash == hash_share_token(token)
