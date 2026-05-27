from app.modules.reporting.schemas import (
    DecisionOut,
    MethodologyOut,
    ReportRead,
    ScoreOut,
    WhyColumn,
)


def test_report_read_roundtrips():
    r = ReportRead(
        verdict="reject",
        verdict_reason="failed must-have: x",
        overall_score=42,
        overall_coverage=0.8,
        overall_confidence="high",
        decision=DecisionOut(
            headline="Not recommended.",
            why_positive=WhyColumn(title="", body=""),
            why_negative=WhyColumn(title="", body=""),
        ),
        scores={
            "overall": ScoreOut(score=42, tier_label="Below Bar", tone="caution",
                                confidence="high", coverage=0.8),
        },
        methodology=MethodologyOut(note="n", charity_flags=[]),
    )
    assert r.verdict == "reject"
    assert r.scores["overall"].score == 42
    assert r.status == "ready"  # default


def test_score_out_defaults_coverage():
    s = ScoreOut(score=None, tier_label="Not Assessed", tone="neutral", confidence="low")
    assert s.coverage == 0.0
    assert s.score is None
