"""Integration test: close_polite policy sets _end_outcome="knockout_closed".

Phase 5 / Task 8 — three scenarios:

A. record_only — knockout fires, controller continues (no _end_outcome
   set). The in-memory _knockout_failures list grows; the loop carries
   on.

B. close_polite — knockout fires, _end_outcome is set to
   "knockout_closed", controller.intent.knockout_closed event fires.
   Termination itself runs at the loop's natural convergence point;
   _handle_task_result no longer schedules _terminate.

C. Empty-reason guard (T7) — knockout_reason="" → log warning + skip
   append + skip event log, regardless of policy.
"""

from __future__ import annotations

import uuid

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


def _build_controller(policy: str) -> tuple[InterviewController, EventCollector]:
    """Mirror test_disqualify_knockout.py's _make_controller pattern.

    `policy` is "record_only" or "close_polite".
    """
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
            engine_knockout_policy=policy,  # type: ignore[arg-type]
        ),
    )
    return ctrl, collector


def _knockout_result(
    question_id: str,
    reason: str = "Cannot work UK shift hours.",
) -> TaskResult:
    return TaskResult(
        question_id=question_id,
        kind="technical_depth",
        tier="below_bar",
        knockout=True,
        knockout_reason=reason,
    )


# --- Scenario A: record_only ---


async def test_record_only_continues() -> None:
    ctrl, collector = _build_controller("record_only")

    # Q3 in live_data is the UK-shift question.
    q3 = next(q for q in ctrl._config.stage.questions if q.id == "q-3")
    ctrl._handle_task_result(q3, _knockout_result(q3.id))

    assert len(ctrl._knockout_failures) == 1
    assert ctrl._knockout_failures[0].question_id == "q-3"
    assert "UK shift" in ctrl._knockout_failures[0].reason
    # record_only must NOT short-circuit the loop.
    assert ctrl._end_outcome is None

    # No knockout_closed event should appear.
    assert collector.events_of_kind("controller.intent.knockout_closed") == []
    # But disqualify.knockout DOES appear.
    assert len(collector.events_of_kind("disqualify.knockout")) == 1


# --- Scenario B: close_polite ---


async def test_close_polite_sets_end_outcome() -> None:
    """The close_polite branch sets _end_outcome and returns.

    The question loop's next-iteration check (`if self._end_outcome is
    not None: break`) breaks before dispatching q_{n+1}; the natural
    convergence call (`await self._terminate(self._end_outcome or
    "completed")`) handles termination synchronously. No asyncio.create_task,
    no race window where q_{n+1} could be dispatched before _terminate ran.
    """
    ctrl, _collector = _build_controller("close_polite")

    q3 = next(q for q in ctrl._config.stage.questions if q.id == "q-3")
    ctrl._handle_task_result(q3, _knockout_result(q3.id))

    assert len(ctrl._knockout_failures) == 1
    assert ctrl._end_outcome == "knockout_closed"


async def test_close_polite_emits_event() -> None:
    ctrl, collector = _build_controller("close_polite")

    q3 = next(q for q in ctrl._config.stage.questions if q.id == "q-3")
    ctrl._handle_task_result(q3, _knockout_result(q3.id))

    assert len(collector.events_of_kind("disqualify.knockout")) == 1
    knockout_closed_events = collector.events_of_kind(
        "controller.intent.knockout_closed"
    )
    assert len(knockout_closed_events) == 1
    assert knockout_closed_events[0].payload["question_id"] == "q-3"


# --- Scenario C: empty-reason guard ---


@pytest.mark.parametrize("empty_value", ["", "   ", "\t\n"])
async def test_empty_reason_skips_append_and_event(empty_value: str) -> None:
    """The guard fires regardless of policy. Use record_only here for
    minimum coupling to other behavior; the guard runs before the
    policy branch."""
    ctrl, collector = _build_controller("record_only")

    q3 = next(q for q in ctrl._config.stage.questions if q.id == "q-3")
    ctrl._handle_task_result(q3, _knockout_result(q3.id, reason=empty_value))

    # No append; no event log entries; no _end_outcome change.
    assert ctrl._knockout_failures == []
    assert collector.events_of_kind("disqualify.knockout") == []
    assert collector.events_of_kind("controller.intent.knockout_closed") == []
    assert ctrl._end_outcome is None
