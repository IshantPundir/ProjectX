"""Scoring constants. All policy numbers live here (configurable later)."""
from __future__ import annotations

LEVEL_POINTS: dict[str, int] = {"excellent": 100, "meets_bar": 70, "below_bar": 30}

ADVANCE_THRESHOLD = 65          # Overall >= → advance (when not knockout-capped)
REJECT_THRESHOLD = 40           # Overall <  → reject
MIN_COVERAGE_FOR_ADVANCE = 0.6  # below this, a high Overall is forced to borderline
SUBSTANTIVE_WORD_FLOOR = 8      # min words for an answer to count as a "substantive" engagement

TECHNICAL_TYPES = frozenset({"competency", "experience", "credential"})
BEHAVIORAL_TYPES = frozenset({"behavioral"})

# Question kinds where a brief, clear answer is a complete answer.
# For these kinds, any engaged non-empty response counts as FULL opportunity
# regardless of word count — e.g. "More than sixteen years" is a complete
# answer to "how many years experience?".
FACTUAL_QUESTION_KINDS: frozenset[str] = frozenset({"experience_check", "compliance_binary"})

# ---------------------------------------------------------------------------
# Coverage-state scoring vocabulary (additive — Task 2)
# ---------------------------------------------------------------------------

# Engine coverage state → 0..100 points.
# `none` maps to None so it is excluded from the scoring denominator entirely
# (a signal the candidate never touched should not drag the average; it
# represents a coverage gap, not a graded failure).
STATE_POINTS: dict[str, int | None] = {
    "exceeded": 100, "sufficient": 70, "partial": 30, "failed": 0, "none": None,
}

# 0-100 composite score → display tier label.
# Bands are calibrated against the ADVANCE_THRESHOLD (65) / REJECT_THRESHOLD (40)
# above: "Strong" sits comfortably above advance, "Meets Bar" spans the
# advance-borderline corridor, "Below Bar" spans borderline-reject, and
# "Well Below Bar" covers clear rejects.  Tunable here without touching logic.
_TIER_BANDS: list[tuple[int, str]] = [
    (70, "Strong"),
    (55, "Meets Bar"),
    (40, "Below Bar"),
    (0, "Well Below Bar"),
]


def tier_label(score: int | None) -> str:
    """Map a 0-100 composite score to its display tier label.

    Returns "Not Assessed" when *score* is None (i.e. no gradeable signal
    turns were collected — the candidate never meaningfully touched this
    dimension).
    """
    if score is None:
        return "Not Assessed"
    for floor, label in _TIER_BANDS:
        if score >= floor:
            return label
    return "Well Below Bar"
