"""Integration test: _safe_shutdown retries session.aclose with backoff.

Phase 2 controller calls ``_safe_shutdown(self.session, max_attempts=3)``
at the end of _terminate. The helper retries aclose() up to N times,
sleeping with exponential backoff. We patch ``asyncio.sleep`` to a no-op
so the test does not actually wait.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.controller import _safe_shutdown


pytestmark = pytest.mark.asyncio


async def test_succeeds_on_first_attempt():
    session = MagicMock()
    session.aclose = AsyncMock(return_value=None)

    await _safe_shutdown(session, max_attempts=3)

    assert session.aclose.call_count == 1


async def test_retries_then_succeeds(monkeypatch):
    """First two aclose() calls raise; third succeeds. Total 3 attempts."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.asyncio.sleep", fake_sleep
    )

    call_count = {"n": 0}

    async def flaky_aclose():
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError(f"transient failure {call_count['n']}")
        return None

    session = MagicMock()
    session.aclose = flaky_aclose

    await _safe_shutdown(session, max_attempts=3)

    assert call_count["n"] == 3
    # backoff = 0.5 * (2 ** attempt) for attempt in 0..1 -> [0.5, 1.0]
    # Sleeps fire after each FAILED attempt. With 3 attempts and the
    # 3rd succeeding, only 2 sleeps fire (after attempts 0 and 1).
    assert sleeps == [0.5, 1.0]


async def test_exhausts_attempts_when_all_fail(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.asyncio.sleep", fake_sleep
    )

    call_count = {"n": 0}

    async def always_fail():
        call_count["n"] += 1
        raise RuntimeError("permanent failure")

    session = MagicMock()
    session.aclose = always_fail

    # Helper logs error but does NOT raise on exhaustion (graceful close).
    await _safe_shutdown(session, max_attempts=3)

    assert call_count["n"] == 3
    # All 3 attempts failed -> 3 backoff sleeps with the doubling schedule.
    assert sleeps == [0.5, 1.0, 2.0]


async def test_idempotent_persist_only_runs_once(monkeypatch):
    """Sanity: re-entering _terminate while already terminated is a no-op.

    Mirrors the spec's "only 1 persist (idempotency flag)" assertion.
    """
    import uuid
    from app.modules.interview_engine.budget import SessionBudget
    from app.modules.interview_engine.controller import InterviewController
    from app.modules.interview_engine.event_log import EventCollector
    from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
    from app.modules.tenant_settings import TenantSettings
    from tests.interview_engine.fixtures.mock_session_config import (
        load_live_data_session_config,
    )

    cfg = load_live_data_session_config()
    collector = EventCollector(
        session_id=cfg.session_id,
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="test-correlation",
        controller_prompt_hash="sha256:test",
        model_versions={},
        redaction_mode="metadata",
    )
    ctrl = InterviewController(
        session_config=cfg,
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        correlation_id="test-correlation",
        collector=collector,
        idle_nudge_config=IdleNudgeConfig(30.0, 30.0, 30.0),
        budget=SessionBudget(0.0, 900.0),
        tenant_settings=TenantSettings(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )

    # Patch persistence + session attributes used in _terminate.
    record_mock = AsyncMock()
    monkeypatch.setattr(
        "app.modules.interview_engine.controller.record_session_result",
        record_mock,
    )

    fake_db_cm = MagicMock()
    fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock(commit=AsyncMock()))
    fake_db_cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.modules.interview_engine.controller.get_bypass_session",
        lambda: fake_db_cm,
    )

    fake_session = MagicMock()
    fake_session.current_speech = None
    fake_session.aclose = AsyncMock(return_value=None)
    closing_handle = MagicMock()
    closing_handle.wait_for_playout = AsyncMock(return_value=None)
    fake_session.generate_reply = MagicMock(return_value=closing_handle)
    fake_session.room_io = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = AsyncMock()

    type(ctrl).session = property(lambda self: fake_session)  # type: ignore[assignment]

    # Initial state — never started, so no idle-nudge tick to cancel.
    await ctrl._terminate("completed")

    # _terminate is idempotent — second call is a no-op.
    await ctrl._terminate("completed")

    assert record_mock.call_count == 1
    assert ctrl._persisted is True
    assert ctrl._terminated is True
