from app.modules.reporting.scoring.aggregate import (
    ScoredSignal,
    SignalObservation,
    combine_signal,
    score_dimension,
    score_overall,
)


def obs(level, opp, red=False):
    return SignalObservation(level=level, opportunity=opp, red_flags_hit=red)


def ss(sig_type, weight, state, score):
    return ScoredSignal(value=f"{sig_type}-{weight}", type=sig_type, weight=weight,
                        knockout=False, priority="required", state=state, score=score)


def test_not_assessed_when_no_opportunity():
    state, score = combine_signal([obs("below_bar", "none")])
    assert state == "not_assessed" and score is None

def test_below_bar_full_opportunity_is_real_low_score():
    state, score = combine_signal([obs("below_bar", "full")])
    assert state == "below_bar" and score == 30

def test_excellent_requires_grounded_excellent_no_redflag():
    state, score = combine_signal([obs("excellent", "full")])
    assert state == "excellent" and score == 100

def test_redflag_with_nothing_meeting_bar_pulls_to_below():
    state, score = combine_signal([obs("meets_bar", "full", red=True)])
    assert state == "below_bar" and score == 30

def test_meets_bar_default():
    state, score = combine_signal([obs("meets_bar", "full")])
    assert state == "meets_bar" and score == 70

def test_best_of_multiple_when_one_excellent():
    state, score = combine_signal([obs("below_bar", "full"), obs("excellent", "full")])
    assert state == "excellent" and score == 100

def test_partial_opportunity_alone_is_not_assessed():
    state, score = combine_signal([obs("meets_bar", "partial")])
    assert state == "not_assessed" and score is None


def test_dimension_weighted_mean_excludes_not_assessed():
    signals = [ss("competency", 3, "excellent", 100),
               ss("competency", 1, "below_bar", 30),
               ss("competency", 2, "not_assessed", None)]   # excluded
    dim = score_dimension("technical", signals, {"competency", "experience", "credential"})
    # (3*100 + 1*30) / (3+1) = 82.5 → 82 ; coverage = (3+1)/(3+1+2) = 0.666...
    assert dim.score == 82
    assert round(dim.coverage, 3) == 0.667
    assert dim.confidence == "medium"

def test_dimension_all_not_assessed_is_none():
    dim = score_dimension("behavioral",
                          [ss("behavioral", 2, "not_assessed", None)], {"behavioral"})
    assert dim.score is None and dim.coverage == 0.0

def test_overall_weighted_mean():
    score, cov = score_overall([ss("competency", 3, "excellent", 100),
                                ss("behavioral", 1, "below_bar", 30)])
    assert score == 82 and round(cov, 2) == 1.0
