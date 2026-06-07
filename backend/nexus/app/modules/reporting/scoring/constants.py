"""Scoring constants. All policy numbers live here (configurable later)."""
from __future__ import annotations

ADVANCE_THRESHOLD = 65          # Overall >= → advance (when not knockout-capped)
REJECT_THRESHOLD = 40           # Overall <  → reject
MIN_COVERAGE_FOR_ADVANCE = 0.6  # below this, a high Overall is forced to borderline

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

# Engine coverage state × evidence texture → 0..100 points.
# `none` → None (excluded from the scoring denominator entirely).
# Texture is the bluff axis: `thin` (buzzwords, no demonstrated depth) scores
# well below `concrete` (specific, owned, mechanism shown) at the same state.
STATE_TEXTURE_POINTS: dict[str, dict[str, int]] = {
    "exceeded":   {"concrete": 100, "thin": 80, "null": 80},
    "sufficient": {"concrete": 75,  "thin": 50, "null": 50},
    "partial":    {"concrete": 40,  "thin": 25, "null": 12},
    "failed":     {"concrete": 0,   "thin": 0,  "null": 0},
}

# Fit-aware aggregation ceilings (the score MEANS role-fit, so a must-have
# gap caps it — this is the metric's definition, not a post-hoc clamp).
REJECT_CEILING = 35      # failed must-have / knockout_close → score forced into reject band (<40)
BORDERLINE_CEILING = 60  # unconfirmed must-have / low coverage → at most borderline (<65)

# Bound on the Layer-2.5 holistic adjustment (±5 pts = ±0.5 on the /10 scale).
HOLISTIC_ADJ_MAX = 5

# 0-100 composite score → display tier label.
# Bands are calibrated against the ADVANCE_THRESHOLD (65) / REJECT_THRESHOLD (40)
# above: "Strong" sits comfortably above advance, "Meets Bar" spans the
# advance-borderline corridor, "Below Bar" spans borderline-reject, and
# "Well Below Bar" covers clear rejects.  Tunable here without touching logic.
# NOTE: ADVANCE_THRESHOLD (65) sits inside the "Meets Bar" band (55-69): a
# "Meets Bar" candidate CAN be auto-advanced. "Below Bar" (40-54) is the
# borderline zone; REJECT_THRESHOLD (40) is its floor.
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


# ---------------------------------------------------------------------------
# Gen-3 demonstration-level scoring (replaces STATE_TEXTURE_POINTS)
# ---------------------------------------------------------------------------
# Level → 0..100 points. Ordering is load-bearing; absolute numbers are tunable.
# `absent` and `not_reached` share the floor (uniform low band — see spec §5).
LEVEL_POINTS: dict[str, int] = {
    "strong": 100,
    "solid": 80,
    "thin": 40,
    "absent": 10,
    "not_reached": 10,
}


def level_score(level: str) -> int:
    """Map a DemonstrationLevel to its 0..100 score."""
    return LEVEL_POINTS[level]
