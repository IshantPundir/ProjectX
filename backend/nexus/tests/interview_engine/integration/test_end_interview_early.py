"""Integration test: end_interview_early intent path.

The controller exposes ``end_interview_early`` as a ``@function_tool``.
When the LLM calls it (typically in response to "I'd like to end the
interview now."), the controller must:

  1. Emit ``controller.intent.end_early`` with reason="candidate_request".
  2. Set ``self._end_outcome = "candidate_ended"``.
  3. Cancel any in-flight task.
  4. Allow the outer ``on_enter`` loop to converge into ``_terminate``.

We invoke the tool directly (not through real LLM) because LLM intent
classification is non-deterministic. The tool's behavior is the
correctness property; the LLM's classification accuracy is covered by
the prompt-quality suite (Phase 2 Task 13).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.interview_engine.tasks.base import TaskResult
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def patch_persistence(monkeypatch):
    record_mock = AsyncMock(return_value=None)
    fake_db = MagicMock()
    fake_db.commit = AsyncMock()

    def fake_session_cm():
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=fake_db)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.record_session_result",
        record_mock,
    )
    monkeypatch.setattr(
        "app.modules.interview_engine.controller.get_bypass_session",
        fake_session_cm,
    )
    return record_mock


def _make_controller_with_fake_session():
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
        idle_nudge_config=IdleNudgeConfig(999.0, 999.0, 999.0),
        budget=SessionBudget(0.0, 900.0),
        tenant_policy="record_only",
    )
    fake_session = MagicMock()
    handle = MagicMock()
    handle.wait_for_playout = AsyncMock(return_value=None)
    fake_session.generate_reply = MagicMock(return_value=handle)
    fake_session.current_speech = None
    fake_session.aclose = AsyncMock(return_value=None)
    fake_session.room_io = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = AsyncMock()
    type(ctrl).session = property(lambda self: fake_session)  # type: ignore[assignment]
    return ctrl, collector, fake_session


async def test_tool_emits_audit_event_with_reason_candidate_request():
    ctrl, collector, _ = _make_controller_with_fake_session()
    fake_ctx = MagicMock()

    result = await ctrl.end_interview_early(fake_ctx, reason="candidate_request")

    # Tool returns a brief instruction for the LLM to ack.
    assert "Okay" in result

    events = collector.events_of_kind("controller.intent.end_early")
    assert len(events) == 1
    assert events[0].payload["reason"] == "candidate_request"


async def test_tool_sets_end_outcome_and_cancels_current_task():
    ctrl, collector, _ = _make_controller_with_fake_session()
    fake_ctx = MagicMock()

    # Simulate a running task by attaching a real asyncio.Task that
    # sleeps long enough for cancel to fire.
    async def long_task():
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            raise

    ctrl._current_task_run = asyncio.create_task(long_task())

    await ctrl.end_interview_early(fake_ctx, reason="candidate_request")

    # _end_outcome must be set.
    assert ctrl._end_outcome == "candidate_ended"

    # Current task must have been cancelled.
    await asyncio.sleep(0)  # let the cancel propagate
    assert ctrl._current_task_run.cancelled() or ctrl._current_task_run.done()


async def test_full_on_enter_loop_converges_after_end_early(monkeypatch, patch_persistence):
    """Drive on_enter; first dispatched task calls end_interview_early
    immediately. The loop must terminate cleanly into _terminate.
    """
    ctrl, collector, fake_session = _make_controller_with_fake_session()

    # Build a fake task for q-0 that, when run(), fires end_interview_early
    # via the controller and returns a TaskResult.
    async def first_task_run():
        fake_ctx = MagicMock()
        await ctrl.end_interview_early(fake_ctx, reason="candidate_request")
        return TaskResult(question_id="q-0", kind="technical_depth", tier="strong")

    fake_task = MagicMock()
    fake_task.kind = "technical_depth"
    fake_task.max_probes = 1
    fake_task.run = first_task_run
    fake_task.force_complete = MagicMock(
        return_value=TaskResult(question_id="q-0", kind="technical_depth")
    )

    other_task = MagicMock()
    other_task.kind = "technical_depth"
    other_task.max_probes = 1

    async def other_run():
        return TaskResult(question_id="q-other", kind="technical_depth")

    other_task.run = other_run
    other_task.force_complete = MagicMock()

    def fake_build(q, *, controller, disqualified_signals):
        if q.id == "q-0":
            return fake_task
        return other_task

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    # Audit event present.
    end_events = collector.events_of_kind("controller.intent.end_early")
    assert len(end_events) == 1

    # Controller terminated with the right outcome.
    assert ctrl._end_outcome == "candidate_ended"
    assert ctrl._terminated is True

    # Persistence ran exactly once.
    assert patch_persistence.call_count == 1
