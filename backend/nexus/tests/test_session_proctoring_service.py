"""DB-backed service tests for record_proctoring_event.

Uses the bypass-RLS test session (``db`` fixture from conftest) and the
``seed_minimal_session`` helper which already builds the minimal FK chain
(Client → User → org_unit → job → pipeline instance → stage → candidate →
assignment → session).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.session import service as session_service
from app.modules.session.errors import SessionNotFoundError
from app.modules.session.schemas import SessionState
from tests.conftest import seed_minimal_session

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def make_active_session(db: AsyncSession, tenant_id: uuid.UUID):  # type: ignore[return]
    """Build a sessions row via seed_minimal_session, force state='active',
    and set livekit_room_name so cancel_room has something to call.

    The seed helper creates the full FK graph and returns (session, tenant_id).
    We ignore the returned tenant_id because the caller already has it, but we
    accept ``tenant_id`` here only to mirror the pattern in the task spec — the
    seed helper actually creates a fresh tenant, so what comes back is what we
    should use.  We therefore return BOTH session AND the actual tenant_id so
    callers can align.
    """
    session, actual_tenant_id = await seed_minimal_session(db, state="active")
    session.livekit_room_name = "session-test"
    await db.flush()
    return session, actual_tenant_id


@pytest.fixture(autouse=True)
def _termination_enabled(monkeypatch):
    """Pin PROCTORING_TERMINATION_ENABLED on so the termination-behavior tests
    are hermetic regardless of the developer's local .env. The dry-run tests
    override this to False inside the test body."""
    monkeypatch.setattr(session_service.settings, "proctoring_termination_enabled", True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_violation_terminates(db: AsyncSession, monkeypatch):
    """tab_switch (hard) → terminated=True, state=='terminated', outcome==kind."""
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())

    sess, tenant_id = await make_active_session(db, uuid.uuid4())

    result = await session_service.record_proctoring_event(
        db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="tab_switch",
        occurred_at=datetime.now(UTC),
        correlation_id="cid-hard",
    )

    assert result.terminated is True
    assert result.violation_count == 1
    assert result.already_terminal is False

    await db.refresh(sess)
    assert sess.state == SessionState.TERMINATED.value
    assert sess.proctoring_outcome == "tab_switch"
    assert sess.proctoring_violation_count == 1

    session_service.cancel_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_three_soft_violations_not_terminal(db: AsyncSession, monkeypatch):
    """3 keyboard (soft) violations at default limit=3 → not terminated, state stays active."""
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())

    sess, tenant_id = await make_active_session(db, uuid.uuid4())

    for i in range(3):
        result = await session_service.record_proctoring_event(
            db,
            session_id=sess.id,
            tenant_id=tenant_id,
            kind="keyboard",
            occurred_at=datetime.now(UTC),
            correlation_id=f"cid-soft-{i}",
        )
        assert result.terminated is False, f"Should not terminate on violation {i + 1}"

    await db.refresh(sess)
    assert sess.state == SessionState.ACTIVE.value
    assert sess.proctoring_violation_count == 3
    assert result.violation_count == 3
    assert result.soft_violation_count == 3

    session_service.cancel_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_fourth_soft_violation_terminates(db: AsyncSession, monkeypatch):
    """4th keyboard (soft) violation exceeds default limit=3 → terminated.

    proctoring_outcome should be 'soft_threshold_exceeded'.
    """
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())

    sess, tenant_id = await make_active_session(db, uuid.uuid4())

    # Send 3 non-terminating soft violations
    for i in range(3):
        await session_service.record_proctoring_event(
            db,
            session_id=sess.id,
            tenant_id=tenant_id,
            kind="keyboard",
            occurred_at=datetime.now(UTC),
            correlation_id=f"cid-soft-{i}",
        )

    # 4th should terminate
    result = await session_service.record_proctoring_event(
        db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="keyboard",
        occurred_at=datetime.now(UTC),
        correlation_id="cid-soft-terminal",
    )

    assert result.terminated is True
    assert result.violation_count == 4
    assert result.soft_violation_count == 4

    await db.refresh(sess)
    assert sess.state == SessionState.TERMINATED.value
    assert sess.proctoring_outcome == "soft_threshold_exceeded"
    assert sess.proctoring_violation_count == 4

    session_service.cancel_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_termination_call_is_idempotent(db: AsyncSession, monkeypatch):
    """A call after the session is already terminated → already_terminal=True."""
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())

    sess, tenant_id = await make_active_session(db, uuid.uuid4())

    # First call terminates
    await session_service.record_proctoring_event(
        db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="tab_switch",
        occurred_at=datetime.now(UTC),
        correlation_id="cid-first",
    )

    # Second call after termination
    result = await session_service.record_proctoring_event(
        db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="focus_loss",
        occurred_at=datetime.now(UTC),
        correlation_id="cid-second",
    )

    assert result.terminated is True
    assert result.already_terminal is True
    # violation_count should reflect what was on the row at the time of the
    # second call (1 violation from the first call, second was NOT appended)
    assert result.violation_count == 1


@pytest.mark.asyncio
async def test_wrong_tenant_raises_session_not_found(db: AsyncSession, monkeypatch):
    """Cross-tenant lookup → SessionNotFoundError (same opacity as /state)."""
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())

    sess, _correct_tenant_id = await make_active_session(db, uuid.uuid4())
    wrong_tenant_id = uuid.uuid4()

    with pytest.raises(SessionNotFoundError):
        await session_service.record_proctoring_event(
            db,
            session_id=sess.id,
            tenant_id=wrong_tenant_id,
            kind="tab_switch",
            occurred_at=datetime.now(UTC),
            correlation_id="cid-cross-tenant",
        )


# ---------------------------------------------------------------------------
# _build_proctoring_config — the block embedded into /start, /rejoin, /pre-check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_termination_survives_cancel_room_failure(db: AsyncSession, monkeypatch):
    """If DeleteRoom raises, the session is still marked terminated.

    cancel_room is a best-effort backstop (the candidate's own disconnect
    already closes the agent). A failure must be logged, not propagated.
    """
    monkeypatch.setattr(
        session_service, "cancel_room", AsyncMock(side_effect=RuntimeError("LK down"))
    )
    sess, tenant_id = await make_active_session(db, uuid.uuid4())

    result = await session_service.record_proctoring_event(
        db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="devtools",
        occurred_at=datetime.now(UTC),
        correlation_id="cid-cancel-fail",
    )

    assert result.terminated is True
    await db.refresh(sess)
    assert sess.state == SessionState.TERMINATED.value
    assert sess.proctoring_outcome == "devtools"


@pytest.mark.asyncio
async def test_pre_check_exposes_proctoring_outcome_for_terminated_session(db: AsyncSession):
    """A terminated session's /pre-check carries the proctoring_outcome so a
    reloaded wizard can show the ended screen instead of the cam/mic step."""
    sess, _tenant_id = await seed_minimal_session(db, state="terminated")
    sess.proctoring_outcome = "tab_switch"
    await db.flush()

    resp = await session_service.get_pre_check_context(db, session_id=sess.id)

    assert resp.state.value == "terminated"
    assert resp.proctoring_outcome == "tab_switch"


@pytest.mark.asyncio
async def test_build_proctoring_config_uses_lazy_defaults(db: AsyncSession):
    """A tenant with no tenant_settings row → schema defaults (enabled, 3, 10).

    terminate_enabled coverage lives in test_build_proctoring_config_exposes_terminate_enabled.
    """
    cfg = await session_service._build_proctoring_config(db, uuid.uuid4())
    assert cfg.enabled is True
    assert cfg.soft_violation_limit == 3
    assert cfg.fullscreen_grace_seconds == 10


@pytest.mark.asyncio
async def test_build_proctoring_config_reads_tenant_row(db: AsyncSession):
    """A tenant_settings row's proctoring columns flow into the config block."""
    from app.modules.tenant_settings.models import TenantSettingsModel

    # seed_minimal_session creates a real client (tenant) the FK can reference.
    _sess, tenant_id = await seed_minimal_session(db, state="active")
    db.add(
        TenantSettingsModel(
            tenant_id=tenant_id,
            proctoring_enabled=False,
            proctoring_soft_violation_limit=5,
            proctoring_fullscreen_grace_seconds=20,
        )
    )
    await db.flush()

    cfg = await session_service._build_proctoring_config(db, tenant_id)
    assert cfg.enabled is False
    assert cfg.soft_violation_limit == 5
    assert cfg.fullscreen_grace_seconds == 20


