from app.modules.reporting.models import SessionReport
from app.modules.reporting.serialization import report_read_from_row


def test_report_read_from_row_maps_columns():
    row = SessionReport(
        status="ready", engine_version="v3", version=1,
        verdict="advance", verdict_reason="Clears the bar",
        overall_score=90, overall_coverage=1.0, overall_confidence="high",
        dimension_scores={"overall": {"score": 90, "tier_label": "Strong",
                                      "tone": "ok", "confidence": "high", "coverage": 1.0}},
        summary={"quick_summary": "Solid screen.",
                 "decision": {"headline": "h",
                              "why_positive": {"title": "p", "body": "pb"},
                              "why_negative": {"title": "n", "body": "nb"}},
                 "strengths": [], "concerns": [],
                 "methodology": {"note": "", "charity_flags": []}},
        question_scorecards=[], signal_scorecards=[],
    )
    read = report_read_from_row(row)
    assert read.verdict == "advance"
    assert read.overall_score == 9.0  # recruiter-facing 0-10 scale: to_ten(90) = 9.0
    assert read.quick_summary == "Solid screen."
    assert read.scores["overall"].score == 9.0  # recruiter-facing 0-10 scale: to_ten(90) = 9.0
