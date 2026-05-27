from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, score_state, score_dimension, score_overall,
    knockout_status, resolve_verdict, KnockoutResult,
)
from app.modules.reporting.scoring.engine_signals import KnockoutClose


def ss(t, w, state, *, knockout=False, priority="required"):
    return ScoredSignal(value=f"{t}-{w}-{state}", type=t, weight=w,
                        knockout=knockout, priority=priority, state=state,
                        score=score_state(state))


def test_score_state_mapping():
    assert score_state("exceeded") == 100
    assert score_state("sufficient") == 70
    assert score_state("partial") == 30
    assert score_state("failed") == 0
    assert score_state("none") is None


def test_dimension_excludes_none():
    dim = score_dimension("technical",
                          [ss("competency", 3, "sufficient"), ss("competency", 1, "partial"),
                           ss("competency", 2, "none")],
                          {"competency", "experience", "credential"})
    # (3*70 + 1*30)/(3+1) = 60 ; coverage (3+1)/(3+1+2)=0.667
    assert dim.score == 60
    assert round(dim.coverage, 3) == 0.667
    assert dim.confidence == "medium"


def test_reference_session1_technical_lands_at_41():
    sigs = (
        [ss("experience", 3, "sufficient"), ss("experience", 3, "sufficient")]
        + [ss("competency", 3, "partial")] * 3
        + [ss("competency", 2, "partial")] * 3
    )
    dim = score_dimension("technical", sigs, {"competency", "experience", "credential"})
    assert dim.score == 41          # (420+450)/21 -> 41.4 -> 41 ; matches PDF 4.2


def test_overall_excludes_unassessed_and_communication():
    score, cov = score_overall([ss("competency", 3, "sufficient"),
                                ss("behavioral", 1, "partial")])
    assert score == 60 and round(cov, 2) == 1.0


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
