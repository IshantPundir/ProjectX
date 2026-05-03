"""Integration test: disqualify_knockout records and continues (record_only).

Tenant policy ``record_only`` accumulates knockout failures but never
breaks the loop. Phase 5 will introduce ``close_polite``.

Strategy: drive the controller's ``_handle_task_result`` directly with a
TaskResult that has ``knockout=True``. This is the controller's surface
for the knockout side effect — the tool itself only mutates partial
state, then ``complete_question`` produces the TaskResult that the
controller folds in. We assert:

  * ``disqualify.knockout`` audit event fires with question_id + reason.
  * Controller's _knockout_failures list grows by one.
  * Controller does NOT set _end_outcome (record_only).
  * Loop is free to continue (no end_outcome means subsequent dispatch
    iterations would proceed).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

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


async def test_knockout_records_audit_event_and_does_not_break_loop():
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    # Q3 in live_data is the UK-shift question.
    q3 = next(q for q in cfg.stage.questions if q.id == "q-3")

    result = TaskResult(
        question_id=q3.id,
        kind="technical_depth",
        tier="below_bar",
        knockout=True,
        knockout_reason="cannot work UK shift hours",
    )
    ctrl._handle_task_result(q3, result)

    events = collector.events_of_kind("disqualify.knockout")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["question_id"] == "q-3"
    # `reason` is stripped under metadata-mode redaction; only the length
    # metadata survives. The full reason is preserved on the in-memory
    # _knockout_failures record below.
    assert payload["reason_chars"] == len("cannot work UK shift hours")
    assert "reason" not in payload

    # In-memory record updated.
    assert len(ctrl._knockout_failures) == 1
    record = ctrl._knockout_failures[0]
    assert record.question_id == "q-3"
    assert record.reason == "cannot work UK shift hours"
    assert record.signal_values == ["uk_shift_availability"]

    # Loop must remain free to continue (record_only): no end outcome.
    assert ctrl._end_outcome is None


async def test_knockout_signals_lacked_propagates_into_disqualified_set():
    """Signals lacked still propagate even on a knockout result."""
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    q3 = next(q for q in cfg.stage.questions if q.id == "q-3")

    result = TaskResult(
        question_id=q3.id,
        kind="technical_depth",
        tier="below_bar",
        signals_lacked=["uk_shift_availability"],
        knockout=True,
        knockout_reason="cannot work UK shift hours",
    )
    ctrl._handle_task_result(q3, result)

    assert "uk_shift_availability" in ctrl._disqualified_signals


async def test_multiple_knockouts_accumulate_under_record_only():
    """Two knockouts on different questions both record + neither breaks."""
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    q0 = next(q for q in cfg.stage.questions if q.id == "q-0")
    q3 = next(q for q in cfg.stage.questions if q.id == "q-3")

    ctrl._handle_task_result(
        q0,
        TaskResult(
            question_id=q0.id,
            kind="technical_depth",
            knockout=True,
            knockout_reason="reason a",
        ),
    )
    ctrl._handle_task_result(
        q3,
        TaskResult(
            question_id=q3.id,
            kind="technical_depth",
            knockout=True,
            knockout_reason="reason b",
        ),
    )

    events = collector.events_of_kind("disqualify.knockout")
    assert len(events) == 2
    assert len(ctrl._knockout_failures) == 2
    assert ctrl._end_outcome is None


async def test_non_knockout_result_does_not_record_event():
    """Sanity: a normal TaskResult (knockout=False) emits no audit row."""
    ctrl, collector = _make_controller()
    cfg = ctrl._config
    q0 = next(q for q in cfg.stage.questions if q.id == "q-0")

    ctrl._handle_task_result(
        q0,
        TaskResult(
            question_id=q0.id,
            kind="technical_depth",
            tier="strong",
            knockout=False,
        ),
    )

    events = collector.events_of_kind("disqualify.knockout")
    assert len(events) == 0
    assert len(ctrl._knockout_failures) == 0
