"""Actor end-to-end with mock CeipalAdapter — verify the four phases
(load → auth → sync → persist) execute and the sync_log closes correctly.

Also verify: ATSCredentialsInvalidError disables the connection;
ATSRateLimitedError finalizes a partial sync_log and exits cleanly (no raise);
ATSTransientError re-raises so Dramatiq retries.

Auto-sync was removed (no scheduler) — the actor no longer advances
next_poll_at on either the success or rate-limit path. The recruiter
re-triggers manually when ready.

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
async def test_auth_phase_non_credential_error_finalizes_sync_log(db, actor_fixture):
    """Production case: Phase B raises something other than
    ATSCredentialsInvalidError (e.g. ATSVendorContractError when Ceipal's
    refreshToken returns 200 with a non-JSON body and the in-adapter
    fallback hasn't been deployed yet, OR a transient network blip).

    Pre-fix behavior: the exception escaped uncaught; Dramatiq retried
    three times, each retry created a NEW sync_log, none of them got
    finalized, and the UI was left polling a 'running' row forever
    ('Counting jobs…' stuck).

    Post-fix behavior: the actor finalizes the current sync_log as
    'failed' BEFORE re-raising. The connection stays active (creds may
    well be fine — this is a vendor/network issue, not a creds issue).
    """
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSVendorContractError

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock(
        side_effect=ATSVendorContractError(
            "refreshToken returned 200 with non-JSON body",
        ),
    )

    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        with pytest.raises(ATSVendorContractError):
            await actors._run_poll(connection_id, tenant_id)

    # The sync_log must have been finalized as 'failed', not left
    # 'running' for the UI poll to fixate on.
    log_status = (await db.execute(text(
        "SELECT status, error_phase FROM ats_sync_logs "
        "WHERE connection_id = :c ORDER BY started_at DESC LIMIT 1"
    ), {"c": connection_id})).one()
    assert log_status.status == "failed"
    assert log_status.error_phase == "auth"

    # Connection MUST stay active — creds may be fine; this is a
    # vendor/network failure, not a creds-revoked situation.
    conn_status = (await db.execute(text(
        "SELECT active FROM ats_connections WHERE id = :i"
    ), {"i": connection_id})).scalar_one()
    assert conn_status is True


@pytest.mark.asyncio
async def test_rate_limited_finalizes_partial_returns_cleanly(db, actor_fixture):
    """ATSRateLimitedError → finalize sync_log as partial, return cleanly.

    next_poll_at is NOT shifted: auto-sync was removed, so there is no
    scheduler to honor the retry-after window. The recruiter re-triggers
    manually when ready.
    """
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSRateLimitedError

    tenant_id, connection_id = actor_fixture
    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()

    # Snapshot next_poll_at before the run so we can prove it didn't move.
    pre = (await db.execute(text(
        "SELECT next_poll_at FROM ats_connections WHERE id = :i"
    ), {"i": connection_id})).scalar_one()

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

    # Sync log finalized as 'partial' (the actor's rate-limit branch path).
    log_status = (await db.execute(text(
        "SELECT status FROM ats_sync_logs WHERE connection_id = :c "
        "ORDER BY started_at DESC LIMIT 1"
    ), {"c": connection_id})).scalar_one()
    assert log_status == "partial"

    # next_poll_at unchanged — no scheduler to honor anyway.
    post = (await db.execute(text(
        "SELECT next_poll_at FROM ats_connections WHERE id = :i"
    ), {"i": connection_id})).scalar_one()
    assert post == pre


@pytest.mark.asyncio
async def test_rate_limited_records_partial_progress_in_sync_log(db, actor_fixture):
    """When sync_tenant attaches a partial SyncResult to ATSRateLimitedError,
    the actor's rate-limit handler must propagate that partial result into
    ats_sync_logs.entity_counts so the UI doesn't misreport "nothing imported".

    Pins the contract that fixes the production observability bug:
    32 clients + 19 users were getting imported per run, but entity_counts
    showed all-nulls because the actor was passing _empty_partial_result()
    instead of the real partial.
    """
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSRateLimitedError
    from app.modules.ats.importer import PhaseResult

    tenant_id, connection_id = actor_fixture
    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()

    # Simulate "phases 1-3 succeeded, phase 4 hit rate limit"
    rl_exc = ATSRateLimitedError(retry_after_seconds=60)
    from app.modules.ats.importer import SyncResult
    partial = SyncResult()
    partial.clients = PhaseResult(new=32)
    partial.users = PhaseResult(new=19)
    partial.jobs = PhaseResult(new=5, updated=2)
    # Phases 4 + 5 never ran → stay None.
    rl_exc.partial_result = partial  # type: ignore[attr-defined]

    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        with patch.object(actors.ATSImporter, "sync_tenant", side_effect=rl_exc):
            await actors._run_poll(connection_id, tenant_id)

    r = await db.execute(text(
        "SELECT entity_counts FROM ats_sync_logs "
        "WHERE connection_id = :c ORDER BY started_at DESC LIMIT 1"
    ), {"c": connection_id})
    counts = r.scalar_one()
    # Phases that completed are recorded; phases that didn't run are null.
    assert counts["clients"]["new"] == 32
    assert counts["users"]["new"] == 19
    assert counts["jobs"]["new"] == 5
    assert counts["jobs"]["updated"] == 2
    assert counts["applicants"] is None
    assert counts["submissions"] is None


@pytest.mark.asyncio
async def test_connection_deleted_before_actor_runs_exits_cleanly(db, actor_fixture):
    """Connection deleted between scheduler tick and actor execution.

    The actor's Phase A calls load_connection_state which raises
    ATSConnectionNotFoundError when the row is gone. The top-level handler
    in _run_poll must catch this and return cleanly so Dramatiq does NOT
    retry (the cascade deletion guarantees there is nothing left to do).

    Verifies the recruiter-mid-sync-delete scenario does not produce an
    infinite retry storm.
    """
    from app.modules.ats import actors

    tenant_id, connection_id = actor_fixture

    # Simulate "recruiter deleted the connection" between scheduler tick
    # (which enqueued the message) and actor execution.
    await db.execute(text(
        "DELETE FROM ats_connections WHERE id = :i"
    ), {"i": connection_id})
    await db.flush()

    # Must NOT raise. The top-level handler converts ATSConnectionNotFoundError
    # to a clean return so Dramatiq won't retry.
    await actors._run_poll(connection_id, tenant_id)


@pytest.mark.asyncio
async def test_connection_deleted_mid_sync_during_phase_c_exits_cleanly(
    db, actor_fixture,
):
    """Connection deleted AFTER Phase A's sync_log row is created.

    Reproduces the production incident: Phase A inserts + commits sync_log,
    Phase B + Phase C run, then the recruiter clicks "Remove connection"
    in the middle of Phase C, the importer hits a rate limit (or any
    other error path), and the actor's recovery handlers try to operate
    on the now-cascade-deleted sync_log row.

    finalize_sync_log_partial must gracefully no-op when the row is gone
    (defensive None guard); persist_connection_state in Phase D raises
    ATSConnectionNotFoundError which the top-level handler catches.
    Either way, no exception escapes — no Dramatiq retry.
    """
    from app.modules.ats import actors
    from app.modules.ats.errors import ATSRateLimitedError

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()

    async def _sync_then_delete_and_raise(self_, adapter, *, phase_filter=None, sync_log_id=None):
        # Phase A has already committed the sync_log row by this point.
        # Simulate the recruiter clicking "Remove connection" in the UI:
        # the cascade wipes ats_sync_logs, ats_*_mappings, etc.
        await db.execute(text(
            "DELETE FROM ats_connections WHERE id = :i"
        ), {"i": connection_id})
        await db.flush()
        raise ATSRateLimitedError(retry_after_seconds=60)

    with patch("app.modules.ats.actors.get_ats_adapter") as mock_get:
        def _bind(state):
            fake_adapter.state = state
            return fake_adapter
        mock_get.side_effect = _bind
        with patch.object(
            actors.ATSImporter, "sync_tenant", _sync_then_delete_and_raise,
        ):
            # Must NOT raise — the rate-limit handler's finalize_sync_log_partial
            # gracefully no-ops on the cascade-deleted row.
            await actors._run_poll(connection_id, tenant_id)

    # Connection is gone (cascade clean). No retry was scheduled.
    r = await db.execute(text(
        "SELECT COUNT(*) FROM ats_connections WHERE id = :i"
    ), {"i": connection_id})
    assert r.scalar_one() == 0


@pytest.mark.asyncio
async def test_actor_passes_phase_filter_and_sync_log_id_to_importer(
    db, actor_fixture,
):
    """phase_filter (list on the wire) is converted to a set and passed
    to ATSImporter.sync_tenant, along with the sync_log_id created by
    Phase A."""
    from app.modules.ats import actors

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()
    fake_adapter.list_clients = lambda since=None: _empty_aiter()
    fake_adapter.list_users   = lambda since=None: _empty_aiter()
    fake_adapter.list_jobs    = lambda since=None, *, job_status_ids=None: _empty_aiter()
    fake_adapter.list_applicants = lambda since=None: _empty_aiter()
    fake_adapter.list_submissions = lambda job_external_id, since=None: _empty_aiter()

    captured = {}
    async def fake_sync_tenant(self, adapter, *, phase_filter=None, sync_log_id=None):
        from app.modules.ats.importer import SyncResult
        captured["phase_filter"] = phase_filter
        captured["sync_log_id"] = sync_log_id
        return SyncResult()

    with patch(
        "app.modules.ats.actors.get_ats_adapter",
        return_value=fake_adapter,
    ), patch.object(
        actors.ATSImporter, "sync_tenant", fake_sync_tenant,
    ):
        await actors._run_poll(
            connection_id, tenant_id, phase_filter=["clients", "users"],
        )

    assert captured["phase_filter"] == {"clients", "users"}
    assert captured["sync_log_id"] is not None  # Phase A created the row


@pytest.mark.asyncio
async def test_actor_default_phase_filter_is_none(db, actor_fixture):
    """Calling _run_poll without phase_filter forwards None to the importer."""
    from app.modules.ats import actors

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()
    fake_adapter.list_clients = lambda since=None: _empty_aiter()
    fake_adapter.list_users = lambda since=None: _empty_aiter()
    fake_adapter.list_jobs = lambda since=None, *, job_status_ids=None: _empty_aiter()
    fake_adapter.list_applicants = lambda since=None: _empty_aiter()
    fake_adapter.list_submissions = lambda job_external_id, since=None: _empty_aiter()

    captured = {}
    async def fake_sync_tenant(self, adapter, *, phase_filter=None, sync_log_id=None):
        from app.modules.ats.importer import SyncResult
        captured["phase_filter"] = phase_filter
        return SyncResult()

    with patch(
        "app.modules.ats.actors.get_ats_adapter",
        return_value=fake_adapter,
    ), patch.object(
        actors.ATSImporter, "sync_tenant", fake_sync_tenant,
    ):
        await actors._run_poll(connection_id, tenant_id)

    assert captured["phase_filter"] is None
