from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, score_dimension, score_overall,
)
from app.modules.reporting.scoring.constants import TECHNICAL_TYPES


def _s(value, level, *, type="competency", weight=1, knockout=False, priority="preferred"):
    from app.modules.reporting.scoring.constants import level_score
    return ScoredSignal(value=value, type=type, weight=weight, knockout=knockout,
                        priority=priority, level=level, score=level_score(level))


def test_absent_and_not_reached_score_identically_in_overall():
    a = score_overall([_s("x", "absent")])
    b = score_overall([_s("x", "not_reached")])
    assert a[0] == b[0]


def test_overall_is_weighted_mean_of_primaries():
    scored = [_s("a", "strong", weight=3), _s("b", "thin", weight=1)]
    overall, coverage = score_overall(scored)
    assert overall == round((3 * 100 + 1 * 40) / 4)  # 85
    assert coverage >= 0.0


def test_dimension_filters_by_type():
    scored = [_s("a", "strong", type="competency"), _s("b", "absent", type="behavioral")]
    tech = score_dimension("technical", scored, TECHNICAL_TYPES)
    assert tech.score == 100
