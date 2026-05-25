"""Pure proctoring policy helpers — no DB, no I/O, fully unit-testable.

The HTTP endpoint + service (session/router.py, session/service.py) call
these to classify a violation and decide whether it terminates the session.
Severity is server-authoritative — the client-reported kind is the only
input we trust, and only after Pydantic validates it against ProctoringKind.
"""
from __future__ import annotations

from typing import Literal

Severity = Literal["hard", "soft"]

VIOLATION_SEVERITY: dict[str, Severity] = {
    "tab_switch": "hard",
    # focus_loss is the "returned within the grace window" signal (soft warning),
    # mirroring fullscreen_exit. focus_abandoned is the "grace expired" signal
    # (hard, terminates), mirroring fullscreen_abandoned.
    "focus_loss": "soft",
    "focus_abandoned": "hard",
    "fullscreen_abandoned": "hard",
    "devtools": "hard",
    "fullscreen_exit": "soft",
    "keyboard": "soft",
}


def classify_severity(kind: str) -> Severity:
    """Return 'hard'|'soft' for a violation kind. Raises KeyError on unknown."""
    return VIOLATION_SEVERITY[kind]


def decide_termination(
    *, kind: str, soft_count_including_new: int, soft_limit: int
) -> tuple[bool, str | None]:
    """Decide whether this violation ends the session.

    Returns (terminal, proctoring_outcome). For a hard kind the outcome is
    the kind itself; for a soft escalation it is 'soft_threshold_exceeded';
    otherwise (False, None).
    """
    severity = classify_severity(kind)
    if severity == "hard":
        return True, kind
    if soft_count_including_new > soft_limit:
        return True, "soft_threshold_exceeded"
    return False, None
