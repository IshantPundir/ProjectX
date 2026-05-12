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


# ---- update_job_status_filter ----

@pytest.fixture
async def connection_for_filter_test(db, basic_tenant):
    """Insert a ats_connections row with NULL job_status_filter and a stale
    jobs cursor so widen-vs-keep can be observed."""
    import json as _json
    tenant_id, user_id = basic_tenant
    conn_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO ats_connections (id, tenant_id, vendor, "
        "credentials_ciphertext, created_by, last_synced_cursors) "
        "VALUES (:c, :t, 'ceipal', :ct, :u, :lc)"
    ), {
        "c": conn_id, "t": tenant_id, "ct": b"x", "u": user_id,
        "lc": _json.dumps({"jobs": "2026-05-10T00:00:00+00:00"}),
    })
    await db.flush()
    return (str(tenant_id), str(user_id), str(conn_id))


@pytest.mark.asyncio
async def test_update_job_status_filter_widen_drops_jobs_cursor(
    db, connection_for_filter_test,
):
    """Widening (any new id) clears last_synced_cursors.jobs so the next
    sync re-pulls from scratch."""
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    # First save: filter from NULL -> [1] (Active). NULL->non-empty counts as widen.
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1], names=["Active"],
    )
    r = await db.execute(text(
        "SELECT job_status_filter, last_synced_cursors FROM ats_connections "
        "WHERE id = :c"
    ), {"c": conn_id})
    row = r.one()
    assert row.job_status_filter == {"ids": [1], "names": ["Active"]}
    assert "jobs" not in row.last_synced_cursors


@pytest.mark.asyncio
async def test_update_job_status_filter_narrow_keeps_jobs_cursor(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    # Seed an existing filter [1, 8].
    await db.execute(text(
        "UPDATE ats_connections SET job_status_filter = :f "
        "WHERE id = :c"
    ), {
        "f": '{"ids": [1, 8], "names": ["Active", "Reactivated"]}',
        "c": conn_id,
    })
    await db.flush()
    # Narrow to [1] -- no new ids -> cursor stays.
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1], names=["Active"],
    )
    r = await db.execute(text(
        "SELECT last_synced_cursors FROM ats_connections WHERE id = :c"
    ), {"c": conn_id})
    cursors = r.scalar_one()
    assert cursors.get("jobs") == "2026-05-10T00:00:00+00:00"


@pytest.mark.asyncio
async def test_update_job_status_filter_no_change_keeps_cursor(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    await db.execute(text(
        "UPDATE ats_connections SET job_status_filter = :f WHERE id = :c"
    ), {"f": '{"ids": [1], "names": ["Active"]}', "c": conn_id})
    await db.flush()
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1], names=["Active"],
    )
    r = await db.execute(text(
        "SELECT last_synced_cursors FROM ats_connections WHERE id = :c"
    ), {"c": conn_id})
    cursors = r.scalar_one()
    assert cursors.get("jobs") == "2026-05-10T00:00:00+00:00"


@pytest.mark.asyncio
async def test_update_job_status_filter_empty_ids_raises(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    with pytest.raises(ValueError, match="non-empty"):
        await update_job_status_filter(
            db, connection_id=uuid.UUID(conn_id),
            tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
            status_ids=[], names=[],
        )


@pytest.mark.asyncio
async def test_update_job_status_filter_length_mismatch_raises(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    with pytest.raises(ValueError, match="length mismatch"):
        await update_job_status_filter(
            db, connection_id=uuid.UUID(conn_id),
            tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
            status_ids=[1, 8], names=["Active"],
        )


@pytest.mark.asyncio
async def test_update_job_status_filter_writes_audit_row(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1, 8], names=["Active", "Reactivated"],
    )
    r = await db.execute(text(
        "SELECT action, payload FROM audit_log "
        "WHERE tenant_id = :t AND action = 'ats.connection.job_status_filter_updated'"
    ), {"t": tenant_id})
    row = r.one()
    assert row.action == "ats.connection.job_status_filter_updated"
    assert row.payload["new_ids"] == [1, 8]
    assert row.payload["widened"] is True


# ---- trigger_manual_sync phase_filter ----

@pytest.mark.asyncio
async def test_trigger_manual_sync_passes_phase_filter_to_actor(
    db, connection_for_filter_test, monkeypatch,
):
    from app.modules.ats import service as service_mod

    tenant_id, user_id, conn_id = connection_for_filter_test
    captured = {}

    class _FakeActor:
        def send(self, *args):
            captured["args"] = args

    monkeypatch.setattr(
        "app.modules.ats.actors.poll_ats_connection", _FakeActor(),
    )

    await service_mod.trigger_manual_sync(
        db,
        uuid.UUID(conn_id),
        uuid.UUID(tenant_id),
        uuid.UUID(user_id),
        phase_filter=["clients", "users"],
    )
    assert captured["args"][2] == ["clients", "users"]
