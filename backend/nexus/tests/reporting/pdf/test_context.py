from app.modules.reporting.pdf.context import (
    monogram_initials,
    verdict_stamp,
    assessed_dimensions,
)


def test_verdict_stamp_mapping():
    assert verdict_stamp("advance").text == "APPROVED"
    assert verdict_stamp("advance").color == "#138a47"
    assert verdict_stamp("borderline").text == "BORDERLINE"
    assert verdict_stamp("borderline").color == "#c98a16"
    assert verdict_stamp("reject").text == "REJECTED"
    assert verdict_stamp("reject").color == "#d23b34"


def test_monogram_initials():
    assert monogram_initials("Ishant Pundir") == "IP"
    assert monogram_initials("madonna") == "M"
    assert monogram_initials("  Aarav Kumar Mehta ") == "AM"
    assert monogram_initials("") == "?"
    assert monogram_initials(None) == "?"


def test_assessed_dimensions_drops_unassessed():
    scores = {
        "overall": {"score": 90},
        "technical": {"score": 89, "tier_label": "Strong"},
        "behavioral": {"score": None, "coverage": 0.0},
        "communication": {"score": 70, "tier_label": "Strong"},
    }
    dims = assessed_dimensions(scores)
    names = [d["name"] for d in dims]
    assert names == ["Technical", "Communication"]  # overall + behavioral excluded
    assert dims[0]["score"] == 89


from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.schemas import ReportRead


def _min_report() -> ReportRead:
    return ReportRead.model_validate({
        "verdict": "advance", "verdict_reason": "ok",
        "overall_score": 90, "overall_coverage": 1.0, "overall_confidence": "high",
        "decision": {"headline": "h", "why_positive": {"title": "p", "body": "pb"},
                     "why_negative": {"title": "n", "body": "nb"}},
        "scores": {"overall": {"score": 90, "tier_label": "Strong", "tone": "ok",
                               "confidence": "high", "coverage": 1.0},
                   "technical": {"score": 89, "tier_label": "Strong", "tone": "ok",
                                 "confidence": "high", "coverage": 1.0},
                   "behavioral": {"score": None, "tier_label": "Not Assessed",
                                  "tone": "neutral", "confidence": "low", "coverage": 0.0},
                   "communication": {"score": 70, "tier_label": "Strong", "tone": "ok",
                                     "confidence": "medium", "coverage": 1.0}},
        "quick_summary": "Solid screen.",
        "strengths": [{"title": "s1", "detail": "d1"}],
        "concerns": [{"title": "c1", "detail": "d1", "severity": "moderate"}],
        "questions": [{"seq": 1, "question_id": "q1", "title": "Q one",
                       "status_badge": "passed", "status_tone": "ok",
                       "question_text": "text", "candidate_quote": "quote", "our_read": "",
                       "difficulty": "medium"}],
        "methodology": {"note": "", "charity_flags": []},
    })


def test_build_pdf_context_shape():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/coming-soon",
    )
    assert ctx["candidate_name"] == "Ishant Pundir"
    assert ctx["monogram"] == "IP"
    assert ctx["stamp"].text == "APPROVED"
    assert ctx["overall_score"] == 90
    assert [d["name"] for d in ctx["dimensions"]] == ["Technical", "Communication"]
    assert ctx["reference_photo_url"] is None
    assert len(ctx["questions"]) == 1
