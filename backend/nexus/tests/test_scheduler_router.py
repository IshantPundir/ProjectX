"""Scheduler router — POST /api/scheduler/invites, /resend, /revoke.

Recruiter-facing endpoints that drive the invite lifecycle. The
AuthMiddleware expects a Bearer header for non-candidate paths, so each
test patches `verify_access_token` to accept a synthetic `_TEST_BEARER`
and overrides `get_current_user_roles` with a UserContext carrying both
`candidates.manage` and `jobs.manage` — pattern copied from
tests/test_candidates_router.py.

Task-chain note: main.py registers `scheduler_router` in Task 3C.1.19.
Between 3C.1.17 and 3C.1.19 the transitional `router = scheduler_router`
alias in scheduler/router.py keeps main.py importable; main.py still
mounts the old `router` symbol so all three paths resolve. The only
router-test failures in this window are the ones that depend on
exception handlers which 3C.1.19 installs (e.g. the 422 INVALID_STAGE
mapping). Those are the expected "deferred-to-3C.1.19" cases.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_tenant_db
from app.main import app
from app.modules.auth.context import RoleAssignment, UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from tests.test_scheduler_service import _seed

_TEST_BEARER = "test-scheduler-bearer"


def _ctx(user, permissions=("candidates.manage", "jobs.manage")):
    """Build a UserContext with the given permissions on a synthetic unit."""
    return UserContext(
        user=user,
        is_super_admin=False,
        assignments=[
            RoleAssignment(
                org_unit_id=uuid.uuid4(),
                org_unit_name="Root",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=list(permissions),
            )
        ],
    )


@pytest.fixture
async def http_client(db):
    """Bring up an ASGI client with overrides for tenant DB and the
    middleware's `verify_access_token` — the middleware's Bearer check runs
    before the route handler, so we stub it to accept our synthetic token.
    """
    async def _override_db():
        yield db

    def _fake_verify(token: str):
        if token == _TEST_BEARER:
            return TokenPayload(
                sub=str(uuid.uuid4()),
                tenant_id=str(uuid.uuid4()),
                email="test@example.com",
                is_projectx_admin=False,
                exp=9_999_999_999,
            )
        return None

    app.dependency_overrides[get_tenant_db] = _override_db
    with patch("app.middleware.auth.verify_access_token", side_effect=_fake_verify):
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {_TEST_BEARER}"},
            ) as ac:
                yield ac
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_invite_returns_201_with_session_id(db, http_client):
    _t, user, _stage, _cand, assignment = await _seed(db)
    app.dependency_overrides[get_current_user_roles] = lambda: _ctx(user)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()):
        r = await http_client.post(
            "/api/scheduler/invites",
            json={"assignment_id": str(assignment.id)},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "session_id" in body
    assert "token_expires_at" in body


@pytest.mark.asyncio
async def test_post_invite_422_for_non_ai_stage(db, http_client):
    _t, user, _stage, _cand, assignment = await _seed(db, stage_type="manual_review")
    app.dependency_overrides[get_current_user_roles] = lambda: _ctx(user)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()):
        r = await http_client.post(
            "/api/scheduler/invites",
            json={"assignment_id": str(assignment.id)},
        )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "INVALID_STAGE_TYPE_FOR_INVITE"


@pytest.mark.asyncio
async def test_post_resend_returns_201(db, http_client):
    _t, user, _stage, _cand, assignment = await _seed(db)
    app.dependency_overrides[get_current_user_roles] = lambda: _ctx(user)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()):
        first = await http_client.post(
            "/api/scheduler/invites",
            json={"assignment_id": str(assignment.id)},
        )
        assert first.status_code == 201
        session_id = first.json()["session_id"]
        r = await http_client.post(f"/api/scheduler/invites/{session_id}/resend")
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_post_revoke_returns_204(db, http_client):
    _t, user, _stage, _cand, assignment = await _seed(db)
    app.dependency_overrides[get_current_user_roles] = lambda: _ctx(user)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()):
        first = await http_client.post(
            "/api/scheduler/invites",
            json={"assignment_id": str(assignment.id)},
        )
        assert first.status_code == 201
        r = await http_client.post(
            f"/api/scheduler/invites/{first.json()['session_id']}/revoke"
        )
    assert r.status_code == 204, r.text
