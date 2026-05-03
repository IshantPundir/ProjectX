"""Integration test: controller dispatches compliance_binary correctly.

Asserts:
  - watchdog uses 60s cap regardless of estimated_minutes (live data has est=1.0;
    with 5s overhead the base would be 65s, which is capped to 60s)
  - knockout pairing produces both task.completed (compliance_confirmed=False)
    AND a disqualify.knockout audit event

Note on redaction_mode: both tests assert on content fields — "result" in
task.completed and "reason" in disqualify.knockout. Both are stripped in
metadata mode (spec §5.2). Use redaction_mode="full" here so the assertions
can inspect those fields directly.
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


class _ComplianceAwaitableFakeTask:
    """Awaitable fake with kind='compliance_binary' and max_probes=0."""

    def __init__(self, *, question_id: str, default_result: TaskResult):
        self.kind = "compliance_binary"
        self.max_probes = 0
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


def _make_compliance_fake(question_id: str, result: TaskResult) -> _ComplianceAwaitableFakeTask:
    task = _ComplianceAwaitableFakeTask(question_id=question_id, default_result=result)
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


def _make_controller_with_compliance_question():
    cfg = load_live_data_session_config()
    # Live data Q3 (UK shift) is the natural compliance candidate.
    # Actual fixture has estimated_minutes=1.0; with 5s overhead the base
    # budget is 65s, which is above the 60s compliance cap — so the cap fires.
    cfg.stage.questions[3].question_kind = "compliance_binary"
    # Use redaction_mode="full" so task.completed payloads retain "result"
    # and disqualify.knockout retains "reason". Both are content fields
    # stripped in metadata mode (spec §5.2). The assertions below inspect
    # result.compliance_confirmed and knockout reason, so full mode is
    # required here.
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
        tenant_settings=TenantSettings(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ),
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


async def test_compliance_watchdog_capped_at_60_regardless_of_estimated_minutes(
    monkeypatch, patch_persistence
):
    """Compliance has 60s hard cap; live-data Q3 has est=1.0 so base (with
    5s overhead) would be 65s — confirmed capped to exactly 60s."""
    ctrl, collector, _ = _make_controller_with_compliance_question()
    compliance_q = ctrl._config.stage.questions[3]
    # Sanity: cap actually constrains — base (est*60 + 5s overhead) must exceed 60s.
    # At est=1.0: base=65s > cap=60s. Accept any value > 0.9 min to stay robust.
    assert compliance_q.estimated_minutes >= 0.9
    seen_watchdogs: dict[str, float] = {}
    original_dispatch = ctrl._dispatch_task

    async def capturing_dispatch(q, *, watchdog_seconds):
        seen_watchdogs[q.id] = watchdog_seconds
        await original_dispatch(q, watchdog_seconds=watchdog_seconds)

    ctrl._dispatch_task = capturing_dispatch  # type: ignore[method-assign]

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "compliance_binary":
            return _make_compliance_fake(
                q.id,
                TaskResult(
                    question_id=q.id, kind="compliance_binary",
                    compliance_confirmed=True,
                    compliance_reason_or_example="Confirmed without further context",
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

    compliance_watchdog = seen_watchdogs[compliance_q.id]
    assert compliance_watchdog == 60.0


async def test_compliance_knockout_pairing_emits_both_audit_events(
    monkeypatch, patch_persistence
):
    """A compliance 'no' on a hard requirement produces task.completed AND
    disqualify.knockout audit events."""
    ctrl, collector, _ = _make_controller_with_compliance_question()

    def fake_build(q, *, controller, disqualified_signals):
        if q.question_kind == "compliance_binary":
            return _make_compliance_fake(
                q.id,
                TaskResult(
                    question_id=q.id, kind="compliance_binary",
                    compliance_confirmed=False,
                    compliance_reason_or_example="No; UK shift not feasible",
                    knockout=True,
                    knockout_reason="Cannot work UK shift",
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

    completed = collector.events_of_kind("task.completed")
    compliance_completed = [
        e for e in completed if e.payload["result_kind"] == "compliance_binary"
    ]
    assert len(compliance_completed) == 1
    assert compliance_completed[0].payload["result"]["compliance_confirmed"] is False

    # The controller's _handle_task_result wires knockout=True into a
    # disqualify.knockout audit event.
    knockouts = collector.events_of_kind("disqualify.knockout")
    assert len(knockouts) >= 1
    assert any(e.payload["reason"] == "Cannot work UK shift" for e in knockouts)
