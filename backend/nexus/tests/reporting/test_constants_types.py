from app.modules.reporting.scoring import constants as C
from app.modules.reporting.scoring.types import ScoredUnit, SignalDef

def test_anchors_and_thresholds():
    assert C.LEVEL_POINTS == {"excellent": 100, "meets_bar": 70, "below_bar": 30}
    assert C.ADVANCE_THRESHOLD == 75
    assert C.REJECT_THRESHOLD == 55
    assert C.MIN_COVERAGE_FOR_ADVANCE == 0.6
    assert C.SUBSTANTIVE_WORD_FLOOR == 8
    assert C.TECHNICAL_TYPES == frozenset({"competency", "experience", "credential"})
    assert C.BEHAVIORAL_TYPES == frozenset({"behavioral"})

def test_scored_unit_is_frozen():
    u = ScoredUnit(question_id="q1", question_text="Q?", candidate_answer="A",
                   answer_start_ms=10, probes_fired=1, clarifies=0,
                   word_count=12, candidate_engaged=True)
    assert u.question_id == "q1"
    sd = SignalDef(value="Workato", type="experience", weight=3, knockout=True, priority="required")
    assert sd.knockout is True
