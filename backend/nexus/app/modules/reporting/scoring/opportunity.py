"""Classify how much opportunity the candidate had to demonstrate a signal.
Opportunity (not answer-quality) is what separates `not_assessed` from `below_bar`."""
from __future__ import annotations

from app.modules.reporting.scoring.constants import FACTUAL_QUESTION_KINDS, SUBSTANTIVE_WORD_FLOOR
from app.modules.reporting.scoring.types import Opportunity, ScoredUnit


def classify(unit: ScoredUnit) -> Opportunity:
    # Factual question kinds (experience_check, compliance_binary) expect short,
    # direct answers.  A clear engaged response of ≥1 word is a FULL opportunity —
    # applying the SUBSTANTIVE_WORD_FLOOR here would wrongly penalise answers like
    # "More than sixteen years" (4 words) by scoring them as only partial.
    if (
        unit.candidate_engaged
        and unit.question_kind in FACTUAL_QUESTION_KINDS
        and unit.word_count >= 1
    ):
        return "full"

    substantive = unit.candidate_engaged and unit.word_count >= SUBSTANTIVE_WORD_FLOOR
    if unit.probes_fired >= 1 or substantive:
        return "full"
    if unit.candidate_engaged:          # asked, barely engaged, no probe
        return "partial"
    return "none"                       # instant non-answer, no probe
