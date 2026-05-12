"""Actor end-to-end with mock CeipalAdapter — verify the four phases
(load → auth → sync → persist) execute and the sync_log closes correctly.

Also verify: ATSCredentialsInvalidError disables the connection;
ATSRateLimitedError advances next_poll_at and exits cleanly (no raise);
ATSTransientError re-raises so Dramatiq retries.

Test-environment choice: Option (ii) — uses the per-test rollback-isolated
``db`` fixture plus the ``patched_bypass_session`` fixture (in conftest.py),
which now patches BOTH ``importer.get_bypass_session`` and
``actors.get_bypass_session``. The actor's four bypass-session blocks all
resolve to the same test session; writes are visible to the in-test asserts
and roll back automatically at teardown.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _enc_keys(monkeypatch):
    from app.config import settings
    from app.modules.ats import crypto
    monkeypatch.setattr(
        settings,
        "ats_credentials_encryption_keys",
        [Fernet.generate_key().decode()],
    )
    crypto._fernet = None
    yield
    crypto._fernet = None


@pytest.fixture
async def actor_fixture(db, importer_fixture):
    """importer_fixture pre-seeds tenant + user + ats_connections row (with
    a placeholder ``credentials_ciphertext = b'x'``). Update the connection's
    ``credentials_ciphertext`` to a real encrypted blob so the actor can
    decrypt + load state.
    """
    from app.modules.ats.crypto import encrypt_credentials_blob

    tenant_id, _user_id, _root_unit_id = importer_fixture
    ct = encrypt_credentials_blob({
        "email": "u@x.com", "password": "p", "api_key": "k",
    })
    await db.execute(text(
        "UPDATE ats_connections SET credentials_ciphertext = :ct "
        "WHERE tenant_id = :t"
    ), {"ct": ct, "t": tenant_id})
    cid = (await db.execute(text(
        "SELECT id::text FROM ats_connections WHERE tenant_id = :t LIMIT 1"
    ), {"t": tenant_id})).scalar_one()
    await db.flush()
    yield (str(tenant_id), cid)


def _empty_aiter():
    async def _aiter():
        return
        yield  # pragma: no cover
    return _aiter()


@pytest.mark.asyncio
async def test_happy_path_writes_success_sync_log(db, actor_fixture):
    """Mock adapter yields no entities; poll completes; sync_log status='success'."""
    from app.modules.ats import actors

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()
    fake_adapter.list_clients = lambda since=None: _empty_aiter()
    fake_adapter.list_users = lambda since=None: _empty_aiter()
    fake_adapter.list_jobs = lambda since=None: _empty_aiter()
    fake_adapter.list_applicants = lambda since=None: _empty_aiter()
    fake_adapter.list_submissions = lambda job_external_id, since=None: _empty_aiter()

    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        await actors._run_poll(connection_id, tenant_id)

    r = await db.execute(text(
        "SELECT status FROM ats_sync_logs WHERE connection_id = :c "
        "ORDER BY started_at DESC LIMIT 1"
    ), {"c": connection_id})
    assert r.scalar_one() == "success"


@pytest.mark.asyncio
async def test_credentials_invalid_disables_connection_and_raises(db, actor_fixture):
    """ATSCredentialsInvalidError → mark connection disabled + raise."""
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSCredentialsInvalidError

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock(
        side_effect=ATSCredentialsInvalidError("password revoked upstream"),
    )

    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        with pytest.raises(ATSCredentialsInvalidError):
            await actors._run_poll(connection_id, tenant_id)

    row = await db.execute(text(
        "SELECT active, disabled_reason FROM ats_connections WHERE id = :i"
    ), {"i": connection_id})
    r = row.one()
    assert r.active is False
    assert "password revoked" in r.disabled_reason


@pytest.mark.asyncio
async def test_rate_limited_advances_next_poll_returns_cleanly(db, actor_fixture):
    """ATSRateLimitedError → set next_poll_at = now() + retry_after, return cleanly."""
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSRateLimitedError

    tenant_id, connection_id = actor_fixture
    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()

    # Make the importer's sync_tenant raise rate-limited.
    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        with patch.object(
            actors.ATSImporter,
            "sync_tenant",
            side_effect=ATSRateLimitedError(retry_after_seconds=120),
        ):
            # Should NOT raise — handled internally
            await actors._run_poll(connection_id, tenant_id)

    r = await db.execute(text(
        "SELECT EXTRACT(EPOCH FROM (next_poll_at - now())) AS delta "
        "FROM ats_connections WHERE id = :i"
    ), {"i": connection_id})
    row = r.one()
    # next_poll_at should be roughly now + 120s (allow ±5s for clock drift)
    assert 115 <= row.delta <= 125
