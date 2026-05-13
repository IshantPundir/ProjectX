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


@pytest.mark.asyncio
async def test_post_connections_does_not_trigger_any_sync(authed_super_admin_client):
    """POST /connections creates the connection but does NOT enqueue a sync.

    Dev-mode manual control: the recruiter clicks per-phase Sync buttons on
    the detail page when ready. The Dramatiq actor's .send() is captured;
    we assert it was never invoked during connection creation.
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

        with patch("app.modules.ats.actors.poll_ats_connection.send") as mock_send:
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
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_manual_sync_no_body_runs_all_phases(authed_super_admin_client, db):
    """POST /sync with no body enqueues a full sync (phase_filter=None)."""
    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    with patch("app.modules.ats.actors.poll_ats_connection.send") as mock_send:
        resp = await client.post(f"/api/ats/connections/{conn_id}/sync")

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "enqueued", "phases": None}
    mock_send.assert_called_once()
    _args, _ = mock_send.call_args
    # Third positional arg is phase_filter.
    assert _args[2] is None


@pytest.mark.asyncio
async def test_manual_sync_with_phases_scopes_the_run(authed_super_admin_client, db):
    """POST /sync with {phases: ["clients"]} enqueues a run limited to that
    one phase. Dev-mode per-phase manual control.
    """
    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    with patch("app.modules.ats.actors.poll_ats_connection.send") as mock_send:
        resp = await client.post(
            f"/api/ats/connections/{conn_id}/sync",
            json={"phases": ["clients"]},
        )

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "enqueued", "phases": ["clients"]}
    _args, _ = mock_send.call_args
    assert _args[2] == ["clients"]


@pytest.mark.asyncio
async def test_manual_sync_unknown_phase_422(authed_super_admin_client, db):
    """POST /sync with a phase name outside the closed enum → 422 from
    Pydantic before the handler runs."""
    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    resp = await client.post(
        f"/api/ats/connections/{conn_id}/sync",
        json={"phases": ["not-a-phase"]},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_get_job_statuses_returns_adapter_list(authed_super_admin_client, db):
    """GET /connections/{id}/job-statuses → 200 with the vendor list.

    The adapter is patched at ``app.modules.ats.router.get_ats_adapter``.
    ``load_connection_state`` is patched to skip credential decryption —
    the test DB row uses b"x" which is not a real Fernet ciphertext.
    """
    from app.modules.ats.connection import ATSConnectionState

    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()

    # Seed the ats_connections row so the router's db.get() finds it.
    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    fake_adapter = AsyncMock()
    fake_adapter.list_job_statuses = AsyncMock(
        return_value=[
            {"id": 1, "name": "Active"},
            {"id": 4, "name": "Jobs Filled"},
        ]
    )
    fake_adapter.aclose = AsyncMock()

    fake_state = ATSConnectionState(
        id=conn_id,
        tenant_id=user.tenant_id,
        vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
    )

    with patch(
        "app.modules.ats.router.load_connection_state",
        new=AsyncMock(return_value=fake_state),
    ):
        with patch("app.modules.ats.router.get_ats_adapter", return_value=fake_adapter):
            resp = await client.get(f"/api/ats/connections/{conn_id}/job-statuses")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == [
        {"id": 1, "name": "Active"},
        {"id": 4, "name": "Jobs Filled"},
    ]


@pytest.mark.asyncio
async def test_get_job_statuses_422_on_credentials_invalid(
    authed_super_admin_client, db
):
    """GET /job-statuses → 422 when adapter raises ATSCredentialsInvalidError."""
    from app.modules.ats.connection import ATSConnectionState
    from app.modules.ats.errors import ATSCredentialsInvalidError

    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    fake_adapter = AsyncMock()
    fake_adapter.list_job_statuses = AsyncMock(
        side_effect=ATSCredentialsInvalidError("revoked")
    )
    fake_adapter.aclose = AsyncMock()

    fake_state = ATSConnectionState(
        id=conn_id,
        tenant_id=user.tenant_id,
        vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
    )

    with patch(
        "app.modules.ats.router.load_connection_state",
        new=AsyncMock(return_value=fake_state),
    ):
        with patch("app.modules.ats.router.get_ats_adapter", return_value=fake_adapter):
            resp = await client.get(f"/api/ats/connections/{conn_id}/job-statuses")

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "ATS_CREDENTIALS_INVALID"


@pytest.mark.asyncio
async def test_put_job_status_filter_persists_without_triggering_sync(
    authed_super_admin_client, db
):
    """PUT /job-status-filter → 204, row persisted, NO follow-up sync.

    Dev-mode manual control: the filter is persisted so the next
    user-triggered jobs sync picks it up, but the PUT itself does not
    enqueue anything. Verifies:
      1. Response is 204.
      2. The connection row's ``job_status_filter`` is updated.
      3. ``poll_ats_connection.send`` was NOT called.
    """
    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    with patch("app.modules.ats.actors.poll_ats_connection.send") as mock_send:
        resp = await client.put(
            f"/api/ats/connections/{conn_id}/job-status-filter",
            json={"status_ids": [1, 8], "names": ["Active", "Reactivated"]},
        )

    assert resp.status_code == 204, resp.text

    # Reload the row on the same session to verify persistence.
    from app.modules.ats.models import ATSConnection as ATSConn
    await db.refresh(await db.get(ATSConn, conn_id))
    row = await db.get(ATSConn, conn_id)
    assert row is not None
    assert row.job_status_filter == {"ids": [1, 8], "names": ["Active", "Reactivated"]}

    # No sync should be enqueued by the PUT.
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_put_job_status_filter_422_on_empty_ids(authed_super_admin_client, db):
    """PUT /job-status-filter with empty lists → 422 (Pydantic min_length=1)."""
    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    resp = await client.put(
        f"/api/ats/connections/{conn_id}/job-status-filter",
        json={"status_ids": [], "names": []},
    )
    # Pydantic min_length=1 validation fires before the handler runs → 422.
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_sync_log_response_includes_progress(authed_super_admin_client, db):
    """GET /sync-logs → response body includes the ``progress`` field."""
    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()
    log_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.execute(
        text(
            "INSERT INTO ats_sync_logs "
            "(id, tenant_id, connection_id, started_at, status, "
            " entity_counts, progress, correlation_id) "
            "VALUES (:l, :t, :c, now(), 'running', "
            "        '{}'::jsonb, CAST(:p AS jsonb), 'corr-1')"
        ),
        {
            "l": log_id,
            "t": user.tenant_id,
            "c": conn_id,
            "p": '{"jobs": {"processed": 100, "total": 500}}',
        },
    )
    await db.flush()

    resp = await client.get(f"/api/ats/connections/{conn_id}/sync-logs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["progress"] == {"jobs": {"processed": 100, "total": 500}}


@pytest.mark.asyncio
async def test_get_job_statuses_501_when_vendor_unsupported(
    authed_super_admin_client, db
):
    """GET /job-statuses → 501 when adapter raises NotImplementedError.

    Some vendors (Greenhouse uses stages; Workday differs) don't expose a
    job-status concept.  Their adapters raise NotImplementedError, which the
    router translates to 501.  The finally block must still close the adapter.
    """
    from app.modules.ats.connection import ATSConnectionState

    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": user.tenant_id, "ct": b"x", "u": user.id},
    )
    await db.flush()

    fake_adapter = AsyncMock()
    fake_adapter.list_job_statuses = AsyncMock(
        side_effect=NotImplementedError("no status endpoint"),
    )
    fake_adapter.aclose = AsyncMock()

    fake_state = ATSConnectionState(
        id=conn_id,
        tenant_id=user.tenant_id,
        vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
    )

    with patch(
        "app.modules.ats.router.load_connection_state",
        new=AsyncMock(return_value=fake_state),
    ):
        with patch("app.modules.ats.router.get_ats_adapter", return_value=fake_adapter):
            resp = await client.get(f"/api/ats/connections/{conn_id}/job-statuses")

    assert resp.status_code == 501, resp.text
    # The finally block must close the adapter even on NotImplementedError.
    fake_adapter.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_put_job_status_filter_404_on_missing_connection(
    authed_super_admin_client,
):
    """PUT to a non-existent connection returns 404, not silent 204."""
    client, _user = authed_super_admin_client
    bogus_id = uuid.uuid4()
    response = await client.put(
        f"/api/ats/connections/{bogus_id}/job-status-filter",
        json={"status_ids": [1], "names": ["Active"]},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "ATS_CONNECTION_NOT_FOUND"


@pytest.mark.asyncio
async def test_connection_response_exposes_job_status_filter(
    authed_super_admin_client, db
):
    """GET /connections/{id} → response body includes ``job_status_filter``."""
    client, user = authed_super_admin_client
    conn_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by, job_status_filter) "
            "VALUES (:c, :t, 'ceipal', :ct, :u, CAST(:f AS jsonb))"
        ),
        {
            "c": conn_id,
            "t": user.tenant_id,
            "ct": b"x",
            "u": user.id,
            "f": '{"ids": [1], "names": ["Active"]}',
        },
    )
    await db.flush()

    resp = await client.get(f"/api/ats/connections/{conn_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_status_filter"] == {"ids": [1], "names": ["Active"]}
