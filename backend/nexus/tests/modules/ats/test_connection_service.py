"""create_connection: encrypt credentials, test via adapter, persist + audit.

Tests adapted from the plan to use the per-test rollback-isolated ``db``
fixture (Option ii, see ``tests/modules/ats/conftest.py``) instead of the
plan's ``async_session_factory`` pattern — no committed rows, no cleanup.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text


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
async def basic_tenant(db):
    """Seed (tenant, user) on the rollback-isolated db session."""
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
    return (str(tenant_id), str(user_id))


@pytest.mark.asyncio
async def test_create_connection_encrypts_credentials_and_audits(db, basic_tenant):
    from app.modules.ats.service import create_connection

    tenant_id, user_id = basic_tenant

    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake_adapter = AsyncMock()
        fake_adapter.ensure_authenticated = AsyncMock()

        def _bind(state):
            state.access_token = "fresh-access"
            state.refresh_token = "fresh-refresh"
            state.access_token_expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(hours=1)
            )
            state.refresh_token_expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(days=7)
            )
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind

        conn_id = await create_connection(
            db,
            tenant_id=uuid.UUID(tenant_id),
            vendor="ceipal",
            credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
            created_by=uuid.UUID(user_id),
        )

    # Verify the row exists and credentials/tokens are encrypted (not plain).
    r = await db.execute(
        text(
            "SELECT vendor, credentials_ciphertext, access_token_ciphertext, "
            "active FROM ats_connections WHERE id = :i"
        ),
        {"i": conn_id},
    )
    row = r.one()
    assert row.vendor == "ceipal"
    assert b"password" not in row.credentials_ciphertext        # encrypted
    assert b"fresh-access" not in row.access_token_ciphertext   # encrypted
    assert row.active is True


@pytest.mark.asyncio
async def test_create_connection_invalid_credentials_raises(db, basic_tenant):
    """ATSCredentialsInvalidError during test → no DB row inserted."""
    from app.modules.ats.errors import ATSCredentialsInvalidError
    from app.modules.ats.service import create_connection

    tenant_id, user_id = basic_tenant

    with patch("app.modules.ats.service.get_ats_adapter") as mock_get:
        fake_adapter = AsyncMock()
        fake_adapter.ensure_authenticated = AsyncMock(
            side_effect=ATSCredentialsInvalidError("bad password")
        )

        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind

        with pytest.raises(ATSCredentialsInvalidError):
            await create_connection(
                db,
                tenant_id=uuid.UUID(tenant_id),
                vendor="ceipal",
                credentials={
                    "email": "u@x.com", "password": "wrong", "api_key": "k",
                },
                created_by=uuid.UUID(user_id),
            )

    count = await db.execute(
        text("SELECT COUNT(*) FROM ats_connections WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    assert count.scalar_one() == 0
