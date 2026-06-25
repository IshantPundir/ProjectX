"""Tests for the PDF context builder (web-parity glance band + header)."""
from app.modules.reporting.pdf.context import (
    assessed_dimensions,
    build_competencies,
    build_pdf_context,
    gauge_color,
)
from app.modules.reporting.schemas import ReportRead


# ---------------------------------------------------------------------------
# gauge_color — aligned to web verdict bands (6.5 / 4.0)
# ---------------------------------------------------------------------------


def test_gauge_color_ok():
    assert gauge_color(8.0) == "#AEE3D9"
    assert gauge_color(6.5) == "#AEE3D9"


def test_gauge_color_caution():
    assert gauge_color(5.0) == "#E8930C"
    assert gauge_color(4.0) == "#E8930C"


def test_gauge_color_danger():
    assert gauge_color(3.9) == "#E5556B"


def test_gauge_color_none():
    assert gauge_color(None) == "#E7EBEE"


def test_assessed_dimensions_color_and_filter():
    scores = {
        "technical": {"score": 8.1, "tier_label": "Strong"},
        "behavioral": {"score": None, "tier_label": "Not Assessed"},
        "communication": {"score": 5.0, "tier_label": "Mid"},
    }
    dims = assessed_dimensions(scores)
    assert [d["name"] for d in dims] == ["Technical", "Communication"]   # behavioral dropped
    assert dims[0]["score"] == 8.1
    assert dims[0]["color"] == "#AEE3D9"
    assert dims[1]["color"] == "#E8930C"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_report_with_header() -> ReportRead:
    return ReportRead.model_validate({
        "verdict": "advance",
        "verdict_reason": "strong across the board",
        "overall_score": 8.5,
        "overall_coverage": 1.0,
        "overall_confidence": "high",
        "decision": {
            "headline": "Recommend",
            "why_positive": {"title": "Pro", "body": "body"},
            "why_negative": {"title": "Con", "body": "body"},
        },
        "scores": {
            "overall":       {"score": 8.5, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
            "technical":     {"score": 8.0, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
            "behavioral":    {"score": None, "tier_label": "Not Assessed", "tone": "neutral", "confidence": "low", "coverage": 0.0},
            "communication": {"score": 7.5, "tier_label": "Solid",  "tone": "ok", "confidence": "medium", "coverage": 1.0},
        },
        "quick_summary": "Good candidate.",
        "strengths": [{"title": "Initiative", "detail": "Led the migration"}],
        "concerns":   [{"title": "Gaps", "detail": "Limited testing exp", "severity": "moderate"}],
        "questions": [
            {
                "seq": 1, "question_id": "q1", "title": "Python",
                "status_badge": "passed", "status_tone": "ok",
                "question_text": "Tell me about a time you designed a Python service from scratch.",
                "candidate_quote": "I built a FastAPI service.",
                "our_read": "Strong answer", "difficulty": "hard", "score": 8,
            },
            {
                "seq": 2, "question_id": "q2", "title": "System design",
                "status_badge": "partial", "status_tone": "caution",
                "question_text": "How would you design a distributed rate limiter?",
                "candidate_quote": "I'd use Redis.", "our_read": "Partial",
                "difficulty": "medium", "score": 5,
            },
        ],
        "methodology": {"note": "", "charity_flags": []},
        "signal_assessments": [
            {"signal": "python_expertise", "type": "technical", "weight": 3,
             "knockout": True, "priority": "required",
             "provenance": "asked_directly", "level": "strong", "score": 9.0,
             "level_basis": "dedicated: strong"},
            {"signal": "system_design", "type": "technical", "weight": 2,
             "knockout": False, "priority": "nice_to_have",
             "provenance": "probed_absent", "level": "thin", "score": 5.5},
            {"signal": "leadership", "type": "behavioral", "weight": 1,
             "knockout": False, "priority": "nice_to_have",
             "provenance": "not_reached", "level": "not_reached", "score": None},
        ],
        "header": {
            "candidate_name": "Priya Sharma",
            "candidate_email": "priya@example.com",
            "candidate_title": "Senior Engineer",
            "candidate_location": "Pune, India",
            "company_name": "Acme Corp",
            "job_title": "Senior Backend Engineer",
            "job_location": "Bangalore",
            "work_arrangement": "Remote",
            "stage_label": "AI Screening",
            "session_started_at": "2026-06-15T10:30:00Z",
            "duration_seconds": 1845,
            "skills": ["Python", "FastAPI", "System Design"],
        },
    })


def _make_report_no_header() -> ReportRead:
    return ReportRead.model_validate({
        "verdict": "reject",
        "verdict_reason": "weak",
        "overall_score": 4.0,
        "overall_coverage": 0.5,
        "overall_confidence": "low",
        "decision": {
            "headline": "Decline",
            "why_positive": {"title": "P", "body": "b"},
            "why_negative": {"title": "N", "body": "b"},
        },
        "scores": {
            "overall": {"score": 4.0, "tier_label": "Weak", "tone": "caution", "confidence": "low", "coverage": 0.5},
        },
        "quick_summary": "Weak screen.",
        "strengths": [], "concerns": [], "questions": [],
        "methodology": {"note": "", "charity_flags": []},
    })


def _ctx(report):
    return build_pdf_context(
        report, candidate_name="Fallback Name", job_title="Fallback Title",
        stage_label="Fallback Stage", generated_on="Jun 15, 2026",
        reference_photo_url=None, full_session_url="https://x/recordings/tok",
    )


# ---------------------------------------------------------------------------
# Header block (incl. new fields)
# ---------------------------------------------------------------------------


def test_header_block_present_with_new_fields():
    h = _ctx(_make_report_with_header())["header"]
    assert h["candidate_name"] == "Priya Sharma"
    assert h["candidate_title"] == "Senior Engineer"
    assert h["candidate_location"] == "Pune, India"
    assert h["company_name"] == "Acme Corp"
    assert h["job_location"] == "Bangalore"
    assert h["work_arrangement"] == "Remote"
    assert h["session_date"] == "Jun 15, 2026"
    assert h["duration"] == "30:45"
    assert h["skills"] == ["Python", "FastAPI", "System Design"]


def test_header_block_falls_back_to_params_when_no_header():
    h = _ctx(_make_report_no_header())["header"]
    assert h["candidate_name"] == "Fallback Name"
    assert h["job_title"] == "Fallback Title"
    assert h["company_name"] is None
    assert h["candidate_title"] is None
    assert h["skills"] == []


def test_legacy_top_level_keys_still_present():
    ctx = _ctx(_make_report_with_header())
    for k in ("candidate_name", "job_title", "stage_label", "monogram"):
        assert k in ctx


# ---------------------------------------------------------------------------
# Verdict chrome
# ---------------------------------------------------------------------------


def test_recommendation_glow_and_seal_present():
    ctx = _ctx(_make_report_with_header())
    assert ctx["recommendation"]["label"] == "Recommended"
    assert ctx["recommendation"]["ink"] == "#0B3D34"
    assert ctx["recommendation"]["headline"] == "Recommend"
    assert ctx["glow"]["glow"].startswith("rgba(54,208,127")   # advance → green
    assert ctx["stamp"].text == "APPROVED"
    assert ctx["verified_seal_path"].startswith("M") and ctx["verified_seal_path"].endswith("Z")


def test_gauges_overall_first_then_assessed_dims():
    g = _ctx(_make_report_with_header())["gauges"]
    assert g[0]["name"] == "Overall" and g[0]["is_overall"] is True
    assert [x["name"] for x in g[1:]] == ["Technical", "Communication"]   # behavioral=None dropped
    assert g[0]["color"] == "#AEE3D9"   # 8.5 → ok


# ---------------------------------------------------------------------------
# Competency bars (must-have vs other)
# ---------------------------------------------------------------------------


def test_build_competencies_splits_and_sorts():
    comp = build_competencies(_make_report_with_header())
    assert [b["label"] for b in comp["must_haves"]] == ["python_expertise"]
    assert [b["label"] for b in comp["others"]] == ["system_design", "leadership"]


def test_competency_bar_fields():
    comp = build_competencies(_make_report_with_header())
    must = comp["must_haves"][0]
    assert must["assessed"] is True and must["cleared"] is True       # 9.0 >= 6.5
    assert must["value"] == "9.0" and must["glyph"] == "✓"
    assert must["fill_color"] == "#AEE3D9" and must["must_have"] is True
    assert must["hint"] == "dedicated: strong"

    sys = comp["others"][0]
    assert sys["cleared"] is False and sys["glyph"] == "⚠"           # 5.5 < 6.5
    assert sys["value"] == "5.5" and sys["fill_color"] == "#E8930C"

    lead = comp["others"][1]
    assert lead["not_reached"] is True and lead["assessed"] is False
    assert lead["value"] is None


# ---------------------------------------------------------------------------
# Questions: score + text + star fractions
# ---------------------------------------------------------------------------


def test_questions_carry_score_text_and_stars():
    q0 = _ctx(_make_report_with_header())["questions"][0]
    assert q0["score"] == 8
    assert q0["question_text"].startswith("Tell me about a time")
    assert q0["stars"] == [1.0, 1.0, 1.0, 1.0, 0.0]   # 8 → 4.0 stars


def test_star_fractions_for_score_5():
    q1 = _ctx(_make_report_with_header())["questions"][1]
    assert q1["stars"] == [1.0, 1.0, 0.5, 0.0, 0.0]   # 5 → 2.5 stars


def test_star_fractions_none_score():
    base = _make_report_with_header().model_dump()
    base["questions"][0]["score"] = None
    q0 = _ctx(ReportRead.model_validate(base))["questions"][0]
    assert q0["stars"] == [0.0, 0.0, 0.0, 0.0, 0.0]
