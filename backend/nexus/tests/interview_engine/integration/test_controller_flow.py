"""Integration test: controller dispatches the live-data bank end-to-end.

The plan's literal sketch drives this via a real ``AgentSession`` with a
cheap LLM + ``mock_tools(TechnicalDepthTask, ...)``. Real LLM-driven
flows are non-deterministic (the LLM may chat for several turns before
calling ``complete_question``); flakiness on a 6-question bank with a
real LLM was high in early local runs.

This stubbed variant exercises the same controller surface — entered/
completed event ordering, signal accumulation, terminate path, single
shutdown — by patching ``build_task_for`` so each task immediately
resolves via ``await task.run()`` with a deterministic ``TaskResult``.
The LLM never participates. Real-LLM verification is left for the
prompt-quality suite (Task 13) and the end-to-end checklist (Task 16),
where flakiness is acceptable and the cost amortizes.

The Phase 1 ``session.close`` listener is exercised in
``tests/interview_engine/test_event_log_integration.py``; this file
asserts on the controller's audit emissions (``task.entered`` /
``task.completed`` are emitted by the task itself + the controller for
the entry side, but the technical_depth task only emits at the log
level inside ``complete_question`` — the controller's audit event is
``task.entered``).
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


async def test_six_question_flow_completes_cleanly_and_persists_once(
    monkeypatch, patch_persistence
):
    """Controller dispatches 6 mocked tasks, each resolves to a TaskResult.

    Asserts:
      * task.entered fires 6 times in order.
      * No task.timeout, no skip events, no end_early.
      * Persistence runs exactly once.
      * session.aclose called.
      * _terminated is True at the end.
    """
    ctrl, collector, fake_session = _make_controller_with_fake_session()

    # Each fake task immediately resolves with a "strong" TaskResult so
    # no signals are disqualified, no skips, no knockouts.
    def fake_build(q, *, controller, disqualified_signals):
        task = MagicMock()
        task.kind = "technical_depth"
        task.max_probes = 1

        async def run():
            return TaskResult(
                question_id=q.id,
                kind="technical_depth",
                tier="strong",
                evidence_keys=["e1", "e2"],
                signals_lacked=[],
                non_answer=False,
            )

        task.run = run
        task.force_complete = MagicMock()
        return task

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    # 6 task.entered events, in question order.
    entered = collector.events_of_kind("task.entered")
    assert len(entered) == 6
    qids_in_order = [e.payload["question_id"] for e in entered]
    assert qids_in_order == ["q-0", "q-1", "q-2", "q-3", "q-4", "q-5"]

    # No skips, no timeouts, no end_early, no knockouts.
    assert collector.events_of_kind("task.timeout") == []
    assert collector.events_of_kind("controller.intent.signal_disclaim_skip") == []
    assert collector.events_of_kind("controller.intent.end_early") == []
    assert collector.events_of_kind("disqualify.knockout") == []

    # Persistence ran exactly once.
    assert patch_persistence.call_count == 1
    assert ctrl._persisted is True
    assert ctrl._terminated is True

    # Outcome: completed (default).
    # session.aclose called at least once via _safe_shutdown.
    assert fake_session.aclose.call_count >= 1


async def test_signals_lacked_propagates_across_questions(monkeypatch):
    """If Q0 disclaims signals, later questions see them in disqualified_signals."""
    ctrl, collector, fake_session = _make_controller_with_fake_session()

    seen_disq = {}

    def fake_build(q, *, controller, disqualified_signals):
        seen_disq[q.id] = set(disqualified_signals)
        task = MagicMock()
        task.kind = "technical_depth"
        task.max_probes = 1

        async def run():
            if q.id == "q-0":
                return TaskResult(
                    question_id=q.id,
                    kind="technical_depth",
                    tier="below_bar",
                    signals_lacked=["uk_shift_availability"],
                )
            return TaskResult(question_id=q.id, kind="technical_depth", tier="strong")

        task.run = run
        task.force_complete = MagicMock()
        return task

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    # Q0 saw an empty disqualified set.
    assert seen_disq["q-0"] == set()
    # By the time Q3 was reached, the disclaimer set already had the signal.
    # But since Q3's signals subset == disqualified set, Q3 is skipped before
    # build_task_for is called. So we shouldn't see q-3 in seen_disq.
    assert "q-3" not in seen_disq

    # And the skip event fires for q-3.
    skip_events = collector.events_of_kind("controller.intent.signal_disclaim_skip")
    assert any(e.payload["question_id"] == "q-3" for e in skip_events)


async def test_outcome_completed_emitted_when_loop_exits_naturally(monkeypatch):
    """Default outcome on loop completion is 'completed'."""
    ctrl, collector, fake_session = _make_controller_with_fake_session()

    def fake_build(q, *, controller, disqualified_signals):
        task = MagicMock()
        task.kind = "technical_depth"
        task.max_probes = 1

        async def run():
            return TaskResult(question_id=q.id, kind="technical_depth", tier="strong")

        task.run = run
        task.force_complete = MagicMock()
        return task

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    # When the loop exits without setting _end_outcome, _terminate is called
    # with "completed" (the default).
    assert ctrl._end_outcome is None  # never explicitly set during a clean run
    # The room.local_participant.set_attributes call (publish_session_outcome)
    # is the side-effect that records the outcome.
    set_attrs = fake_session.room_io.room.local_participant.set_attributes
    # set_attributes was called twice: once with progress, once with outcome.
    outcome_calls = [
        c for c in set_attrs.call_args_list
        if c.args and "session_outcome" in c.args[0]
    ]
    assert len(outcome_calls) == 1
    assert outcome_calls[0].args[0]["session_outcome"] == "completed"
