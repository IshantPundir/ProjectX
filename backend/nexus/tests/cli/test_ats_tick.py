"""Scheduler tick enqueues poll_ats_connection for every due connection,
stamps poll_lock_acquired_at, and skips connections that are not yet due.

Test-environment choice: Option (ii) — monkeypatch the
``async_session_factory`` symbol in ``app.cli.ats_tick`` to yield the
rollback-isolated ``db`` fixture session (shimming ``commit`` → ``flush``
for the duration of the patched context). Mirrors the
``patched_bypass_session`` fixture in ``tests/modules/ats/conftest.py``:
prior ATS tasks adopted the same pattern, and it keeps test writes off the
dev DB without forcing explicit cleanup. The CLI itself still calls the
real ``async_session_factory`` in production.

We also patch the ``app.cli.ats_tick.poll_ats_connection`` symbol so no
Dramatiq message ever hits Redis. The mock collects ``.send()`` calls so
the test can assert which (connection_id, tenant_id) tuples were enqueued.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

# Force registration of ATS ORM classes with Base.metadata so the
# session-scoped _create_tables fixture in tests/conftest.py builds the
# ats_connections table in the test DB before the first test runs.
from app.modules.ats import models as _ats_models  # noqa: F401


@pytest.fixture
def patched_ats_tick_session(db, monkeypatch):
    """Make ``app.cli.ats_tick.async_session_factory()`` yield the
    rollback-isolated ``db`` session instead of opening a new dev-DB session.

    The CLI does::

        async with async_session_factory() as session:
            async with session.begin():
                ...
                await session.commit()

    The test session is already inside a transaction (see ``db`` fixture in
    tests/conftest.py). Calling ``session.begin()`` on it would normally
    raise — but SQLAlchemy AsyncSession's ``begin()`` returns a savepoint /
    nested transaction context when called inside an existing transaction,
    so this works. We swap ``commit`` → ``flush`` for the duration of the
    patched context so the outer test rollback still cleans up everything
    the CLI wrote.
    """
    from app.cli import ats_tick as ats_tick_mod

    @asynccontextmanager
    async def _noop_txn():
        # The test ``db`` session is already inside a transaction (the
        # ``db`` fixture wrapped it in one for rollback isolation). The
        # CLI's ``async with session.begin():`` would raise
        # InvalidRequestError on an already-active session, so the shim
        # makes ``begin()`` a no-op context manager.
        yield

    @asynccontextmanager
    async def _fake_session_factory():
        original_commit = db.commit
        original_begin = db.begin
        db.commit = db.flush  # type: ignore[method-assign]
        db.begin = _noop_txn  # type: ignore[method-assign]
        try:
            yield db
        finally:
            db.commit = original_commit  # type: ignore[method-assign]
            db.begin = original_begin  # type: ignore[method-assign]

    monkeypatch.setattr(ats_tick_mod, "async_session_factory", _fake_session_factory)
    return db


@pytest.fixture
async def due_connections_fixture(db, patched_ats_tick_session):
    """Three connections (A and B are due; C is not yet due) across three
    tenants — the ``ats_connections`` table has a UNIQUE constraint on
    ``(tenant_id, vendor)``, so we can't co-locate three Ceipal connections
    under the same tenant. The CLI doesn't care about tenancy here: it
    SELECTs across all tenants under a bypass-RLS session.

    Returns the set of all seeded tenant IDs plus the per-label connection
    UUID map, so the test that verifies lock-stamping can scope its SELECT
    to just those rows (the dev DB may carry rows from prior runs).
    """
    user_id = uuid.uuid4()
    ids = {"A": uuid.uuid4(), "B": uuid.uuid4(), "C": uuid.uuid4()}
    tenants = {label: uuid.uuid4() for label in ids}
    now = datetime.now(tz=timezone.utc)

    for label, due in [
        ("A", now - timedelta(minutes=5)),
        ("B", now - timedelta(minutes=1)),
        ("C", now + timedelta(minutes=10)),
    ]:
        tenant_id = tenants[label]
        await db.execute(
            text("INSERT INTO clients (id, name) VALUES (:t, :n)"),
            {"t": tenant_id, "n": f"tenant-{label}"},
        )
        await db.execute(
            text(
                "INSERT INTO users (id, email, tenant_id, auth_user_id) "
                "VALUES (:u, :e, :t, :a)"
            ),
            {
                "u": user_id, "e": f"u-{label}@x.com",
                "t": tenant_id, "a": uuid.uuid4(),
            },
        )
        await db.execute(
            text(
                "INSERT INTO ats_connections "
                "(id, tenant_id, vendor, credentials_ciphertext, "
                "created_by, next_poll_at) "
                "VALUES (:i, :t, 'ceipal', :ct, :u, :n)"
            ),
            {"i": ids[label], "t": tenant_id, "ct": b"x", "u": user_id, "n": due},
        )
        # Each iteration uses a fresh user_id so the test does not depend on
        # the partial-unique index on users.auth_user_id behaving in any
        # particular way across re-inserts in the same txn.
        user_id = uuid.uuid4()
    await db.flush()

    yield (tenants, ids)


@pytest.mark.asyncio
async def test_tick_enqueues_only_due_connections(due_connections_fixture):
    from app.cli.ats_tick import run_tick

    _tenants, ids = due_connections_fixture
    enqueued: list[tuple] = []
    with patch("app.cli.ats_tick.poll_ats_connection") as mock_actor:
        mock_actor.send = lambda *a, **k: enqueued.append(a)
        await run_tick()

    enqueued_ids = {a[0] for a in enqueued}
    assert str(ids["A"]) in enqueued_ids
    assert str(ids["B"]) in enqueued_ids
    assert str(ids["C"]) not in enqueued_ids


@pytest.mark.asyncio
async def test_tick_stamps_poll_lock_acquired_at(
    db, due_connections_fixture
):
    from app.cli.ats_tick import run_tick

    _tenants, ids = due_connections_fixture
    with patch("app.cli.ats_tick.poll_ats_connection") as mock_actor:
        mock_actor.send = lambda *a, **k: None
        await run_tick()

    seeded_ids = [str(ids[label]) for label in ("A", "B", "C")]
    r = await db.execute(
        text(
            "SELECT id::text AS id, poll_lock_acquired_at "
            "FROM ats_connections WHERE id::text = ANY(:ids)"
        ),
        {"ids": seeded_ids},
    )
    rows = {row.id: row.poll_lock_acquired_at for row in r}
    assert rows[str(ids["A"])] is not None
    assert rows[str(ids["B"])] is not None
    assert rows[str(ids["C"])] is None  # not picked up
