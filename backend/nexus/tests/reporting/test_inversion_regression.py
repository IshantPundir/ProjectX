"""Golden regression: knockout-close candidate ranks BELOW unconfirmed-must-have candidate.

Deterministic unit test (no LLM, no live data) reproducing the two real-world scenarios
that exposed the score inversion bug. Asserts:
  - reject_score < borderline_score  (the inversion is gone)
  - reject_verdict == "reject"
  - borderline_verdict == "borderline"
"""
from app.modules.reporting.scoring.aggregate import (
    KnockoutResult,
    ScoredSignal,
    clamp_to_ceiling,
    knockout_status,
    resolve_verdict,
    score_overall,
    score_signal,
    signal_ceiling,
)
from app.modules.reporting.scoring.engine_signals import KnockoutClose


def _sig(value, t, w, state, *, knockout=False, texture="concrete"):
    return ScoredSignal(value=value, type=t, weight=w, knockout=knockout,
                        priority="required", state=state, texture=texture,
                        score=score_signal(state, texture))


def _grade(scored, knockout_close):
    base, cov = score_overall(scored)
    ceiling = signal_ceiling(scored, knockout_close=knockout_close is not None, coverage=cov)
    session = clamp_to_ceiling(base, ceiling)
    knockouts = [KnockoutResult(signal=s.value, status=knockout_status(state=s.state), reason="")
                 for s in scored if s.knockout]
    verdict = resolve_verdict(overall=session, coverage=cov, knockouts=knockouts,
                              knockout_close=knockout_close)
    return session, verdict.verdict


def test_knockout_close_ranks_below_unconfirmed_must_have():
    # bc7ba6d3-like: strong tenure+Workato, programming must-have never confirmed,
    # interview CLOSED on a knockout (REST disclaim).
    reject_case = [
        _sig("prog", "competency", 3, "none", knockout=True),
        _sig("workato", "experience", 3, "sufficient", knockout=True),
        _sig("tenure", "experience", 3, "sufficient", knockout=True),
        _sig("rest", "competency", 2, "failed"),
    ]
    kc = KnockoutClose(signal="rest", quote="I've never built", reason="x")
    reject_score, reject_verdict = _grade(reject_case, kc)

    # c7173674-like: programming must-have only PARTIAL (unconfirmed), broader engagement.
    borderline_case = [
        _sig("prog", "competency", 3, "partial", knockout=True, texture="thin"),
        _sig("workato", "experience", 3, "sufficient", knockout=True),
        _sig("tenure", "experience", 3, "sufficient", knockout=True),
        _sig("rest", "competency", 2, "partial"),
    ]
    borderline_score, borderline_verdict = _grade(borderline_case, None)

    assert reject_verdict == "reject"
    assert borderline_verdict == "borderline"
    assert reject_score < borderline_score      # inversion fixed
