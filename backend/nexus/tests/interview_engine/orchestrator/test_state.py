"""Tests for InterviewState — phase transition allowlist, exit-mode
stamping, reconnect counter, sequence-number monotonicity."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.interview_engine.orchestrator import (
    ExitMode,
    InterviewPhase,
    InterviewPhaseError,
    InterviewState,
    QuestionState,
)


def _new_state(**overrides) -> InterviewState:
    base = dict(
        session_id="sess-1",
        tenant_id="tenant-1",
        job_id="job-1",
        candidate_id="cand-1",
        started_at=datetime.now(UTC),
        target_duration_seconds=900,
    )
    base.update(overrides)
    return InterviewState(**base)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_state_initial_phase_is_connecting():
    state = _new_state()
    assert state.phase == InterviewPhase.CONNECTING
    assert state.exit_mode is None
    assert state.ended_at is None
    assert state.sequence_number == 0
    assert state.reconnect_count == 0
    assert state.max_reconnects == 2


def test_state_target_duration_must_be_positive():
    with pytest.raises(ValueError):
        _new_state(target_duration_seconds=0)


# ---------------------------------------------------------------------------
# Phase transitions — happy path
# ---------------------------------------------------------------------------


def test_connecting_to_consent_to_intro_to_main_loop():
    state = _new_state()
    state.transition(InterviewPhase.CONSENT)
    assert state.phase == InterviewPhase.CONSENT
    state.transition(InterviewPhase.INTRO)
    state.transition(InterviewPhase.MAIN_LOOP)
    assert state.phase == InterviewPhase.MAIN_LOOP
    assert state.sequence_number == 3


def test_main_loop_to_normal_wrap_to_closed():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    state.transition(InterviewPhase.NORMAL_WRAP)
    state.transition(InterviewPhase.CLOSED)
    assert state.phase == InterviewPhase.CLOSED


def test_main_loop_to_knockout_confirmation_to_early_exit_wrap():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    state.transition(InterviewPhase.KNOCKOUT_CONFIRMATION)
    state.transition(InterviewPhase.EARLY_EXIT_WRAP)
    state.transition(InterviewPhase.CLOSED)


def test_knockout_confirmation_to_main_loop_correction_path():
    """Candidate corrected disclaim during confirmation — return to main loop."""
    state = _new_state(phase=InterviewPhase.KNOCKOUT_CONFIRMATION)
    state.transition(InterviewPhase.MAIN_LOOP)
    assert state.phase == InterviewPhase.MAIN_LOOP


def test_main_loop_to_candidate_initiated_wrap_to_closed():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    state.transition(InterviewPhase.CANDIDATE_INITIATED_WRAP)
    state.transition(InterviewPhase.CLOSED)


def test_technical_failure_path_any_phase_to_closed():
    """Every non-CLOSED phase can transition to CLOSED via technical-failure."""
    for phase in InterviewPhase:
        if phase == InterviewPhase.CLOSED:
            continue
        state = _new_state(phase=phase)
        state.transition(InterviewPhase.CLOSED)
        assert state.phase == InterviewPhase.CLOSED


# ---------------------------------------------------------------------------
# Phase transitions — illegal
# ---------------------------------------------------------------------------


def test_cannot_skip_phases_consent_to_main_loop():
    state = _new_state(phase=InterviewPhase.CONSENT)
    with pytest.raises(InterviewPhaseError, match="Illegal phase transition"):
        state.transition(InterviewPhase.MAIN_LOOP)


def test_cannot_go_backward_main_loop_to_intro():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    with pytest.raises(InterviewPhaseError, match="Illegal phase transition"):
        state.transition(InterviewPhase.INTRO)


def test_cannot_go_directly_main_loop_to_early_exit_wrap():
    """early_exit_wrap is reachable only via knockout_confirmation."""
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    with pytest.raises(InterviewPhaseError, match="Illegal phase transition"):
        state.transition(InterviewPhase.EARLY_EXIT_WRAP)


def test_cannot_transition_from_closed():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    state.transition(InterviewPhase.NORMAL_WRAP)
    state.transition(InterviewPhase.CLOSED)
    for phase in InterviewPhase:
        with pytest.raises(InterviewPhaseError, match="Illegal phase transition"):
            state.transition(phase)


def test_normal_wrap_only_goes_to_closed():
    state = _new_state(phase=InterviewPhase.NORMAL_WRAP)
    for phase in InterviewPhase:
        if phase == InterviewPhase.CLOSED:
            continue
        with pytest.raises(InterviewPhaseError, match="Illegal phase transition"):
            state.transition(phase)


def test_failed_transition_does_not_increment_sequence():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    seq_before = state.sequence_number
    with pytest.raises(InterviewPhaseError):
        state.transition(InterviewPhase.INTRO)
    assert state.sequence_number == seq_before


# ---------------------------------------------------------------------------
# Reconnect counter
# ---------------------------------------------------------------------------


def test_record_reconnect_increments_counter_and_sequence():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    seq_before = state.sequence_number
    state.record_reconnect()
    assert state.reconnect_count == 1
    assert state.sequence_number == seq_before + 1
    state.record_reconnect()
    assert state.reconnect_count == 2


def test_record_reconnect_rejected_after_close():
    state = _new_state(phase=InterviewPhase.NORMAL_WRAP)
    state.transition(InterviewPhase.CLOSED)
    with pytest.raises(InterviewPhaseError, match="CLOSED"):
        state.record_reconnect()


# ---------------------------------------------------------------------------
# Exit-mode stamping
# ---------------------------------------------------------------------------


def test_set_exit_mode_stamps_mode_and_ended_at():
    state = _new_state(phase=InterviewPhase.NORMAL_WRAP)
    end_time = datetime.now(UTC)
    state.set_exit_mode(ExitMode.COMPLETED, ended_at=end_time)
    assert state.exit_mode == ExitMode.COMPLETED
    assert state.ended_at == end_time


def test_set_exit_mode_idempotent_with_same_args():
    """Same call twice is fine — covers retry paths in close handler."""
    state = _new_state(phase=InterviewPhase.NORMAL_WRAP)
    end_time = datetime.now(UTC)
    state.set_exit_mode(ExitMode.COMPLETED, ended_at=end_time)
    seq_after_first = state.sequence_number
    state.set_exit_mode(ExitMode.COMPLETED, ended_at=end_time)
    # No error — but sequence_number bumped again (every call writes).
    # If we ever need true idempotency, this test pins current behavior.
    assert state.sequence_number == seq_after_first + 1


def test_set_exit_mode_rejects_reassignment_to_different_mode():
    state = _new_state(phase=InterviewPhase.NORMAL_WRAP)
    end_time = datetime.now(UTC)
    state.set_exit_mode(ExitMode.COMPLETED, ended_at=end_time)
    with pytest.raises(InterviewPhaseError, match="already set"):
        state.set_exit_mode(ExitMode.KNOCKOUT_EXIT, ended_at=end_time)


# ---------------------------------------------------------------------------
# QuestionState invariants
# ---------------------------------------------------------------------------


def test_question_state_defaults():
    q = QuestionState(question_id="q1", position=0, is_mandatory=True)
    assert q.followups_asked == 0
    assert q.meta_request_count == 0
    assert q.elapsed_seconds == 0.0
    assert q.asked_at is None
    assert q.completed_at is None
    assert q.asked_mode is None


def test_question_state_position_must_be_non_negative():
    with pytest.raises(ValueError):
        QuestionState(question_id="q1", position=-1, is_mandatory=False)


def test_question_state_followups_asked_must_be_non_negative():
    with pytest.raises(ValueError):
        QuestionState(
            question_id="q1", position=0, is_mandatory=False, followups_asked=-1,
        )


def test_question_state_asked_mode_literal_enforced():
    """Pydantic rejects unknown asked_mode values."""
    with pytest.raises(ValueError):
        QuestionState(
            question_id="q1", position=0, is_mandatory=False,
            asked_mode="bogus",
        )


# ---------------------------------------------------------------------------
# Phase enum exhaustiveness
# ---------------------------------------------------------------------------


def test_every_phase_has_a_transition_entry():
    """Defense against silently dropping a phase from the allowlist."""
    from app.modules.interview_engine.orchestrator.state import _LEGAL_TRANSITIONS

    for phase in InterviewPhase:
        assert phase in _LEGAL_TRANSITIONS, (
            f"Phase {phase!r} is missing from the transition allowlist"
        )


def test_closed_is_terminal_in_allowlist():
    """CLOSED has no outgoing transitions."""
    from app.modules.interview_engine.orchestrator.state import _LEGAL_TRANSITIONS

    assert _LEGAL_TRANSITIONS[InterviewPhase.CLOSED] == frozenset()


# ---------------------------------------------------------------------------
# Pydantic round-trip (foundation for Redis persistence in A.4)
# ---------------------------------------------------------------------------


def test_state_model_dump_and_validate_roundtrip():
    state = _new_state(phase=InterviewPhase.MAIN_LOOP)
    state.transition(InterviewPhase.NORMAL_WRAP)
    state.transition(InterviewPhase.CLOSED)
    state.set_exit_mode(ExitMode.COMPLETED, ended_at=datetime.now(UTC))

    dumped = state.model_dump(mode="json")
    rehydrated = InterviewState.model_validate(dumped)

    assert rehydrated.phase == InterviewPhase.CLOSED
    assert rehydrated.exit_mode == ExitMode.COMPLETED
    assert rehydrated.sequence_number == state.sequence_number
