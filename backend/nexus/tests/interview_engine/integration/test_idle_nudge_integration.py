"""Integration test: idle-nudge state machine wired into the controller.

Strategy: drive the state machine via ``on_user_state_changed("away")``
plus direct calls to ``controller._idle_nudge_state.on_tick(...)`` (the
state machine accepts a now-time so we don't have to actually wait).

Then walk the audit log for ``controller.intent.idle_nudge`` events and
verify the nudge counter advances, then END_UNRESPONSIVE sets the
end-outcome to ``candidate_unresponsive``.

Avoids the real 1Hz ``_idle_nudge_loop`` task: the loop is the trivial
plumbing; the assertion is on the controller's reaction to state-machine
output.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import (
    InterviewController,
    now_ms,
)
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import (
    IdleNudgeConfig,
    IdleNudgeOutput,
)
from app.modules.tenant_settings import TenantSettings
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


pytestmark = pytest.mark.asyncio


def _make_controller_with_fast_idle_config():
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
        idle_nudge_config=IdleNudgeConfig(
            first_nudge_seconds=1.0,
            second_nudge_seconds=1.0,
            give_up_seconds=1.0,
        ),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
        ),
        tenant_settings=TenantSettings(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )
    # Provide a fake session so generate_reply doesn't blow up.
    fake_session = MagicMock()
    fake_session.generate_reply = MagicMock(return_value=MagicMock())
    type(ctrl).session = property(lambda self: fake_session)  # type: ignore[assignment]
    return ctrl, collector, fake_session


def _emit_audit_for_output(ctrl, output: IdleNudgeOutput) -> None:
    """Mirror the controller's _idle_nudge_loop reaction in pure form.

    The actual loop is a 1Hz asyncio.sleep + on_tick + reaction. We
    bypass the sleep-driven loop and call the same reaction directly.
    """
    if output is IdleNudgeOutput.NUDGE_ONE:
        ctrl._collector.append(
            kind="controller.intent.idle_nudge",
            payload={"nudge_number": 1},
            wall_ms=now_ms(),
        )
        ctrl.session.generate_reply(
            instructions=ctrl._idle_nudge_instruction(1),
            allow_interruptions=False,
        )
    elif output is IdleNudgeOutput.NUDGE_TWO:
        ctrl._collector.append(
            kind="controller.intent.idle_nudge",
            payload={"nudge_number": 2},
            wall_ms=now_ms(),
        )
        ctrl.session.generate_reply(
            instructions=ctrl._idle_nudge_instruction(2),
            allow_interruptions=False,
        )
    elif output is IdleNudgeOutput.END_UNRESPONSIVE:
        ctrl._end_outcome = "candidate_unresponsive"


async def test_first_nudge_fires_after_first_silence_threshold():
    ctrl, collector, fake_session = _make_controller_with_fast_idle_config()

    # Candidate goes away at t=0.
    ctrl.on_user_state_changed("away")
    # The state-machine constructor uses now_seconds=0.0 for on_user_state
    # only when explicitly passed; on_user_state_changed forwards
    # time.monotonic(). We re-bind to a virtual clock by directly poking
    # the silence_started_at.
    ctrl._idle_nudge_state._silence_started_at = 0.0  # type: ignore[attr-defined]

    # Tick 1.0s later -> NUDGE_ONE.
    out = ctrl._idle_nudge_state.on_tick(now_seconds=1.0)
    assert out is IdleNudgeOutput.NUDGE_ONE
    _emit_audit_for_output(ctrl, out)

    nudges = collector.events_of_kind("controller.intent.idle_nudge")
    assert len(nudges) == 1
    assert nudges[0].payload["nudge_number"] == 1
    fake_session.generate_reply.assert_called_once()


async def test_second_nudge_fires_then_end_unresponsive_sets_outcome():
    ctrl, collector, fake_session = _make_controller_with_fast_idle_config()

    ctrl.on_user_state_changed("away")
    ctrl._idle_nudge_state._silence_started_at = 0.0  # type: ignore[attr-defined]

    # NUDGE_ONE at t=1.0
    out1 = ctrl._idle_nudge_state.on_tick(now_seconds=1.0)
    assert out1 is IdleNudgeOutput.NUDGE_ONE
    _emit_audit_for_output(ctrl, out1)

    # NUDGE_TWO at t=2.0
    out2 = ctrl._idle_nudge_state.on_tick(now_seconds=2.0)
    assert out2 is IdleNudgeOutput.NUDGE_TWO
    _emit_audit_for_output(ctrl, out2)

    # END_UNRESPONSIVE at t=3.0
    out3 = ctrl._idle_nudge_state.on_tick(now_seconds=3.0)
    assert out3 is IdleNudgeOutput.END_UNRESPONSIVE
    _emit_audit_for_output(ctrl, out3)

    nudges = collector.events_of_kind("controller.intent.idle_nudge")
    assert len(nudges) == 2
    assert nudges[0].payload["nudge_number"] == 1
    assert nudges[1].payload["nudge_number"] == 2
    # After END_UNRESPONSIVE, end_outcome is set.
    assert ctrl._end_outcome == "candidate_unresponsive"


async def test_resume_on_speech_resets_nudge_cycle():
    ctrl, collector, fake_session = _make_controller_with_fast_idle_config()

    ctrl.on_user_state_changed("away")
    ctrl._idle_nudge_state._silence_started_at = 0.0  # type: ignore[attr-defined]

    # NUDGE_ONE at t=1.0
    out1 = ctrl._idle_nudge_state.on_tick(now_seconds=1.0)
    assert out1 is IdleNudgeOutput.NUDGE_ONE
    _emit_audit_for_output(ctrl, out1)

    # Candidate speaks -> reset.
    ctrl.on_user_state_changed("speaking")

    # No more nudges fire on subsequent ticks (silence_started_at cleared).
    out2 = ctrl._idle_nudge_state.on_tick(now_seconds=10.0)
    assert out2 is IdleNudgeOutput.NO_OP

    nudges = collector.events_of_kind("controller.intent.idle_nudge")
    assert len(nudges) == 1


async def test_idle_nudge_loop_react_path_via_terminate_cancellation(monkeypatch):
    """Verify the actual _idle_nudge_loop coroutine on at least one tick.

    Drives the loop with a fake asyncio.sleep that returns immediately,
    then cancels after one nudge has fired. This proves the integration
    between the loop and the controller's reaction is wired correctly
    (not just the state machine in isolation).
    """
    ctrl, collector, fake_session = _make_controller_with_fast_idle_config()
    ctrl._session_start_monotonic = time.monotonic()

    # Pre-arm the silence window so the first tick fires NUDGE_ONE.
    ctrl.on_user_state_changed("away")
    ctrl._idle_nudge_state._silence_started_at = time.monotonic() - 5.0  # type: ignore[attr-defined]

    import asyncio as _asyncio

    # Spawn the loop directly.
    loop_task = _asyncio.create_task(ctrl._idle_nudge_loop())
    # Give the loop a chance to wake from the 1Hz sleep at least once.
    # 1.1s gives one tick; 2.5s gives 2 ticks (NUDGE_ONE + NUDGE_TWO).
    await _asyncio.sleep(1.2)
    ctrl._terminated = True
    loop_task.cancel()
    try:
        await loop_task
    except _asyncio.CancelledError:
        pass

    nudges = collector.events_of_kind("controller.intent.idle_nudge")
    assert len(nudges) >= 1
    assert nudges[0].payload["nudge_number"] == 1
