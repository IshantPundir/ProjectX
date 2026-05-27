"""Deterministic per-question status badge (pure)."""
from __future__ import annotations

from app.modules.reporting.scoring.types import ScoredUnit, StatusBadge

_TONE: dict[str, str] = {
    "passed": "ok", "partial": "caution", "failed_required": "danger",
    "not_demonstrated": "danger", "not_attempted": "neutral",
    "not_fully_assessed": "neutral",
}


def derive_status(
    unit: ScoredUnit,
    *,
    signal_states: dict[str, str],                      # signal -> final CovState
    signal_defs: dict[str, tuple[str, bool, str]],      # signal -> (type, knockout, priority)
    no_experience: bool,
    closed_before_complete: bool,
) -> tuple[StatusBadge, str]:
    """Precedence: failed-required > not-demonstrated > not-attempted >
    not-fully-assessed > passed > partial."""
    states = list(signal_states.values())

    # A required/knockout signal explicitly failed → the deal-breaker question.
    for sig, st in signal_states.items():
        if st == "failed":
            _t, knockout, priority = signal_defs.get(sig, ("competency", False, "preferred"))
            if knockout or priority == "required":
                return "failed_required", _TONE["failed_required"]

    if no_experience and not any(s in ("sufficient", "exceeded", "partial") for s in states):
        return "not_demonstrated", _TONE["not_demonstrated"]

    if (not unit.candidate_engaged) and all(s == "none" for s in states):
        return "not_attempted", _TONE["not_attempted"]

    if closed_before_complete and not any(s in ("sufficient", "exceeded") for s in states):
        return "not_fully_assessed", _TONE["not_fully_assessed"]

    if any(s in ("sufficient", "exceeded") for s in states):
        return "passed", _TONE["passed"]

    if any(s == "partial" for s in states):
        return "partial", _TONE["partial"]

    return "not_fully_assessed", _TONE["not_fully_assessed"]
