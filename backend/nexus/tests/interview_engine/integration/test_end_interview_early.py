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


class _AwaitableSimpleTask:
    """Awaitable AgentTask drop-in. Resolves on the next loop tick with
    the supplied default result."""

    def __init__(self, *, default_result: TaskResult):
        self.kind = "technical_depth"
        self.max_probes = 1
        self.id = default_result.question_id
        self._fut: asyncio.Future = asyncio.Future()
        self._default_result = default_result
        loop = asyncio.get_event_loop()
        loop.call_soon(self._resolve)

    def _resolve(self) -> None:
        if not self._fut.done():
            self._fut.set_result(self._default_result)

    def __await__(self):
        return self._fut.__await__()

    def done(self) -> bool:
        return self._fut.done()

    def complete(self, result) -> None:
        if self._fut.done():
            raise RuntimeError("already completed")
        if isinstance(result, Exception):
            self._fut.set_exception(result)
        else:
            self._fut.set_result(result)

    def cancel(self) -> None:
        if self._fut.done():
            return
        self._fut.set_exception(RuntimeError("cancelled"))

    def force_complete(self, *, reason: str) -> TaskResult:
        return self._default_result.model_copy(
            update={"forced": True, "forced_reason": reason}
        )


class _AwaitableEndingTask:
    """Awaitable task that, on first await, fires end_interview_early on
    the controller and then resolves. Mirrors a question-task whose LLM
    detected end-intent mid-question. When end_interview_early calls
    _complete_inflight_task (which calls force_complete + complete), the
    forced result is what the controller's await resolves to.
    """

    def __init__(self, *, controller, default_result: TaskResult):
        self.kind = "technical_depth"
        self.max_probes = 1
        self.id = default_result.question_id
        self._controller = controller
        self._default_result = default_result
        self._fut: asyncio.Future = asyncio.Future()
        # Schedule end_interview_early on the next loop tick. The
        # controller's tool call sequence: end_interview_early ->
        # _complete_inflight_task -> task.force_complete -> task.complete
        # which resolves the future.
        loop = asyncio.get_event_loop()
        loop.call_soon(self._fire_end_intent)

    def _fire_end_intent(self) -> None:
        # end_interview_early is async — schedule it as a task, but do
        # so synchronously so its body executes before the controller
        # awaits the question task.
        async def _runner():
            fake_ctx = MagicMock()
            await self._controller.end_interview_early(
                fake_ctx, reason="candidate_request"
            )
            # If _complete_inflight_task didn't resolve it (e.g. the
            # controller hadn't bound _current_question_task yet), we
            # resolve directly so the test doesn't hang.
            if not self._fut.done():
                self._fut.set_result(self._default_result)

        asyncio.create_task(_runner())

    def __await__(self):
        return self._fut.__await__()

    def done(self) -> bool:
        return self._fut.done()

    def complete(self, result) -> None:
        if self._fut.done():
            return  # tolerate double-complete from concurrent paths
        if isinstance(result, Exception):
            self._fut.set_exception(result)
        else:
            self._fut.set_result(result)

    def cancel(self) -> None:
        if self._fut.done():
            return
        self._fut.set_exception(RuntimeError("cancelled"))

    def force_complete(self, *, reason: str) -> TaskResult:
        return self._default_result.model_copy(
            update={"forced": True, "forced_reason": reason}
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

    # Build a fake task for q-0 that, on first await, schedules
    # end_interview_early and resolves with a TaskResult. The
    # controller's `await task` resolves immediately when the future
    # completes, so we resolve it on the next loop tick.
    expected = TaskResult(question_id="q-0", kind="technical_depth", tier="strong")
    fake_task = _AwaitableEndingTask(controller=ctrl, default_result=expected)

    other_default = TaskResult(question_id="q-other", kind="technical_depth")
    other_task = _AwaitableSimpleTask(default_result=other_default)

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