# ---------------------------------------------------------------------------
# PROCTORING_TERMINATION_ENABLED dry-run toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_termination_disabled_hard_kind_keeps_session_active(
    db: AsyncSession, monkeypatch
):
    """PROCTORING_TERMINATION_ENABLED=False → hard kind records violation but
    does NOT terminate: result.terminated is False, session stays active,
    violation count increments, cancel_room is NOT called.
    """
    monkeypatch.setattr(session_service.settings, "proctoring_termination_enabled", False)
    mock_cancel = AsyncMock()
    monkeypatch.setattr(session_service, "cancel_room", mock_cancel)

    sess, tenant_id = await make_active_session(db, uuid.uuid4())

    result = await session_service.record_proctoring_event(
        db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="devtools",
        occurred_at=datetime.now(UTC),
        correlation_id="cid-dry-run",
    )

    # The result must say "not terminated"
    assert result.terminated is False
    assert result.violation_count == 1
    assert result.already_terminal is False

    # The session must remain active in the DB
    await db.refresh(sess)
    assert sess.state == SessionState.ACTIVE.value
    assert sess.proctoring_outcome is None
    assert sess.proctoring_violation_count == 1

    # cancel_room must NOT have been called
    mock_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_termination_enabled_default_hard_kind_terminates(
    db: AsyncSession, monkeypatch
):
    """Regression: with PROCTORING_TERMINATION_ENABLED=True (default), a hard
    kind still terminates normally — the flag must not disturb existing behavior.
    """
    monkeypatch.setattr(session_service.settings, "proctoring_termination_enabled", True)
    monkeypatch.setattr(session_service, "cancel_room", AsyncMock())

    sess, tenant_id = await make_active_session(db, uuid.uuid4())

    result = await session_service.record_proctoring_event(
        db,
        session_id=sess.id,
        tenant_id=tenant_id,
        kind="devtools",
        occurred_at=datetime.now(UTC),
        correlation_id="cid-enabled",
    )

    assert result.terminated is True
    assert result.violation_count == 1

    await db.refresh(sess)
    assert sess.state == SessionState.TERMINATED.value
    assert sess.proctoring_outcome == "devtools"


@pytest.mark.asyncio
async def test_build_proctoring_config_exposes_terminate_enabled(db: AsyncSession, monkeypatch):
    """_build_proctoring_config forwards settings.proctoring_termination_enabled
    as ProctoringConfig.terminate_enabled.
    """
    monkeypatch.setattr(session_service.settings, "proctoring_termination_enabled", False)

    cfg = await session_service._build_proctoring_config(db, uuid.uuid4())
    assert cfg.terminate_enabled is False

    monkeypatch.setattr(session_service.settings, "proctoring_termination_enabled", True)
    cfg2 = await session_service._build_proctoring_config(db, uuid.uuid4())
    assert cfg2.terminate_enabled is True
