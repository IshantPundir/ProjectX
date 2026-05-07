import pytest

from app.modules.interview_engine.state.lifecycle import (
    LifecycleState, LifecycleSnapshot, SessionLifecycle, SessionOutcome,
)
from app.modules.interview_runtime.schemas import KnockoutFailure


def test_initial_state_pre_start():
    lc = SessionLifecycle(time_budget_total_seconds=600.0)
    assert lc.snapshot().state == LifecycleState.pre_start


def test_transition_to_active():
    lc = SessionLifecycle(time_budget_total_seconds=600.0)
    lc.transition_to_active()
    assert lc.snapshot().state == LifecycleState.active


def test_transition_to_active_from_active_raises():
    lc = SessionLifecycle(time_budget_total_seconds=600.0)
    lc.transition_to_active()
    with pytest.raises(ValueError):
        lc.transition_to_active()


def test_record_knockout():
    lc = SessionLifecycle(time_budget_total_seconds=600.0)
    lc.record_knockout(KnockoutFailure(
        question_id="q1", reason="missing JQL skill",
        signal_values=["JQL fluency"], occurred_at_ms=1500,
    ))
    snap = lc.snapshot()
    assert len(snap.knockout_failures) == 1
    assert snap.has_knockout() is True


def test_time_elapsed_tracking():
    lc = SessionLifecycle(time_budget_total_seconds=600.0)
    lc.transition_to_active()
    lc.set_time_elapsed(45.5)
    snap = lc.snapshot()
    assert snap.time_elapsed_seconds == 45.5
    assert snap.time_remaining_seconds() == 600.0 - 45.5


def test_time_exhausted():
    lc = SessionLifecycle(time_budget_total_seconds=10.0)
    lc.set_time_elapsed(11.0)
    assert lc.snapshot().time_exhausted() is True


def test_outcome_resolution_completed():
    lc = SessionLifecycle(time_budget_total_seconds=600.0)
    lc.transition_to_active()
    lc.set_last_outcome(SessionOutcome.completed)
    lc.transition_to_closing()
    lc.transition_to_closed()
    assert lc.snapshot().last_outcome == SessionOutcome.completed
    assert lc.snapshot().state == LifecycleState.closed


def test_outcome_resolution_knockout_closed():
    lc = SessionLifecycle(time_budget_total_seconds=600.0)
    lc.record_knockout(KnockoutFailure(
        question_id="q1", reason="missing X",
        signal_values=["X"], occurred_at_ms=500,
    ))
    lc.set_last_outcome(SessionOutcome.knockout_closed)
    assert lc.snapshot().last_outcome == SessionOutcome.knockout_closed


def test_session_outcome_values():
    """Frontend has 6; backend must produce all 6 + error in v1."""
    expected = {
        "completed", "knockout_closed", "time_expired",
        "candidate_ended", "candidate_disconnected",
        "candidate_unresponsive", "error",
    }
    assert {o.value for o in SessionOutcome} == expected
