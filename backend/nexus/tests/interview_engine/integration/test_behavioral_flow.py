"""Integration test: controller dispatches a behavioral_star question correctly.

Pattern mirrors test_controller_flow.py — patches build_task_for to return
an awaitable fake task that resolves with a behavioral TaskResult. Asserts:
  - task.entered payload carries kind="behavioral_star" and max_probes=2
  - task.completed payload carries star_components dict
  - watchdog uses behavioral budget (no cap)

Note on fixture mutation: load_live_data_session_config() returns a
SessionConfig whose QuestionConfig objects are Pydantic v2 models with no
model_config = ConfigDict(frozen=True), so direct attribute assignment
(q.question_kind = "behavioral_star") is safe. The fixture is re-loaded
per-test-instance via _make_controller_with_fake_session(), so the mutation
does not bleed across tests.
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


class _BehavioralAwaitableFakeTask:
    """Awaitable fake with kind='behavioral_star' and max_probes=2."""

    def __init__(self, *, question_id: str, default_result: TaskResult):
        self.kind = "behavioral_star"
        self.max_probes = 2
        self._fut: asyncio.Future = asyncio.Future()
        self._default_result = default_result
        self.id = question_id
        self.force_complete_calls: list[dict] = []

    def __await__(self):
        return self._fut.__await__()

    def done(self) -> bool:
        return self._fut.done()

    def complete(self, result) -> None:
        if self._fut.done():
            raise RuntimeError("already completed")
        self._fut.set_result(result)

    def cancel(self) -> None:
        if self._fut.done():
            return
        self._fut.set_exception(RuntimeError("cancelled"))

    def force_complete(self, *, reason: str) -> TaskResult:
        self.force_complete_calls.append({"reason": reason})
        return self._default_result.model_copy(
            update={"forced": True, "forced_reason": reason}
        )


def _make_behavioral_fake(question_id: str, result: TaskResult) -> _BehavioralAwaitableFakeTask:
    task = _BehavioralAwaitableFakeTask(question_id=question_id, default_result=result)
    loop = asyncio.get_event_loop()
    loop.call_soon(lambda: task.complete(result) if not task.done() else None)
    return task


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
    # Override one question to be behavioral_star so it routes to the fake.
    # QuestionConfig has no model_config=ConfigDict(frozen=True), so direct
    # assignment is safe. The fixture is reloaded per test call, so this
    # mutation is per-test-instance only.
    cfg.stage.questions[2].question_kind = "behavioral_star"
    # Use redaction_mode="full" so task.completed payloads retain "result".
    # The redaction module strips "result" from task.completed in metadata
    # mode (spec §5.2 — it's a content field). This test needs to inspect
    # result.star_components, so full mode is required here.
    collector = EventCollector(
        session_id=cfg.session_id,
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="test-correlation",
        controller_prompt_hash="sha256:test",
        model_versions={},
        redaction_mode="full",
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


async def test_behavioral_question_dispatch_carries_kind_and_components(
    monkeypatch, patch_persistence
):
    """A behavioral_star question fires task.entered with kind=behavioral_star
    and task.completed with the star_components dict."""
    ctrl, collector, fake_session = _make_controller_with_fake_session()

    captured_kinds = []

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "behavioral_star":
            result = TaskResult(
                question_id=q.id,
                kind="behavioral_star",
                star_components={
                    "situation": "Last year",
                    "task": "Lead the migration",
                    "action": "Broke into chunks",
                    "result": "Shipped on time",
                },
                probes_fired=0,
            )
            return _make_behavioral_fake(q.id, result)
        # Other questions still resolve with technical_depth fakes.
        from tests.interview_engine.integration.test_controller_flow import (
            _make_awaitable_fake_task,
        )
        captured_kinds.append("technical_depth")
        return _make_awaitable_fake_task(
            q.id,
            TaskResult(question_id=q.id, kind="technical_depth", tier="strong"),
        )

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    entered = collector.events_of_kind("task.entered")
    behavioral_entered = [e for e in entered if e.payload["kind"] == "behavioral_star"]
    assert len(behavioral_entered) == 1
    assert behavioral_entered[0].payload["max_probes"] == 2

    completed = collector.events_of_kind("task.completed")
    behavioral_completed = [e for e in completed if e.payload["result_kind"] == "behavioral_star"]
    assert len(behavioral_completed) == 1
    star = behavioral_completed[0].payload["result"]["star_components"]
    assert star["situation"] == "Last year"
    assert star["result"] == "Shipped on time"


async def test_behavioral_watchdog_uses_estimated_minutes_no_cap(
    monkeypatch, patch_persistence
):
    """Behavioral has no hard cap — watchdog reflects estimated_minutes * 60 + overhead."""
    ctrl, collector, _ = _make_controller_with_fake_session()
    cfg = ctrl._config
    behavioral_q = cfg.stage.questions[2]  # the one we marked behavioral_star
    expected_min_seconds = behavioral_q.estimated_minutes * 60.0
    # ContextSpy: capture watchdog_seconds passed to _dispatch_task
    seen_watchdogs: dict[str, float] = {}
    original_dispatch = ctrl._dispatch_task

    async def capturing_dispatch(q, *, watchdog_seconds):
        seen_watchdogs[q.id] = watchdog_seconds
        await original_dispatch(q, watchdog_seconds=watchdog_seconds)

    ctrl._dispatch_task = capturing_dispatch  # type: ignore[method-assign]

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "behavioral_star":
            return _make_behavioral_fake(
                q.id,
                TaskResult(
                    question_id=q.id, kind="behavioral_star",
                    star_components={"situation": "x", "task": "y", "action": "z", "result": "w"},
                ),
            )
        from tests.interview_engine.integration.test_controller_flow import (
            _make_awaitable_fake_task,
        )
        return _make_awaitable_fake_task(
            q.id,
            TaskResult(question_id=q.id, kind="technical_depth", tier="strong"),
        )

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    behavioral_watchdog = seen_watchdogs[behavioral_q.id]
    assert behavioral_watchdog >= expected_min_seconds
    # Definitely not capped — should be much greater than 60s for a multi-minute question.
    assert behavioral_watchdog > 60.0
