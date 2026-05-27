from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, score_signal, score_state, score_dimension, score_overall,
    knockout_status, resolve_verdict, KnockoutResult,
)
from app.modules.reporting.scoring.engine_signals import KnockoutClose


def ss(t, w, state, *, knockout=False, priority="required", texture="concrete"):
    return ScoredSignal(value=f"{t}-{w}-{state}", type=t, weight=w,
                        knockout=knockout, priority=priority, state=state,
                        texture=texture, score=score_signal(state, texture))


def test_score_signal_texture_matrix():
    assert score_signal("sufficient", "concrete") == 75
    assert score_signal("sufficient", "thin") == 50      # bluff penalty
    assert score_signal("exceeded", "concrete") == 100
    assert score_signal("partial", "thin") == 25
    assert score_signal("failed", "concrete") == 0
    assert score_signal("none", "concrete") is None


def test_score_signal_defaults_to_concrete_when_texture_missing():
    assert score_signal("sufficient", None) == 75       # factual gates / un-rechecked = no penalty


def test_score_state_alias_is_concrete_baseline():
    assert score_state("sufficient") == 75
    assert score_state("none") is None


def test_dimension_excludes_none():
    dim = score_dimension("technical",
                          [ss("competency", 3, "sufficient"), ss("competency", 1, "partial"),
                           ss("competency", 2, "none")],
                          {"competency", "experience", "credential"})
    # (3*75 + 1*40)/(3+1) = 66.25 → 66 ; coverage (3+1)/(3+1+2)=0.667
    assert dim.score == 66
    assert round(dim.coverage, 3) == 0.667
    assert dim.confidence == "medium"


def test_reference_session1_technical_recomputed():
    sigs = (
        [ss("experience", 3, "sufficient"), ss("experience", 3, "sufficient")]
        + [ss("competency", 3, "partial")] * 3
        + [ss("competency", 2, "partial")] * 3
    )
    dim = score_dimension("technical", sigs, {"competency", "experience", "credential"})
    # sufficient=75, partial=40 (concrete baseline via ss()):
    # (3*75 + 3*75 + 3*40*3 + 2*40*3) / (3+3+9+6) = (225+225+360+240)/21 = 1050/21 = 50
    assert dim.score == 50


def test_overall_excludes_unassessed_and_communication():
    score, cov = score_overall([ss("competency", 3, "sufficient"),
                                ss("behavioral", 1, "partial")])
    # (3*75 + 1*40)/4 = 66.25 → 66
    assert score == 66 and round(cov, 2) == 1.0


from app.modules.reporting.scoring.aggregate import (
    signal_ceiling, clamp_to_ceiling, apply_holistic,
)
from app.modules.reporting.scoring.constants import REJECT_CEILING, BORDERLINE_CEILING


def test_ceiling_failed_must_have():
    sigs = [ss("competency", 3, "failed", knockout=True), ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.9) == REJECT_CEILING


def test_ceiling_knockout_close():
    sigs = [ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=True, coverage=0.9) == REJECT_CEILING


def test_ceiling_unconfirmed_must_have():
    sigs = [ss("competency", 3, "partial", knockout=True), ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.9) == BORDERLINE_CEILING


def test_ceiling_low_coverage():
    sigs = [ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.4) == BORDERLINE_CEILING


def test_ceiling_clean():
    sigs = [ss("competency", 3, "sufficient", knockout=True), ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.9) is None


def test_clamp_to_ceiling():
    assert clamp_to_ceiling(80, REJECT_CEILING) == 35
    assert clamp_to_ceiling(20, REJECT_CEILING) == 20
    assert clamp_to_ceiling(80, None) == 80
    assert clamp_to_ceiling(None, REJECT_CEILING) == REJECT_CEILING   # knockout w/ no assessed signals
    assert clamp_to_ceiling(None, None) is None


def test_apply_holistic_bounds_and_recaps():
    assert apply_holistic(50, 4, None) == 54
    assert apply_holistic(50, 99, None) == 55         # delta hard-bounded to ±5
    assert apply_holistic(50, -99, None) == 45
    assert apply_holistic(60, 5, BORDERLINE_CEILING) == 60   # re-cap: can't break borderline ceiling
    assert apply_holistic(None, 5, None) is None


def test_knockout_status():
    assert knockout_status(state="failed") == "failed"
    assert knockout_status(state="sufficient") == "passed"
    assert knockout_status(state="exceeded") == "passed"
    assert knockout_status(state="partial") == "insufficient"   # partial must-have = couldn't confirm → human review
    assert knockout_status(state="none") == "insufficient"


def test_verdict_knockout_close_is_reject():
    v = resolve_verdict(overall=90, coverage=0.9, knockouts=[],
                        knockout_close=KnockoutClose(signal="API", quote="never", reason="x"))
    assert v.verdict == "reject" and "API" in v.reason


def test_verdict_reject_on_failed_knockout_flag():
    v = resolve_verdict(overall=90, coverage=0.9, knockout_close=None,
                        knockouts=[KnockoutResult(signal="prog", status="failed", reason="x")])
    assert v.verdict == "reject"


def test_verdict_borderline_on_unconfirmed_knockout():
    v = resolve_verdict(overall=90, coverage=0.9, knockout_close=None,
                        knockouts=[KnockoutResult(signal="prog", status="insufficient", reason="x")])
    assert v.verdict == "borderline"


def test_verdict_advance_when_clear():
    assert resolve_verdict(overall=70, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "advance"


def test_verdict_borderline_on_low_coverage():
    assert resolve_verdict(overall=90, coverage=0.4, knockout_close=None,
                           knockouts=[]).verdict == "borderline"


def test_verdict_reject_on_low_overall():
    assert resolve_verdict(overall=35, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "reject"


def test_verdict_borderline_middle():
    assert resolve_verdict(overall=50, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "borderline"
