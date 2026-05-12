"""Load/persist round-trip through the encryption boundary.

Adapted from the plan body (Option B in the task notes): uses the standard
``db`` fixture from ``tests/conftest.py`` for per-test connection-level
transaction rollback, rather than the plan's ``async_session_factory``
which would commit rows directly to the dev DB. The implementation
(``load_connection_state`` / ``persist_connection_state``) takes an
``AsyncSession`` parameter, so swapping fixtures is a pure test-side
change — nothing in the SUT cares about the engine binding.

Why the top-level model import: ``tests/conftest.py::_create_tables``
runs ``Base.metadata.create_all`` once at session start. ORM classes are
registered with ``Base`` only when their module is imported, so we import
the ATS model module here to make sure ``ats_connections`` exists in the
test DB before the first test runs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Force registration of ATS ORM classes with Base.metadata so
# _create_tables in conftest builds the ats_connections table.
from app.modules.ats import models as _ats_models  # noqa: F401


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Install a fresh Fernet key for each test and reset the crypto cache."""
    from app.config import settings
    from app.modules.ats import crypto

    monkeypatch.setattr(
        settings,
        "ats_credentials_encryption_keys",
        [Fernet.generate_key().decode()],
    )
    crypto._fernet = None


@pytest.fixture
async def seeded_connection(db: AsyncSession):
    """Insert tenant + user + ats_connection row via the per-test session.

    Yields ``(tenant_id, conn_id)``. Cleanup is automatic: the ``db`` fixture
    rolls back the connection-level transaction at test teardown.
    """
    from app.modules.ats.crypto import encrypt_credentials_blob

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    creds_ct = encrypt_credentials_blob(
        {"email": "x@y.com", "password": "p", "api_key": "k"}
    )

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
    await db.execute(
        text(
            "INSERT INTO ats_connections "
            "(id, tenant_id, vendor, credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": conn_id, "t": tenant_id, "ct": creds_ct, "u": user_id},
    )
    await db.flush()
    yield (tenant_id, conn_id)


@pytest.mark.asyncio
async def test_load_returns_decrypted_state(db: AsyncSession, seeded_connection):
    from app.modules.ats.connection import load_connection_state

    tenant_id, conn_id = seeded_connection
    state = await load_connection_state(db, conn_id)

    assert state.id == conn_id
    assert state.tenant_id == tenant_id
    assert state.vendor == "ceipal"
    assert state.credentials == {"email": "x@y.com", "password": "p", "api_key": "k"}
    assert state.access_token is None
    assert state.refresh_token is None
    assert state.last_synced_cursors == {}


@pytest.mark.asyncio
async def test_persist_round_trips_mutated_tokens(
    db: AsyncSession, seeded_connection
):
    from app.modules.ats.connection import (
        load_connection_state,
        persist_connection_state,
    )

    _tenant_id, conn_id = seeded_connection
    expires = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    state = await load_connection_state(db, conn_id)
    state.access_token = "new-access-tok"
    state.refresh_token = "new-refresh-tok"
    state.access_token_expires_at = expires
    state.last_synced_cursors = {"clients": expires.isoformat()}
    await persist_connection_state(db, state)
    await db.flush()

    # Expire the ORM identity map so reload re-reads from the DB instead of
    # handing back the same in-memory row we just mutated. Without this the
    # round-trip would be trivially "true" — we want to prove the ciphertext
    # was written and decrypts back.
    db.expunge_all()

    reloaded = await load_connection_state(db, conn_id)
    assert reloaded.access_token == "new-access-tok"
    assert reloaded.refresh_token == "new-refresh-tok"
    assert reloaded.access_token_expires_at == expires
    assert reloaded.last_synced_cursors == {"clients": expires.isoformat()}


@pytest.mark.asyncio
async def test_load_missing_raises_typed_error(db: AsyncSession):
    from app.modules.ats.connection import load_connection_state
    from app.modules.ats.errors import ATSConnectionNotFoundError

    with pytest.raises(ATSConnectionNotFoundError):
        await load_connection_state(db, uuid.uuid4())
