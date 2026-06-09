from app.modules.reporting.schemas import (
    DecisionOut,
    MethodologyOut,
    QuestionGradeOut,
    QuestionOut,
    ReportRead,
    ScoreOut,
    SignalAssessmentOut,
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


def test_question_grade_out_defaults():
    g = QuestionGradeOut(level="solid")
    assert g.listen_for_hits == [] and g.red_flags_tripped == []
    assert g.evidence_quotes == [] and g.needs_verification is False
    assert g.overridden is False and g.override_reason is None


def test_question_out_new_card_fields_default():
    q = QuestionOut(seq=1, question_id="q1", title="t", status_badge="passed",
                    status_tone="ok", question_text="…", candidate_quote="…")
    assert q.level == "not_reached" and q.difficulty is None
    assert q.listen_for_hits == [] and q.red_flags_tripped == []
    assert q.probes_used == 0 and q.probes_available == 0


def test_signal_assessment_cross_credit_fields_default():
    s = SignalAssessmentOut(signal="s", type="competency", weight=2, knockout=False,
                            priority="preferred", provenance="asked_directly", level="solid")
    assert s.cross_credit_applied is False and s.level_basis == ""
