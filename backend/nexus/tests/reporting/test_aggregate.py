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
