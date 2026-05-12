"""Router-level integration tests for /api/ats/* (Task 28).

Strategy:
  We exercise the router via FastAPI's TestClient pattern by combining the
  bearer-header pattern used elsewhere in this test suite (see
  ``tests/test_candidates_router.py`` for the canonical example) with
  ``app.dependency_overrides`` on the auth + tenant-db boundaries. No real
  JWTs, no JWKS, no Supabase.

  Specifically:
    * ``app.middleware.auth.verify_access_token`` is patched to accept a
      sentinel bearer string and return a synthetic ``TokenPayload`` whose
      ``tenant_id`` matches the seeded tenant. This satisfies the
      ``AuthMiddleware`` gate that runs before any dependency resolution.
    * ``require_ats_admin`` and ``get_current_user_roles`` are overridden to
      return a fake super-admin ``UserContext`` so the router's authz layer
      passes and ``user.user.tenant_id`` resolves against the seeded row.
    * ``get_tenant_db`` is overridden to yield the rollback-isolated ``db``
      session from ``tests/conftest.py``. The router's ``await db.commit()``
      is harmless on this nested transaction (flush-equivalent), so the test
      still rolls back at teardown.

  ``app.modules.ats.service.get_ats_adapter`` is patched per-test to return a
  fake adapter — either succeeding (mutating ``state`` so the service has
  tokens to encrypt) or raising ``ATSCredentialsInvalidError``.

Test scope vs. the plan body (lines 6244-6332):
  - Implemented: ``test_post_connections_201_on_valid_creds``,
    ``test_post_connections_422_on_invalid_creds``.
  - Skipped here (deferred to Task 43 E2E with real JWT + JWKS fixture):
    * ``test_post_connections_403_for_non_super_admin`` — requires a real
      authed-non-admin client. We override the super-admin dependency to
      let the request through, so we cannot exercise the 403 path through
      that same override.
    * ``test_get_connections_returns_no_credentials_fields`` — requires the
      plan's ``seeded_connection`` fixture (a cross-session committed
      row), which is incompatible with the rollback-isolated ``db``
      fixture used here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import get_tenant_db
from app.main import app
from app.modules.ats.authz import require_ats_admin
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.models import User
from app.modules.auth.schemas import TokenPayload


# Force registration of ATS ORM classes with Base.metadata so the
# session-scoped _create_tables fixture (tests/conftest.py) builds the
# ats_* tables. The module-level import has the side effect of registering
# the mappers.
from app.modules.ats import models as _ats_models  # noqa: F401


_TEST_BEARER = "test-bearer-token"


@pytest.fixture(autouse=True)
def _enc(monkeypatch):
    """Provision an ATS encryption key for the duration of the test."""
    from app.config import settings
    from app.modules.ats import crypto

    monkeypatch.setattr(
        settings,
        "ats_credentials_encryption_keys",
        [Fernet.generate_key().decode()],
    )
    crypto._fernet = None


@pytest.fixture
async def seed_super_admin(db):
    """Seed (tenant, user) on the rollback-isolated db session.

    The user's tenant_id matches the inserted client row, so when the
    overridden ``require_ats_admin`` returns a UserContext built around
    this User, the router resolves ``user.user.tenant_id`` to a real row.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
        {"t": tenant_id},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, email, tenant_id, auth_user_id) "
            "VALUES (:u, 'u@x.com', :t, :a)"
        ),
        {"u": user_id, "t": tenant_id, "a": uuid.uuid4()},
    )
    await db.flush()
    user = await db.get(User, user_id)
    return user


@pytest.fixture
async def authed_super_admin_client(db, seed_super_admin):
    """An AsyncClient with auth + tenant-db dependencies overridden.

    Returns ``(client, user)``. Cleans up ``app.dependency_overrides`` and
    the verify_access_token patch at test teardown.
    """
    user = seed_super_admin
    ctx = UserContext(user=user, is_super_admin=True, assignments=[])

    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(user.tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )

    def _fake_verify(token: str):
        if token == _TEST_BEARER:
            return fake_payload
        return None

    async def _fake_user_ctx() -> UserContext:
        return ctx

    async def _fake_tenant_db():
        # Yield the rollback-isolated test session; the router calls
        # ``await db.commit()`` which is a flush()-equivalent on this
        # nested transaction, so the test still rolls back at teardown.
        yield db

    app.dependency_overrides[require_ats_admin] = _fake_user_ctx
    app.dependency_overrides[get_current_user_roles] = _fake_user_ctx
    app.dependency_overrides[get_tenant_db] = _fake_tenant_db

    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {_TEST_BEARER}"},
        ) as ac:
            yield ac, user
    finally:
        verify_patch.stop()
        app.dependency_overrides.pop(require_ats_admin, None)
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)


@pytest.mark.asyncio
async def test_post_connections_201_on_valid_creds(authed_super_admin_client):
    """A super_admin posts valid Ceipal credentials → 201 + connection metadata.

    The adapter is mocked to succeed; the service layer encrypts credentials,
    persists an ats_connections row, and the router serializes it through
    ``ConnectionResponse`` — which does NOT include any credential fields.
    """
    client, _ = authed_super_admin_client

    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake = AsyncMock()
        fake.ensure_authenticated = AsyncMock()

        def _bind(state):
            state.access_token = "tok"
            state.access_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(
                hours=1
            )
            fake.state = state
            return fake

        mock_get.side_effect = _bind

        # The router also calls trigger_manual_sync, which sends a Dramatiq
        # message. In tests, the broker is the test stub; patching the
        # actor's .send() makes that call a no-op rather than depending on
        # broker setup.
        with patch("app.modules.ats.actors.poll_ats_connection.send"):
            resp = await client.post(
                "/api/ats/connections",
                json={
                    "vendor": "ceipal",
                    "credentials": {
                        "email": "u@x.com",
                        "password": "p",
                        "api_key": "k",
                    },
                },
            )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["vendor"] == "ceipal"
    assert body["active"] is True
    # Credentials never leak in any form.
    assert "password" not in resp.text
    assert "api_key" not in resp.text
    assert "credentials" not in body
    assert "access_token" not in body


@pytest.mark.asyncio
async def test_post_connections_422_on_invalid_creds(authed_super_admin_client):
    """Adapter raises ATSCredentialsInvalidError → 422 with the contract envelope."""
    from app.modules.ats.errors import ATSCredentialsInvalidError

    client, _ = authed_super_admin_client
    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake = AsyncMock()
        fake.ensure_authenticated = AsyncMock(
            side_effect=ATSCredentialsInvalidError("bad password"),
        )

        def _bind(state):
            fake.state = state
            return fake

        mock_get.side_effect = _bind

        resp = await client.post(
            "/api/ats/connections",
            json={
                "vendor": "ceipal",
                "credentials": {
                    "email": "u@x.com",
                    "password": "wrong",
                    "api_key": "k",
                },
            },
        )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "ATS_CREDENTIALS_INVALID"
    # Error message must not echo input credentials back to the client.
    assert "wrong" not in resp.text
