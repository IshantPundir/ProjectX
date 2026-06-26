from app.modules.reporting.pdf.context import (
    assessed_dimensions,
    build_competencies,
    build_pdf_context,
    gauge_color,
    monogram_initials,
    recommendation_meta,
    verdict_glow,
    verdict_stamp,
)
from app.modules.reporting.pdf.render import build_pdf_html
from app.modules.reporting.schemas import ReportRead


# ---------------------------------------------------------------------------
# Verdict chrome
# ---------------------------------------------------------------------------


def test_verdict_stamp_mapping():
    assert verdict_stamp("advance").text == "APPROVED"
    assert verdict_stamp("advance").color == "#36d07f"
    assert verdict_stamp("borderline").text == "BORDERLINE"
    assert verdict_stamp("reject").text == "REJECTED"
    assert verdict_stamp("reject").color == "#ff6b6b"


def test_recommendation_meta():
    assert recommendation_meta("advance") == {"label": "Recommended", "ink": "#0B3D34"}
    assert recommendation_meta("borderline") == {"label": "Borderline", "ink": "#4A3E7A"}
    assert recommendation_meta("reject") == {"label": "Not Recommended", "ink": "#8A2733"}


def test_verdict_glow():
    assert verdict_glow("advance")["glow"].startswith("rgba(54,208,127")
    assert verdict_glow("reject")["glow"].startswith("rgba(239,68,68")


def test_monogram_initials():
    assert monogram_initials("Ishant Pundir") == "IP"
    assert monogram_initials("madonna") == "M"
    assert monogram_initials("  Aarav Kumar Mehta ") == "AM"
    assert monogram_initials("") == "?"
    assert monogram_initials(None) == "?"


# ---------------------------------------------------------------------------
# gauge_color — web verdict bands (6.5 / 4.0)
# ---------------------------------------------------------------------------


def test_gauge_color_bands():
    assert gauge_color(8.0) == "#AEE3D9"   # >= 6.5 ok
    assert gauge_color(6.5) == "#AEE3D9"
    assert gauge_color(5.0) == "#E8930C"   # >= 4.0 caution
    assert gauge_color(3.9) == "#E5556B"   # danger
    assert gauge_color(None) == "#E7EBEE"


def test_assessed_dimensions_drops_unassessed_and_colors():
    scores = {
        "overall": {"score": 9.0},
        "technical": {"score": 8.9},
        "behavioral": {"score": None},
        "communication": {"score": 5.0},
    }
    dims = assessed_dimensions(scores)
    assert [d["name"] for d in dims] == ["Technical", "Communication"]   # overall + behavioral excluded
    assert dims[0]["color"] == "#AEE3D9"   # 8.9 → ok
    assert dims[1]["color"] == "#E8930C"   # 5.0 → caution


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _min_report() -> ReportRead:
    """Minimal report — no header, no signal_assessments (legacy/empty path)."""
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


