"""HTTP-layer tests for POST /api/candidate-session/{token}/proctoring/event.

Scope:
  * Happy path — hard violation (devtools): 200, terminated=True.
  * Happy path — soft violation (keyboard): 200, terminated=False.
  * Validation: invalid ProctoringKind → 422 (Pydantic rejects before handler).

Auth pattern mirrors test_middleware_candidate_single_use.py:
  - seed_minimal_session builds the full FK graph (Client → session row).
  - mint_candidate_session_token inserts a CandidateSessionToken row and
    returns a signed JWT string.
  - We override get_tenant_db AND patch get_bypass_session so both the
    endpoint's DB session and the middleware's bypass session share the
    same rolled-back test connection.
  - session_service.cancel_room is patched with AsyncMock so no real LiveKit
    call is made.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.main import app
from app.modules.session import service as session_service
from tests.conftest import mint_candidate_session_token, seed_minimal_session


def _patch_bypass_session_to(db: AsyncSession):
    """Return a patcher that routes middleware's get_bypass_session to `db`.

    Same helper pattern used in test_middleware_candidate_single_use.py.
    """

    @asynccontextmanager
    async def _fake_bypass():
        yield db

    return patch("app.middleware.auth.get_bypass_session", _fake_bypass)


@pytest.fixture(autouse=True)
def _termination_enabled(monkeypatch):
    """Pin PROCTORING_TERMINATION_ENABLED on so these endpoint tests are
    hermetic regardless of the developer's local .env (a dev may set it false
    for dry-run testing)."""
    monkeypatch.setattr(session_service.settings, "proctoring_termination_enabled", True)


@pytest.mark.asyncio
async def test_hard_violation_returns_terminated_true(db: AsyncSession):
    """POST devtools (hard kind) for an active session → 200, terminated=True."""
    session, tenant_id = await seed_minimal_session(db, state="active")
    token_str = await mint_candidate_session_token(
        db, session_id=session.id, tenant_id=tenant_id
    )

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db), patch.object(
            session_service, "cancel_room", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    f"/api/candidate-session/{token_str}/proctoring/event",
                    json={
                        "kind": "devtools",
                        "occurred_at": datetime.now(UTC).isoformat(),
                    },
                )
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["terminated"] is True
    assert body["violation_count"] == 1
    assert body["already_terminal"] is False


@pytest.mark.asyncio
async def test_soft_violation_returns_terminated_false(db: AsyncSession):
    """POST keyboard (soft kind) once for an active session → 200, terminated=False.

    Default soft_violation_limit is 3; one keyboard event is well below it.
    """
    session, tenant_id = await seed_minimal_session(db, state="active")
    token_str = await mint_candidate_session_token(
        db, session_id=session.id, tenant_id=tenant_id
    )

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db), patch.object(
            session_service, "cancel_room", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    f"/api/candidate-session/{token_str}/proctoring/event",
                    json={
                        "kind": "keyboard",
                        "occurred_at": datetime.now(UTC).isoformat(),
                    },
                )
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["terminated"] is False
    assert body["violation_count"] == 1
    assert body["soft_violation_count"] == 1


@pytest.mark.asyncio
async def test_invalid_kind_returns_422(db: AsyncSession):
    """POST with an unknown ProctoringKind → 422 (Pydantic validation error).

    No DB access should occur — Pydantic rejects the body before the handler
    runs. We still need the token/bypass infrastructure so the request reaches
    the handler layer (and doesn't get rejected at middleware first).
    """
    session, tenant_id = await seed_minimal_session(db, state="active")
    token_str = await mint_candidate_session_token(
        db, session_id=session.id, tenant_id=tenant_id
    )

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    f"/api/candidate-session/{token_str}/proctoring/event",
                    json={
                        "kind": "screenshot",  # not a valid ProctoringKind
                        "occurred_at": datetime.now(UTC).isoformat(),
                    },
                )
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert resp.status_code == 422, resp.text
