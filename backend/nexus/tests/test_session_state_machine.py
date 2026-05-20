"""State-machine invariants: legal vs illegal transitions, monotonicity of pre_check load."""
import pytest

from app.modules.session.errors import InvalidSessionStateError
from app.modules.session.schemas import SessionState
from app.modules.session.state_machine import (
    advance_on_pre_check_load,
    transition,
)


def test_transition_accepts_legal_moves():
    assert transition(SessionState.CREATED, SessionState.PRE_CHECK) == SessionState.PRE_CHECK
    assert transition(SessionState.PRE_CHECK, SessionState.CONSENTED) == SessionState.CONSENTED
    assert transition(SessionState.CONSENTED, SessionState.ACTIVE) == SessionState.ACTIVE
    assert transition(SessionState.ACTIVE, SessionState.COMPLETED) == SessionState.COMPLETED


def test_transition_accepts_cancel_from_pre_active_states():
    for s in (SessionState.CREATED, SessionState.PRE_CHECK, SessionState.CONSENTED):
        assert transition(s, SessionState.CANCELLED) == SessionState.CANCELLED


def test_transition_rejects_illegal_moves():
    with pytest.raises(InvalidSessionStateError):
        transition(SessionState.CREATED, SessionState.ACTIVE)  # skip consent
    with pytest.raises(InvalidSessionStateError):
        transition(SessionState.COMPLETED, SessionState.ACTIVE)  # regress
    with pytest.raises(InvalidSessionStateError):
        transition(SessionState.CANCELLED, SessionState.CONSENTED)  # after cancel


def test_advance_on_pre_check_load_is_monotonic():
    # Only created → pre_check
    assert advance_on_pre_check_load(SessionState.CREATED) == SessionState.PRE_CHECK
    # Any later state: no-op
    for s in (
        SessionState.PRE_CHECK, SessionState.CONSENTED,
        SessionState.ACTIVE, SessionState.COMPLETED, SessionState.CANCELLED,
    ):
        assert advance_on_pre_check_load(s) == s


def test_active_to_terminated_is_legal():
    assert transition(SessionState.ACTIVE, SessionState.TERMINATED) == SessionState.TERMINATED


def test_active_to_completed_still_legal():
    assert transition(SessionState.ACTIVE, SessionState.COMPLETED) == SessionState.COMPLETED


def test_terminated_is_terminal():
    with pytest.raises(InvalidSessionStateError):
        transition(SessionState.TERMINATED, SessionState.COMPLETED)


def test_consented_cannot_jump_to_terminated():
    with pytest.raises(InvalidSessionStateError):
        transition(SessionState.CONSENTED, SessionState.TERMINATED)
