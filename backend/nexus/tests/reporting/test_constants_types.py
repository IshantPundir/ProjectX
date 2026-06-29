from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD, REJECT_THRESHOLD,
    MIN_COVERAGE_FOR_ADVANCE, TECHNICAL_TYPES, BEHAVIORAL_TYPES, tier_label,
)


def test_thresholds():
    # Verdict bands are aligned to the tier-label floors: advance = "Strong"
    # (>=70), borderline = "Meets Bar" (55-69), reject = "Below Bar" or worse.
    assert ADVANCE_THRESHOLD == 70
    assert REJECT_THRESHOLD == 55
    assert MIN_COVERAGE_FOR_ADVANCE == 0.6


def test_tier_label_bands():
    assert tier_label(80) == "Strong"
    assert tier_label(60) == "Meets Bar"
    assert tier_label(50) == "Below Bar"
    assert tier_label(30) == "Well Below Bar"
    assert tier_label(None) == "Not Assessed"


def test_type_sets():
    assert TECHNICAL_TYPES == frozenset({"competency", "experience", "credential"})
    assert BEHAVIORAL_TYPES == frozenset({"behavioral"})