def _report_full() -> ReportRead:
    """Report with header + must-have / other signal assessments."""
    return ReportRead.model_validate({
        "verdict": "advance", "verdict_reason": "ok",
        "overall_score": 8.5, "overall_coverage": 1.0, "overall_confidence": "high",
        "decision": {"headline": "Recommend advance.", "why_positive": {"title": "p", "body": "pb"},
                     "why_negative": {"title": "n", "body": "nb"}},
        "scores": {
            "overall": {"score": 8.5, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
            "technical": {"score": 8.0, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
            "communication": {"score": 7.5, "tier_label": "Solid", "tone": "ok", "confidence": "medium", "coverage": 1.0},
        },
        "quick_summary": "Strong technical candidate.",
        "strengths": [{"title": "Initiative", "detail": "Led the migration"}],
        "concerns": [{"title": "Gaps", "detail": "Limited testing", "severity": "deal_breaker"}],
        "questions": [{"seq": 1, "question_id": "q1", "title": "t",
                       "status_badge": "passed", "status_tone": "ok",
                       "question_text": "Describe a distributed system you built.",
                       "candidate_quote": "I built X.", "our_read": "Good depth.",
                       "difficulty": "hard", "score": 8,
                       "listen_for_hits": ["scaling"], "red_flags_tripped": []}],
        "methodology": {"note": "", "charity_flags": []},
        "signal_assessments": [
            {"signal": "System Design", "score": 9.0, "weight": 3, "provenance": "asked_directly",
             "level": "strong", "type": "skill", "knockout": True, "priority": "required",
             "level_basis": "dedicated: strong"},
            {"signal": "Problem Solving", "score": 5.5, "weight": 2, "provenance": "probed_absent",
             "level": "thin", "type": "skill", "knockout": False, "priority": "nice_to_have"},
            {"signal": "Leadership", "score": None, "weight": 1, "provenance": "not_reached",
             "level": "not_reached", "type": "behavioral", "knockout": False, "priority": "nice_to_have"},
        ],
        "header": {
            "candidate_name": "Riya Sharma", "candidate_email": "riya@example.com",
            "candidate_title": "Senior Engineer", "candidate_location": "Pune, India",
            "company_name": "Acme Corp", "job_title": "Senior Backend Engineer",
            "job_location": "Bangalore", "work_arrangement": "Remote",
            "stage_label": "AI Screening", "session_started_at": "2026-06-15T10:30:00Z",
            "duration_seconds": 1845, "skills": ["Python", "Kafka"],
        },
    })


def _ctx(report):
    return build_pdf_context(
        report, candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/recordings/tok",
    )


# ---------------------------------------------------------------------------
# build_pdf_context shape
# ---------------------------------------------------------------------------


def test_build_pdf_context_shape():
    ctx = _ctx(_min_report())
    assert ctx["candidate_name"] == "Ishant Pundir"
    assert ctx["monogram"] == "IP"
    assert ctx["stamp"].text == "APPROVED"
    assert ctx["recommendation"]["label"] == "Recommended"
    assert [g["name"] for g in ctx["gauges"]] == ["Overall", "Technical", "Communication"]
    assert ctx["gauges"][0]["is_overall"] is True
    assert "glow" in ctx and "verified_seal_path" in ctx
    assert ctx["competencies"] == {"must_haves": [], "others": []}   # no signals
    # Dead radar/pill fields are gone.
    assert "radar" not in ctx and "radar_geom" not in ctx
    assert "strengths_pills" not in ctx and "dimensions" not in ctx


# ---------------------------------------------------------------------------
# Competency bars
# ---------------------------------------------------------------------------


def test_build_competencies_split_sort_and_fields():
    comp = build_competencies(_report_full())
    assert [b["label"] for b in comp["must_haves"]] == ["System Design"]
    assert [b["label"] for b in comp["others"]] == ["Problem Solving", "Leadership"]

    must = comp["must_haves"][0]
    assert must["cleared"] is True and must["glyph"] == "✓" and must["value"] == "9.0"
    assert must["fill_color"] == "#AEE3D9" and must["must_have"] is True
    assert must["hint"] == "dedicated: strong"

    sol = comp["others"][0]
    assert sol["cleared"] is False and sol["glyph"] == "⚠" and sol["fill_color"] == "#E8930C"

    lead = comp["others"][1]
    assert lead["not_reached"] is True and lead["assessed"] is False and lead["value"] is None


# ---------------------------------------------------------------------------
# Header (new fields) + question stars
# ---------------------------------------------------------------------------


def test_header_new_fields_and_fallback():
    h = _ctx(_report_full())["header"]
    assert h["company_name"] == "Acme Corp"
    assert h["candidate_title"] == "Senior Engineer"
    assert h["candidate_location"] == "Pune, India"
    assert h["job_location"] == "Bangalore" and h["work_arrangement"] == "Remote"
    assert h["session_date"] == "Jun 15, 2026" and h["duration"] == "30:45"
    # legacy fallback when no header
    hf = _ctx(_min_report())["header"]
    assert hf["candidate_name"] == "Ishant Pundir" and hf["company_name"] is None


def test_questions_carry_stars():
    q0 = _ctx(_report_full())["questions"][0]
    assert q0["score"] == 8
    assert q0["stars"] == [1.0, 1.0, 1.0, 1.0, 0.0]   # 8 → 4.0 stars


# ---------------------------------------------------------------------------
# Rendered HTML carries the new structure
# ---------------------------------------------------------------------------


def test_rendered_html_has_glance_and_competencies():
    html = build_pdf_html(_ctx(_report_full()))
    for token in ("AI recommendation", "Must-have competencies", "Other competencies",
                  "System Design", "Quick summary", "Why this verdict",
                  "Question by question", "Acme Corp", "Senior Engineer",
                  "riya@example.com", "bar-track", "photo-glow", "vbadge"):
        assert token in html, f"missing: {token}"
    # radar artifacts gone
    assert "radar" not in html
