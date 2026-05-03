"""Integration test: per-task watchdog timeout.

The controller awaits each AgentTask directly (it is awaitable via
``__await__``) and runs a sibling watchdog asyncio.Task that calls
``task.force_complete(reason='task_timeout')`` + ``task.complete(...)``
after watchdog_seconds. The forced TaskResult resolves the controller's
``await``; ``task.timeout`` is emitted to the audit log.

We exercise the watchdog logic directly via ``_dispatch_task`` with a
fake awaitable task whose future is never auto-resolved — only the
controller's watchdog can complete it.
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
from app.modules.tenant_settings import TenantSettings
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


pytestmark = pytest.mark.asyncio


def _make_controller():
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
            first_nudge_seconds=30.0,
            second_nudge_seconds=30.0,
            give_up_seconds=30.0,
        ),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
        ),
        tenant_settings=TenantSettings(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )
    return ctrl, collector


class _AwaitableSlowTask:
    """Awaitable fake that NEVER resolves on its own — the controller's
    watchdog must call .complete() to unblock the await, mirroring
    AgentTask's contract."""

    def __init__(self, *, question_id: str, forced_result: TaskResult):
        self.kind = "technical_depth"
        self.max_probes = 1
        self.id = question_id
        self._fut: asyncio.Future = asyncio.Future()
        self._forced_result = forced_result
        self.force_complete_call_count = 0
        self.last_force_complete_kwargs: dict | None = None

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
        self.force_complete_call_count += 1
        self.last_force_complete_kwargs = {"reason": reason}
        return self._forced_result


async def test_watchdog_force_completes_when_task_exceeds_timeout(monkeypatch):
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    q0 = next(q for q in cfg.stage.questions if q.id == "q-0")

    forced_result = TaskResult(
        question_id=q0.id,
        kind="technical_depth",
        forced=True,
        forced_reason="task_timeout",
    )
    fake_task = _AwaitableSlowTask(question_id=q0.id, forced_result=forced_result)

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for",
        lambda q, controller, disqualified_signals: fake_task,
    )

    # Tiny watchdog so the test doesn't take 2s.
    await ctrl._dispatch_task(q0, watchdog_seconds=0.1)

    # task.entered must have fired with watchdog_seconds=0 (int truncation).
    entered = collector.events_of_kind("task.entered")
    assert len(entered) == 1
    assert entered[0].payload["question_id"] == "q-0"
    assert entered[0].payload["kind"] == "technical_depth"
    assert entered[0].payload["watchdog_seconds"] == 0  # int(0.1) == 0

    # task.timeout must have fired.
    timeouts = collector.events_of_kind("task.timeout")
    assert len(timeouts) == 1
    assert timeouts[0].payload["question_id"] == "q-0"
    assert timeouts[0].payload["elapsed_seconds"] == 0  # int(0.1) == 0

    # force_complete called with task_timeout reason.
    assert fake_task.force_complete_call_count == 1
    assert fake_task.last_force_complete_kwargs == {"reason": "task_timeout"}

    # task.completed must have fired with forced=True (timeout path).
    completed_events = collector.events_of_kind("task.completed")
    assert len(completed_events) == 1
    assert completed_events[0].payload["forced"] is True


async def test_watchdog_does_not_fire_when_task_completes_promptly(monkeypatch):
    """Sanity inverse: a task that finishes quickly emits no timeout."""
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    q0 = next(q for q in cfg.stage.questions if q.id == "q-0")

    expected = TaskResult(question_id=q0.id, kind="technical_depth", tier="strong")
    fake_task = _AwaitableSlowTask(question_id=q0.id, forced_result=expected)
    # Pre-resolve on the next loop tick so the await returns immediately.
    loop = asyncio.get_event_loop()
    loop.call_soon(lambda: fake_task.complete(expected) if not fake_task.done() else None)

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for",
        lambda q, controller, disqualified_signals: fake_task,
    )

    await ctrl._dispatch_task(q0, watchdog_seconds=5.0)

    assert len(collector.events_of_kind("task.entered")) == 1
    assert len(collector.events_of_kind("task.timeout")) == 0
    assert fake_task.force_complete_call_count == 0
