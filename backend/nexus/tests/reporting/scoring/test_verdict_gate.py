from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, must_have_cap, resolve_verdict,
)
from app.modules.reporting.scoring.constants import (
    BORDERLINE_CEILING, REJECT_CEILING, level_score,
)


def _mh(level):
    return ScoredSignal(value="must", type="competency", weight=3, knockout=True,
                        priority="required", level=level, score=level_score(level))


def test_probed_absent_must_have_rejects():
    assert must_have_cap([_mh("absent")], is_knockout_close=False, coverage=1.0) == REJECT_CEILING


def test_knockout_close_rejects():
    assert must_have_cap([_mh("strong")], is_knockout_close=True, coverage=1.0) == REJECT_CEILING


def test_not_reached_must_have_is_borderline():
    assert must_have_cap([_mh("not_reached")], is_knockout_close=False, coverage=1.0) == BORDERLINE_CEILING


def test_thin_must_have_is_borderline():
    assert must_have_cap([_mh("thin")], is_knockout_close=False, coverage=1.0) == BORDERLINE_CEILING


def test_solid_must_have_no_cap():
    assert must_have_cap([_mh("solid")], is_knockout_close=False, coverage=1.0) is None


def test_verdict_reject_on_knockout_close():
    v = resolve_verdict(overall=90, coverage=1.0, is_knockout_close=True,
                        knockout_signal="must", must_haves=[_mh("strong")])
    assert v.verdict == "reject"


def test_verdict_borderline_on_not_reached_must_have():
    v = resolve_verdict(overall=90, coverage=1.0, is_knockout_close=False,
                        knockout_signal=None, must_haves=[_mh("not_reached")])
    assert v.verdict == "borderline"


def test_verdict_advance_when_clear():
    v = resolve_verdict(overall=80, coverage=0.9, is_knockout_close=False,
                        knockout_signal=None, must_haves=[_mh("solid")])
    assert v.verdict == "advance"


def test_resolve_verdict_absent_must_have_rejects():
    v = resolve_verdict(overall=90, coverage=1.0, is_knockout_close=False,
                        knockout_signal=None, must_haves=[_mh("absent")])
    assert v.verdict == "reject"


def test_resolve_verdict_thin_must_have_borderline():
    v = resolve_verdict(overall=90, coverage=1.0, is_knockout_close=False,
                        knockout_signal=None, must_haves=[_mh("thin")])
    assert v.verdict == "borderline"


def test_must_have_cap_low_coverage_forces_borderline():
    # solid must-have (no must-have cap), but coverage below the advance minimum
    # → BORDERLINE_CEILING.
    assert must_have_cap([_mh("solid")], is_knockout_close=False, coverage=0.3) == BORDERLINE_CEILING
