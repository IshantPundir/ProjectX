"""Tests for the stuck-session reaper."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.session.models import Session as SessionRow
from app.modules.session.reaper import run_stuck_session_reaper
from tests.conftest import seed_minimal_session


@pytest.fixture(autouse=True)
def _patch_bypass_session(monkeypatch, db):
    """Make the reaper's get_bypass_session() yield the test's db session.

    Mirrors the established pattern from test_question_banks_events.py and
    test_entrypoint_failure.py: the reaper opens an independent connection
    by default, which can't see the test's rollback-isolated transaction.
    """
    @asynccontextmanager
    async def _fake_bypass():
        yield db

    monkeypatch.setattr(
        "app.modules.session.reaper.get_bypass_session",
        _fake_bypass,
    )


@pytest.mark.asyncio
async def test_only_stuck_active_sessions_transition(db):
    """state='active' past threshold -> error. Within threshold stays active.
    Completed stays completed.
    """
    past = datetime.now(UTC) - timedelta(minutes=20)
    recent = datetime.now(UTC) - timedelta(minutes=5)

    stuck, _ = await seed_minimal_session(db, state="active")
    stuck.state_changed_at = past

    fresh, _ = await seed_minimal_session(db, state="active")
    fresh.state_changed_at = recent
    # A live engine pulses recently — the reaper must treat it as alive
    # (clearly inside the stale-pulse threshold, not on its boundary).
    fresh.last_engine_heartbeat_at = datetime.now(UTC) - timedelta(seconds=10)

    done, _ = await seed_minimal_session(db, state="completed")
    done.state_changed_at = past

    await db.flush()

    await run_stuck_session_reaper()

    refreshed_stuck = (await db.execute(
        select(SessionRow).where(SessionRow.id == stuck.id)
    )).scalar_one()
    refreshed_fresh = (await db.execute(
        select(SessionRow).where(SessionRow.id == fresh.id)
    )).scalar_one()
    refreshed_done = (await db.execute(
        select(SessionRow).where(SessionRow.id == done.id)
    )).scalar_one()

    assert refreshed_stuck.state == "error"
    assert refreshed_stuck.error_code == "engine_unresponsive"
    assert refreshed_fresh.state == "active"  # within threshold
    assert refreshed_done.state == "completed"


@pytest.mark.asyncio
async def test_live_long_session_with_fresh_heartbeat_not_reaped(db):
    """A legitimately long interview that is still pulsing is NEVER reaped,
    no matter how long ago it went active. Liveness, not duration, decides."""
    long_ago = datetime.now(UTC) - timedelta(minutes=60)
    fresh_beat = datetime.now(UTC) - timedelta(seconds=5)

    live, _ = await seed_minimal_session(db, state="active")
    live.state_changed_at = long_ago          # active for an hour
    live.last_engine_heartbeat_at = fresh_beat  # but engine pulsed 5s ago
    await db.flush()

    await run_stuck_session_reaper()

    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == live.id)
    )).scalar_one()
    assert refreshed.state == "active"  # alive -> survives


@pytest.mark.asyncio
async def test_dead_engine_stale_heartbeat_is_reaped(db):
    """An engine that pulsed but then died (stale heartbeat past threshold) is
    reaped even though it once showed signs of life."""
    long_ago = datetime.now(UTC) - timedelta(minutes=60)

    dead, _ = await seed_minimal_session(db, state="active")
    dead.state_changed_at = long_ago
    dead.last_engine_heartbeat_at = long_ago  # last pulse an hour ago -> stale
    await db.flush()

    await run_stuck_session_reaper()

    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == dead.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_unresponsive"


@pytest.mark.asyncio
async def test_agent_never_connected_is_reaped(db):
    """No heartbeat ever (dispatch never arrived): falls back to state_changed_at,
    reaped once that ages past the threshold."""
    long_ago = datetime.now(UTC) - timedelta(minutes=60)

    never, _ = await seed_minimal_session(db, state="active")
    never.state_changed_at = long_ago
    never.last_engine_heartbeat_at = None
    await db.flush()

    await run_stuck_session_reaper()

    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == never.id)
    )).scalar_one()
    assert refreshed.state == "error"


@pytest.mark.asyncio
async def test_reaper_writes_audit_row(db):
    past = datetime.now(UTC) - timedelta(minutes=20)
    stuck, _ = await seed_minimal_session(db, state="active")
    stuck.state_changed_at = past
    await db.flush()

    await run_stuck_session_reaper()

    audit = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == stuck.id,
            AuditLog.action == "session.errored",
        )
    )).scalar_one()
    assert audit.payload["error_code"] == "engine_unresponsive"
    assert audit.payload["reason"] == "reaper"


@pytest.mark.asyncio
async def test_reaper_is_idempotent_across_back_to_back_runs(db):
    past = datetime.now(UTC) - timedelta(minutes=20)
    stuck, _ = await seed_minimal_session(db, state="active")
    stuck.state_changed_at = past
    await db.flush()

    await run_stuck_session_reaper()
    await run_stuck_session_reaper()  # second tick — no-op

    audit_rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == stuck.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert len(audit_rows) == 1  # only the first tick wrote the audit


@pytest.mark.asyncio
async def test_concurrent_reapers_advisory_lock(db):
    """Two reaper calls on the same session — only one audit row is written.

    In production the advisory-lock path enforces single-flight across
    replicas. In the test harness both calls share the same patched `db`
    session (connection-level isolation), so asyncio.gather would cause
    concurrent access to a single SQLAlchemy session, which SQLAlchemy 2.0
    does not support. We therefore run the two calls sequentially here.

    Either flow produces the same invariant:
      - "lock contention": the second call sees pg_try_advisory_lock return
        False and returns immediately (no duplicate sweep).
      - "second call is no-op": the first call already transitioned the row
        to 'error'; transition_to_error's WHERE-gate returns False on the
        second call (no duplicate audit row).
    Both flows satisfy: exactly one audit row written.
    """
    past = datetime.now(UTC) - timedelta(minutes=20)
    stuck, _ = await seed_minimal_session(db, state="active")
    stuck.state_changed_at = past
    await db.flush()

    # First call sweeps the stuck row.
    await run_stuck_session_reaper()
    # Second call — either contends on the lock or finds the row already
    # transitioned. Either way: no additional audit row.
    await run_stuck_session_reaper()

    audit_rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == stuck.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert len(audit_rows) == 1  # only one sweep, only one audit
