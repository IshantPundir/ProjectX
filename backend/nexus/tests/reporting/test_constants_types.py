from app.modules.reporting.scoring import constants
from app.modules.reporting.scoring.types import ScoredUnit, SignalDef


def test_anchors_and_thresholds():
    assert constants.LEVEL_POINTS == {"excellent": 100, "meets_bar": 70, "below_bar": 30}
    assert constants.ADVANCE_THRESHOLD == 75
    assert constants.REJECT_THRESHOLD == 55
    assert constants.MIN_COVERAGE_FOR_ADVANCE == 0.6
    assert constants.SUBSTANTIVE_WORD_FLOOR == 8
    assert frozenset({"competency", "experience", "credential"}) == constants.TECHNICAL_TYPES
    assert frozenset({"behavioral"}) == constants.BEHAVIORAL_TYPES

def test_scored_unit_is_frozen():
    u = ScoredUnit(question_id="q1", question_text="Q?", candidate_answer="A",
                   answer_start_ms=10, probes_fired=1, clarifies=0,
                   word_count=12, candidate_engaged=True)
    assert u.question_id == "q1"
    sd = SignalDef(value="Workato", type="experience", weight=3, knockout=True, priority="required")
    assert sd.knockout is True
