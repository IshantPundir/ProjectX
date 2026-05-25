"""Classify how much opportunity the candidate had to demonstrate a signal.
Opportunity (not answer-quality) is what separates `not_assessed` from `below_bar`."""
from __future__ import annotations

from app.modules.reporting.scoring.constants import SUBSTANTIVE_WORD_FLOOR
from app.modules.reporting.scoring.types import Opportunity, ScoredUnit


def classify(unit: ScoredUnit) -> Opportunity:
    substantive = unit.candidate_engaged and unit.word_count >= SUBSTANTIVE_WORD_FLOOR
    if unit.probes_fired >= 1 or substantive:
        return "full"
    if unit.candidate_engaged:          # asked, barely engaged, no probe
        return "partial"
    return "none"                       # instant non-answer, no probe
