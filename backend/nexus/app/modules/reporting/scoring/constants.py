"""Scoring constants. All policy numbers live here (configurable later)."""
from __future__ import annotations

ADVANCE_THRESHOLD = 70          # Overall >= → advance (when not must-have-capped)
REJECT_THRESHOLD = 55           # Overall <  → reject
MIN_COVERAGE_FOR_ADVANCE = 0.6  # below this, a high Overall is forced to borderline

TECHNICAL_TYPES = frozenset({"competency", "experience", "credential"})
BEHAVIORAL_TYPES = frozenset({"behavioral"})

# Question kinds where a brief, clear answer is a complete answer.
# For these kinds, any engaged non-empty response counts as FULL opportunity
# regardless of word count — e.g. "More than sixteen years" is a complete
# answer to "how many years experience?".
FACTUAL_QUESTION_KINDS: frozenset[str] = frozenset({"experience_check", "compliance_binary"})

# Evidence-threading bounds (Cluster 3) — keep the narrative payload sane while
# still grounding it in real candidate words. Tune against a real session.
NARRATIVE_TRANSCRIPT_CHAR_BUDGET = 8000  # candidate transcript chars sent to the narrative LLM
NARRATIVE_NOTES_PER_SIGNAL = 6           # max engine notes echoed per signal into the narrative
SCORECARD_EVIDENCE_MAX = 5               # max fallback quotes per signal scorecard

# Fit-aware aggregation ceilings (the score MEANS role-fit, so a must-have
# gap caps it — this is the metric's definition, not a post-hoc clamp).
REJECT_CEILING = 35      # failed must-have → score forced into reject band (<55)
BORDERLINE_CEILING = 60  # unconfirmed must-have / low coverage → at most borderline (<70)

# Bound on the Layer-2.5 holistic adjustment (±5 pts = ±0.5 on the /10 scale).
HOLISTIC_ADJ_MAX = 5

# 0-100 composite score → display tier label.
# The verdict bands are aligned 1:1 with the tier bands (so the verdict the
# recruiter sees always matches the tier label on the report):
#   "Strong"     (>=70) → advance      (ADVANCE_THRESHOLD = 70)
#   "Meets Bar"  (55-69) → borderline   (held for human review)
#   "Below Bar"  (40-54) → reject       (REJECT_THRESHOLD = 55)
#   "Well Below" (<40)   → reject
# Tunable here without touching logic; keep the thresholds on the tier floors.
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
# Gen-3 demonstration-level scoring
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
