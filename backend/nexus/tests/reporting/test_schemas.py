from app.modules.reporting.schemas import JudgeVerdict, ReportRead


def test_judge_verdict_field_order_evidence_before_level():
    # field order = output order; evidence/justification BEFORE level
    # (reasoning-model best practice)
    fields = list(JudgeVerdict.model_fields.keys())
    assert fields.index("evidence_quotes") < fields.index("level")
    assert fields.index("justification") < fields.index("level")


def test_judge_verdict_level_is_enum():
    v = JudgeVerdict(evidence_quotes=["q"], red_flags_hit=[], justification="j", level="meets_bar")
    assert v.level == "meets_bar"


def test_report_read_roundtrips():
    r = ReportRead(verdict="reject", verdict_reason="failed must-have: x",
                   overall_score=42, overall_coverage=0.8, overall_confidence="high",
                   dimension_scores={}, knockout_results=[], signal_scorecards=[],
                   question_scorecards=[], summary={"headline": "h", "strengths": [],
                   "gaps": [], "rationale": "r"})
    assert r.verdict == "reject"
