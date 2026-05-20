"""Session state-machine rules.

Legal transitions (directed graph):
    created      → pre_check, cancelled
    pre_check    → consented, cancelled
    consented    → active, cancelled
    active       → completed, error, terminated
    completed    → (terminal)
    cancelled    → (terminal)
    error        → (terminal)
    terminated   → (terminal)

`advance_on_pre_check_load` is the helper for GET /pre-check's state-mutation
contract: it advances created → pre_check and is a no-op from any other state.
"""
from __future__ import annotations

from app.modules.session.errors import InvalidSessionStateError
from app.modules.session.schemas import SessionState


_LEGAL_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.CREATED: {SessionState.PRE_CHECK, SessionState.CANCELLED},
    SessionState.PRE_CHECK: {SessionState.CONSENTED, SessionState.CANCELLED},
    SessionState.CONSENTED: {SessionState.ACTIVE, SessionState.CANCELLED},
    SessionState.ACTIVE: {
        SessionState.COMPLETED,
        SessionState.ERROR,
        SessionState.TERMINATED,
    },
    SessionState.COMPLETED: set(),
    SessionState.CANCELLED: set(),
    SessionState.ERROR: set(),
    SessionState.TERMINATED: set(),
}


def transition(current: SessionState, target: SessionState) -> SessionState:
    """Assert target is reachable from current; return target on success.

    Raises InvalidSessionStateError if the transition is not in the legal
    graph. Self-loops (current == target) are rejected — callers should
    guard idempotency at a higher layer.
    """
    if target not in _LEGAL_TRANSITIONS.get(current, set()):
        raise InvalidSessionStateError(
            f"Illegal transition {current.value} → {target.value}"
        )
    return target


def advance_on_pre_check_load(current: SessionState) -> SessionState:
    """Monotonic: created → pre_check. Every other state: no-op."""
    if current == SessionState.CREATED:
        return SessionState.PRE_CHECK
    return current
