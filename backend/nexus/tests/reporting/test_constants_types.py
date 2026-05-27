from app.modules.reporting.scoring.constants import (
    STATE_POINTS, ADVANCE_THRESHOLD, REJECT_THRESHOLD,
    MIN_COVERAGE_FOR_ADVANCE, TECHNICAL_TYPES, BEHAVIORAL_TYPES, tier_label,
)


def test_state_points():
    assert STATE_POINTS == {
        "exceeded": 100, "sufficient": 70, "partial": 30, "failed": 0, "none": None,
    }


def test_thresholds():
    assert ADVANCE_THRESHOLD == 65
    assert REJECT_THRESHOLD == 40
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
