"""Middleware tests for the candidate-JWT supersession + unknown-JTI gates.

Scope (Task 3C.1.6):
  * Fresh token (valid JWT + live DB row, not superseded) → middleware
    accepts. We don't assert 2xx because the candidate-session endpoint
    isn't wired yet (Task 3C.1.17); we only assert the response is NOT
    401 with a middleware-owned code.
  * Superseded token (valid JWT + DB row with `superseded_at` populated)
    → 401 with `code = TOKEN_SUPERSEDED`.
  * Orphan token (valid JWT, no matching DB row) → 401 with
    `code = TOKEN_UNKNOWN`.

Critical invariant being guarded here: the middleware MUST NOT touch
`used_at`. That check lives exclusively in the `/start` endpoint (Task
3C.1.12). Writing it in middleware would race with concurrent pre-check /
consent / request-otp / verify-otp calls and permanently break rejoin
scenarios in Phase 3D. We don't need a negative test for this — it's a
code-review invariant — but the docstring is load-bearing.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.session.models import (
    CandidateSessionToken,
    Session,
)
from app.modules.auth.service import create_candidate_token
from tests.conftest import create_test_client, create_test_user
from tests.conftest import make_assignment_with_stage


async def _make_session_and_token_row(
    db: AsyncSession,
    *,
    superseded: bool = False,
) -> tuple[uuid.UUID, str]:
    """Build the minimum graph to land a real CandidateSessionToken row.

    Returns (jti, jwt). The JWT's claims reference the row's PK, so the
    middleware DB lookup will hit the row we just inserted.
    """
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    await db.flush()
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    jti = uuid.uuid4()
    expires = datetime.now(UTC) + timedelta(days=7)
    token_row = CandidateSessionToken(
        jti=jti,
        tenant_id=tenant.id,
        session_id=session.id,
        expires_at=expires,
        superseded_at=datetime.now(UTC) if superseded else None,
    )
    db.add(token_row)
    await db.flush()

    # Candidate UUID isn't enforced as FK on the row (Phase 3C ships
    # candidate_id as a free field in the JWT), so any UUID works.
    candidate_id = uuid.uuid4()
    token, _exp = create_candidate_token(
        jti=jti,
        candidate_id=candidate_id,
        session_id=session.id,
        tenant_id=tenant.id,
    )
    return jti, token


def _patch_bypass_session_to(db: AsyncSession):
    """Return a patcher that makes middleware's get_bypass_session() yield `db`.

    The middleware runs inside the request lifecycle; we can't use the
    real get_bypass_session() because its own transaction would be invisible
    to the test's rolled-back connection. Instead we patch the imported
    reference inside app.middleware.auth to a context manager yielding the
    test session directly.
    """

    @asynccontextmanager
    async def _fake_bypass():
        yield db

    return patch("app.middleware.auth.get_bypass_session", _fake_bypass)


@pytest.mark.asyncio
async def test_fresh_token_is_accepted_by_middleware(db: AsyncSession):
    """Valid JWT + live DB row, not superseded → middleware does NOT 401.

    As of Task 3C.1.17 the candidate-session router has a real GET
    /pre-check handler, so we additionally override `get_tenant_db` to
    yield the same rolled-back test session — otherwise the handler's
    session-row SELECT runs on a fresh connection that can't see the
    seeded rows and raises SessionNotFoundError. We still only assert
    that the middleware did NOT return a 401 with a middleware-owned
    code; the exact 200/404/422 behavior is tested in
    test_session_router.py.
    """
    from app.database import get_tenant_db

    _jti, token = await _make_session_and_token_row(db, superseded=False)

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.get(f"/api/candidate-session/{token}/pre-check")
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    # Must not be a middleware-authored 401. Any other status (200, 404,
    # 422, etc.) is fine — the middleware is the unit under test here.
    assert r.status_code != 401, r.text
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    assert body.get("code") not in {"TOKEN_UNKNOWN", "TOKEN_SUPERSEDED"}


@pytest.mark.asyncio
async def test_superseded_token_is_rejected_with_code(db: AsyncSession):
    """DB row has superseded_at set → 401 with code=TOKEN_SUPERSEDED."""
    _jti, token = await _make_session_and_token_row(db, superseded=True)

    with _patch_bypass_session_to(db):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/candidate-session/{token}/pre-check")

    assert r.status_code == 401, r.text
    body = r.json()
    assert body["code"] == "TOKEN_SUPERSEDED"
    assert "superseded" in body["detail"].lower()


@pytest.mark.asyncio
async def test_orphan_token_is_rejected_with_code(db: AsyncSession):
    """Valid JWT signature but no matching candidate_session_tokens row."""
    # Don't insert any DB row — just mint a JWT whose JTI is a fresh UUID.
    jti = uuid.uuid4()
    token, _exp = create_candidate_token(
        jti=jti,
        candidate_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )

    with _patch_bypass_session_to(db):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(f"/api/candidate-session/{token}/pre-check")

    assert r.status_code == 401, r.text
    body = r.json()
    assert body["code"] == "TOKEN_UNKNOWN"
    assert "unknown" in body["detail"].lower()
