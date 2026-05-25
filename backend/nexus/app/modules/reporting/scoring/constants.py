"""Scoring constants. All policy numbers live here (configurable later)."""
from __future__ import annotations

LEVEL_POINTS: dict[str, int] = {"excellent": 100, "meets_bar": 70, "below_bar": 30}

ADVANCE_THRESHOLD = 75          # Overall >= → advance (when not knockout-capped)
REJECT_THRESHOLD = 55           # Overall <  → reject
MIN_COVERAGE_FOR_ADVANCE = 0.6  # below this, a high Overall is forced to borderline
SUBSTANTIVE_WORD_FLOOR = 8      # min words for an answer to count as a "substantive" engagement

TECHNICAL_TYPES = frozenset({"competency", "experience", "credential"})
BEHAVIORAL_TYPES = frozenset({"behavioral"})
