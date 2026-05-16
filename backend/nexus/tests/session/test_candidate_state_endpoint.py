"""Tests for GET /api/candidate-session/{token}/state.

Covers three contract points:
  1. Happy path — valid token, session in 'active' state → 200 + correct snapshot.
  2. Error-state read-back — session in 'error' with a populated error_code is
     reflected faithfully in the response body.
  3. Cross-tenant denial — a token whose JWT tenant_id doesn't match the session
     must NOT leak whether the session exists. Response is 401 or 404 — both
     are opaque. The invariant: no information about another tenant's sessions.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_tenant_db
from app.main import app
from app.modules.session.models import CandidateSessionToken
from tests.conftest import (
    mint_candidate_session_token,
    seed_minimal_session,
)


def _patch_bypass_session_to(db: AsyncSession):
    """Patch middleware's get_bypass_session() to yield the test db session.

    The middleware performs its own DB lookup (checking the jti in
    candidate_session_tokens) using a bypass session. That session opens its
    own connection which can't see the test's rolled-back in-flight rows.
    Patching it to yield our test session bridges the isolation gap.
    """

    @asynccontextmanager
    async def _fake_bypass():
        yield db

    return patch("app.middleware.auth.get_bypass_session", _fake_bypass)


@pytest.mark.asyncio
async def test_state_happy_path(db: AsyncSession):
    """Valid token + session in 'active' state → 200 with correct snapshot."""
    session, tenant_id = await seed_minimal_session(db, state="active")
    token = await mint_candidate_session_token(
        db, session_id=session.id, tenant_id=tenant_id,
    )

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get(f"/api/candidate-session/{token}/state")
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "active"
    assert body["error_code"] is None


@pytest.mark.asyncio
async def test_state_after_transition_to_error(db: AsyncSession):
    """Session in state='error' with error_code → both fields read back correctly."""
    session, tenant_id = await seed_minimal_session(db, state="active")
    # Directly mutate the ORM row — bypasses the state-machine for test isolation.
    session.state = "error"
    session.error_code = "engine_session_config_invalid"
    await db.flush()

    token = await mint_candidate_session_token(
        db, session_id=session.id, tenant_id=tenant_id,
    )

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get(f"/api/candidate-session/{token}/state")
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "error"
    assert body["error_code"] == "engine_session_config_invalid"


@pytest.mark.asyncio
async def test_state_cross_tenant_token_returns_opaque_error(db: AsyncSession):
    """Token claiming a wrong tenant_id must NOT reveal the session's real tenant.

    Strategy: the token row is inserted with the REAL tenant (satisfying the FK
    constraint on candidate_session_tokens.tenant_id → clients.id), but the JWT
    claims encode a FAKE tenant_id. The middleware finds the row by jti (PK
    lookup, not tenant-filtered) and accepts it as valid and not superseded. It
    then decodes the JWT and sets request.state.candidate_token_payload.tenant_id
    = fake_tenant. The handler queries sessions WHERE tenant_id = fake_tenant AND
    id = session.id — that combination doesn't exist, so it returns 404.

    Either 401 (if middleware rejects for any reason) or 404 (handler's
    tenant-scoped WHERE clause returns no row) is acceptable. The invariant: the
    real tenant's session details are never returned to the caller.
    """
    session, real_tenant = await seed_minimal_session(db, state="active")

    # Insert the token row under the REAL tenant to satisfy the FK constraint.
    jti = uuid.uuid4()
    expires = datetime.now(UTC) + timedelta(days=7)
    token_row = CandidateSessionToken(
        jti=jti,
        tenant_id=real_tenant,  # real tenant satisfies FK
        session_id=session.id,
        expires_at=expires,
    )
    db.add(token_row)
    await db.flush()

    # Encode a FAKE tenant_id in the JWT claims — the middleware sets this as
    # the effective tenant, so the handler's WHERE clause won't match the session.
    fake_tenant = uuid.uuid4()
    claims = {
        "jti": str(jti),
        "sub": str(uuid.uuid4()),
        "session_id": str(session.id),
        "tenant_id": str(fake_tenant),  # ← mismatch: not the real tenant
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(days=7)).timestamp()),
    }
    token = pyjwt.encode(claims, settings.candidate_jwt_secret, algorithm="HS256")

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get(f"/api/candidate-session/{token}/state")
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    # 401 if middleware rejects; 404 if the handler's tenant-scoped WHERE returns
    # no row. Either is opaque — the real tenant's session is never disclosed.
    assert resp.status_code in (401, 404), resp.text
