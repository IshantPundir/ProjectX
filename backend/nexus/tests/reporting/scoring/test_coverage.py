from app.modules.reporting.scoring.aggregate import ScoredSignal, compute_coverage


def _s(level, weight=1):
    from app.modules.reporting.scoring.constants import level_score
    return ScoredSignal(value="x", type="competency", weight=weight, knockout=False,
                        priority="preferred", level=level, score=level_score(level))


def test_demonstrated_and_probed_absent_count_as_covered():
    signals = [_s("strong", 2), _s("absent", 2), _s("not_reached", 4)]
    # covered weight = 2 (strong) + 2 (absent) = 4 of 8
    assert compute_coverage(signals) == 0.5


def test_all_not_reached_is_zero_coverage():
    assert compute_coverage([_s("not_reached"), _s("not_reached")]) == 0.0


def test_empty_is_zero():
    assert compute_coverage([]) == 0.0
