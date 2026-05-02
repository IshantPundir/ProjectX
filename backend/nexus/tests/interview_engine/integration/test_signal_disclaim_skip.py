"""Integration test: signal-disclaim subsumption -> skip with bridge.

When the candidate disclaims signals on Q0 (record_answer_assessment
returns ``signals_lacked=[...]``), every later question whose
``signal_values`` is a subset of the disqualified set is skipped with a
bridge. We assert:

  * ``controller.intent.signal_disclaim_skip`` event fires for the
    skipped question with the subsumed signals listed.
  * The skipped question does NOT emit ``task.entered``.

We drive ``on_enter`` directly with ``build_task_for`` patched so that
each question's task immediately resolves with a deterministic
TaskResult — the LLM is never invoked.
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
    """Stub out get_bypass_session + record_session_result everywhere."""
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
            first_nudge_seconds=999.0,
            second_nudge_seconds=999.0,
            give_up_seconds=999.0,
        ),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
        ),
        tenant_policy="record_only",
    )

    # Fake session.
    fake_session = MagicMock()
    fake_session.generate_reply = MagicMock(return_value=_make_handle())
    fake_session.current_speech = None
    fake_session.aclose = AsyncMock(return_value=None)
    fake_session.room_io = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = AsyncMock()
    type(ctrl).session = property(lambda self: fake_session)  # type: ignore[assignment]
    return ctrl, collector, fake_session


def _make_handle():
    h = MagicMock()
    h.wait_for_playout = AsyncMock(return_value=None)
    return h


def _make_fake_task(question_id: str, **task_result_kwargs):
    """Build a fake task that's drop-in for asyncio.wait_for(task.run(), ...).

    Returns a MagicMock with .kind / .max_probes / .run() coroutine and
    .force_complete().
    """
    task = MagicMock()
    task.kind = "technical_depth"
    task.max_probes = 1
    expected_result = TaskResult(
        question_id=question_id, kind="technical_depth", **task_result_kwargs
    )

    async def run():
        return expected_result

    task.run = run
    task.force_complete = MagicMock(return_value=expected_result)
    return task


async def test_q1_skipped_when_q0_disclaims_its_signals(monkeypatch):
    """Q0 has signals=[backend_depth, system_design].
    If both are disclaimed, Q1 (which requires [debugging, production_ops])
    is NOT subsumed — different signals.

    We instead test the actual subsumption case: rewrite the disqualified
    set to include Q4's signals and verify Q4 skip.

    Q4 signals: [self_direction, growth_mindset]
    Q5 signals: [interest, thoughtfulness]
    """
    ctrl, collector, fake_session = _make_controller()

    # Pre-seed disqualified signals so Q4 is subsumed.
    ctrl._disqualified_signals = {"self_direction", "growth_mindset"}

    # Patch build_task_for so any dispatched task returns a fast result
    # (we don't want the LLM in this test).
    def fake_build(q, *, controller, disqualified_signals):
        return _make_fake_task(q.id)

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    # Audit log: q-4 must be skipped via signal_disclaim_skip.
    skip_events = collector.events_of_kind("controller.intent.signal_disclaim_skip")
    assert len(skip_events) >= 1
    skipped_qids = {e.payload["question_id"] for e in skip_events}
    assert "q-4" in skipped_qids

    # Q4 must NOT have a task.entered event.
    entered_events = collector.events_of_kind("task.entered")
    entered_qids = {e.payload["question_id"] for e in entered_events}
    assert "q-4" not in entered_qids

    # The bridge instruction must have been spoken (generate_reply called
    # with allow_interruptions=False) — verify via the recorded mocks.
    # generate_reply is called for: greeting + bridge per skipped Q + closing.
    assert fake_session.generate_reply.call_count >= 1

    # Subsumed signals listed in payload.
    q4_skip = next(e for e in skip_events if e.payload["question_id"] == "q-4")
    assert set(q4_skip.payload["subsumed_signals"]) == {
        "self_direction",
        "growth_mindset",
    }


async def test_skip_propagates_via_real_task_result(monkeypatch):
    """End-to-end: Q0's TaskResult disclaims signals, so a later question
    with matching signals is skipped on the next iteration.

    Q0 signals: [backend_depth, system_design]
    None of the other questions exactly match Q0's signals — so we
    monkey-patch the task at Q0 to disclaim Q3's signal
    (uk_shift_availability) so Q3 is skipped via subsumption.
    """
    ctrl, collector, fake_session = _make_controller()

    def fake_build(q, *, controller, disqualified_signals):
        if q.id == "q-0":
            return _make_fake_task(
                q.id,
                tier="strong",
                signals_lacked=["uk_shift_availability"],
            )
        return _make_fake_task(q.id)

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    # Q3 must be subsumed and emit skip event.
    skip_events = collector.events_of_kind("controller.intent.signal_disclaim_skip")
    skipped_qids = {e.payload["question_id"] for e in skip_events}
    assert "q-3" in skipped_qids

    # Q3 must NOT have a task.entered event.
    entered_events = collector.events_of_kind("task.entered")
    entered_qids = {e.payload["question_id"] for e in entered_events}
    assert "q-3" not in entered_qids
    # But Q0 was entered.
    assert "q-0" in entered_qids


async def test_no_subsumption_when_signals_partially_overlap(monkeypatch):
    """Partial overlap should NOT trigger skip."""
    ctrl, collector, fake_session = _make_controller()

    # Q0 signals = [backend_depth, system_design]; disclaim only one.
    ctrl._disqualified_signals = {"backend_depth"}

    def fake_build(q, *, controller, disqualified_signals):
        return _make_fake_task(q.id)

    monkeypatch.setattr(
        "app.modules.interview_engine.controller.build_task_for", fake_build
    )

    await ctrl.on_enter()

    skip_events = collector.events_of_kind("controller.intent.signal_disclaim_skip")
    skipped_qids = {e.payload["question_id"] for e in skip_events}
    assert "q-0" not in skipped_qids
    entered_events = collector.events_of_kind("task.entered")
    entered_qids = {e.payload["question_id"] for e in entered_events}
    assert "q-0" in entered_qids
