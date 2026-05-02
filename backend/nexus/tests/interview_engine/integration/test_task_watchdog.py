"""Integration test: per-task watchdog (asyncio.wait_for) timeout.

The controller wraps each task.run() in ``asyncio.wait_for(...,
timeout=watchdog_seconds)``. When the task takes longer than the
watchdog, the controller force-completes the task with a forced
TaskResult and emits ``task.timeout``.

We exercise the watchdog logic directly via ``_dispatch_task`` with a
TechnicalDepthTask whose ``run()`` is patched to sleep. The controller's
session attribute must be present for downstream calls (``self.session``
on the task base via run_chain). We use a MagicMock session.
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
        tenant_policy="record_only",
    )
    return ctrl, collector


async def test_watchdog_force_completes_when_task_exceeds_timeout(monkeypatch):
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    q0 = next(q for q in cfg.stage.questions if q.id == "q-0")

    # Patch build_task_for to return a fake task whose run() never completes
    # within the watchdog window. The fake task must satisfy the surface
    # the controller uses: .kind, .max_probes, .run(), .force_complete(...)
    fake_task = MagicMock()
    fake_task.kind = "technical_depth"
    fake_task.max_probes = 1

    async def slow_run():
        await asyncio.sleep(2.0)
        return TaskResult(question_id=q0.id, kind="technical_depth")

    fake_task.run = slow_run

    forced_result = TaskResult(
        question_id=q0.id,
        kind="technical_depth",
        forced=True,
        forced_reason="task_timeout",
    )
    fake_task.force_complete = MagicMock(return_value=forced_result)

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
    fake_task.force_complete.assert_called_once_with(reason="task_timeout")


async def test_watchdog_does_not_fire_when_task_completes_promptly(monkeypatch):
    """Sanity inverse: a task that finishes quickly emits no timeout."""
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    q0 = next(q for q in cfg.stage.questions if q.id == "q-0")

    fake_task = MagicMock()
    fake_task.kind = "technical_depth"
    fake_task.max_probes = 1

    async def fast_run():
        await asyncio.sleep(0.001)
        return TaskResult(question_id=q0.id, kind="technical_depth", tier="strong")

    fake_task.run = fast_run
    fake_task.force_complete = MagicMock()

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for",
        lambda q, controller, disqualified_signals: fake_task,
    )

    await ctrl._dispatch_task(q0, watchdog_seconds=5.0)

    assert len(collector.events_of_kind("task.entered")) == 1
    assert len(collector.events_of_kind("task.timeout")) == 0
    fake_task.force_complete.assert_not_called()
