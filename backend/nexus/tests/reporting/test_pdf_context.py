"""Tests for 0–10 score scale in pdf/context.py (rescale from 0–100)."""
from app.modules.reporting.pdf.context import _bar_color, assessed_dimensions


def test_bar_color_green():
    assert _bar_color(8.0) == "#137a45"


def test_bar_color_amber():
    assert _bar_color(6.5) == "#b4791a"


def test_bar_color_red():
    assert _bar_color(4.0) == "#d23b34"


def test_assessed_dimensions_preserves_one_decimal_score():
    scores = {"technical": {"score": 8.1, "tier_label": "Strong"}}
    dims = assessed_dimensions(scores)
    assert len(dims) == 1
    assert dims[0]["score"] == 8.1


# ---------------------------------------------------------------------------
# C1: header block, radar list, question score + stars
# ---------------------------------------------------------------------------
from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.schemas import ReportRead, ReportHeader


def _make_report_with_header() -> ReportRead:
    """Minimal ReportRead with a populated header and signal_assessments."""
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
            "overall":        {"score": 8.5, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
            "technical":      {"score": 8.0, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
            "behavioral":     {"score": None, "tier_label": "Not Assessed", "tone": "neutral", "confidence": "low", "coverage": 0.0},
            "communication":  {"score": 7.5, "tier_label": "Solid",  "tone": "ok", "confidence": "medium", "coverage": 1.0},
        },
        "quick_summary": "Good candidate.",
        "strengths": [{"title": "Initiative", "detail": "Led the migration"}],
        "concerns":   [{"title": "Gaps", "detail": "Limited testing exp", "severity": "moderate"}],
        "questions": [
            {
                "seq": 1, "question_id": "q1",
                "title": "Tell me about your Python exp",
                "status_badge": "passed", "status_tone": "ok",
                "question_text": "Tell me about a time you designed a Python service from scratch.",
                "candidate_quote": "I built a FastAPI service at my last role.",
                "our_read": "Strong answer",
                "difficulty": "hard",
                "score": 8,
            },
            {
                "seq": 2, "question_id": "q2",
                "title": "System design basics",
                "status_badge": "thin", "status_tone": "warn",
                "question_text": "How would you design a distributed rate limiter?",
                "candidate_quote": "Hmm, I'd use Redis.",
                "our_read": "Partial",
                "difficulty": "medium",
                "score": 5,
            },
        ],
        "methodology": {"note": "", "charity_flags": []},
        "signal_assessments": [
            # Assessed signals (should appear in radar)
            {"signal": "python_expertise", "type": "technical", "weight": 3,
             "knockout": False, "priority": "must_have",
             "provenance": "asked_directly", "level": "strong", "score": 9.0},
            {"signal": "system_design", "type": "technical", "weight": 2,
             "knockout": False, "priority": "nice_to_have",
             "provenance": "probed_absent", "level": "thin", "score": 5.5},
            # Not-reached signal (must be EXCLUDED from radar)
            {"signal": "leadership", "type": "behavioral", "weight": 1,
             "knockout": False, "priority": "nice_to_have",
             "provenance": "not_reached", "level": "not_reached", "score": None},
        ],
        "header": {
            "candidate_name": "Priya Sharma",
            "candidate_email": "priya@example.com",
            "job_title": "Senior Backend Engineer",
            "stage_label": "AI Screening",
            "session_started_at": "2026-06-15T10:30:00Z",
            "duration_seconds": 1845,
            "skills": ["Python", "FastAPI", "System Design"],
        },
    })


def _make_report_no_header() -> ReportRead:
    """Minimal ReportRead with header=None (legacy path)."""
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
            "overall": {"score": 4.0, "tier_label": "Weak", "tone": "warn", "confidence": "low", "coverage": 0.5},
        },
        "quick_summary": "Weak screen.",
        "strengths": [],
        "concerns": [],
        "questions": [],
        "methodology": {"note": "", "charity_flags": []},
    })


# --- header block ---

def test_header_block_present_when_report_has_header():
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="Fallback Name",
        job_title="Fallback Title",
        stage_label="Fallback Stage",
        generated_on="Jun 15, 2026",
        reference_photo_url=None,
        full_session_url="https://x/recordings/tok",
    )
    h = ctx["header"]
    assert h["candidate_name"] == "Priya Sharma"
    assert h["candidate_email"] == "priya@example.com"
    assert h["job_title"] == "Senior Backend Engineer"
    assert h["stage_label"] == "AI Screening"
    assert h["session_date"] == "Jun 15, 2026"   # formatted from session_started_at
    assert h["duration"] == "30:45"               # 1845s → "30:45"
    assert h["skills"] == ["Python", "FastAPI", "System Design"]


def test_header_block_falls_back_to_params_when_no_header():
    report = _make_report_no_header()
    ctx = build_pdf_context(
        report,
        candidate_name="Fallback Name",
        job_title="Fallback Title",
        stage_label="Fallback Stage",
        generated_on="Jun 15, 2026",
        reference_photo_url=None,
        full_session_url="https://x/recordings/tok",
    )
    h = ctx["header"]
    assert h["candidate_name"] == "Fallback Name"
    assert h["job_title"] == "Fallback Title"
    assert h["stage_label"] == "Fallback Stage"
    assert h["candidate_email"] is None
    assert h["session_date"] is None
    assert h["duration"] is None
    assert h["skills"] == []


def test_legacy_top_level_keys_still_present():
    """Template backwards-compat: candidate_name/job_title/stage_label remain at the top level."""
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="Fallback Name",
        job_title="Fallback Title",
        stage_label="Fallback Stage",
        generated_on="Jun 15, 2026",
        reference_photo_url=None,
        full_session_url="https://x/recordings/tok",
    )
    # Top-level keys that the existing template uses must survive.
    assert "candidate_name" in ctx
    assert "job_title" in ctx
    assert "stage_label" in ctx


# --- radar list ---

def test_radar_excludes_not_reached():
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    radar = ctx["radar"]
    names = [r["name"] for r in radar]
    assert "leadership" not in names          # provenance=not_reached → excluded
    assert "python_expertise" in names
    assert "system_design" in names


def test_radar_sorted_by_weight_desc():
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    radar = ctx["radar"]
    assert radar[0]["name"] == "python_expertise"   # weight=3
    assert radar[1]["name"] == "system_design"       # weight=2


def test_radar_capped_at_8():
    """Build a report with 10 assessed signals; radar should have at most 8."""
    signals = [
        {
            "signal": f"skill_{i}", "type": "technical", "weight": 10 - i,
            "knockout": False, "priority": "must_have",
            "provenance": "asked_directly", "level": "strong", "score": float(7 + (i % 3)),
        }
        for i in range(10)
    ]
    base = _make_report_with_header().model_dump()
    base["signal_assessments"] = signals
    report = ReportRead.model_validate(base)
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    assert len(ctx["radar"]) == 8


def test_radar_score_is_0_10():
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    for r in ctx["radar"]:
        assert 0.0 <= r["score"] <= 10.0, f"out of range: {r}"


# --- questions: score + question_text + star fractions ---

def test_questions_carry_score():
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    q0 = ctx["questions"][0]
    assert q0["score"] == 8           # raw score preserved


def test_questions_carry_full_question_text():
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    q0 = ctx["questions"][0]
    assert q0["question_text"] == "Tell me about a time you designed a Python service from scratch."


def test_questions_carry_star_fractions():
    """Each question dict must have a 'stars' key with 5 fill fractions in [0, 1]."""
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    q0 = ctx["questions"][0]   # score=8 → 4.0 stars
    assert "stars" in q0
    assert len(q0["stars"]) == 5
    # All fractions are in [0, 1]
    for f in q0["stars"]:
        assert 0.0 <= f <= 1.0


def test_star_fractions_for_score_8():
    """score=8 → 4.0 out of 5 stars → [1,1,1,1,0]."""
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    q0 = ctx["questions"][0]   # score=8
    assert q0["stars"] == [1.0, 1.0, 1.0, 1.0, 0.0]


def test_star_fractions_for_score_5():
    """score=5 → 2.5 out of 5 stars → [1, 1, 0.5, 0, 0]."""
    report = _make_report_with_header()
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    q1 = ctx["questions"][1]   # score=5
    assert q1["stars"] == [1.0, 1.0, 0.5, 0.0, 0.0]


def test_star_fractions_none_score():
    """score=None → all zeros (not assessed)."""
    base = _make_report_with_header().model_dump()
    base["questions"][0]["score"] = None
    report = ReportRead.model_validate(base)
    ctx = build_pdf_context(
        report,
        candidate_name="X", job_title="Y", stage_label="Z",
        generated_on="Jun 15, 2026", reference_photo_url=None,
        full_session_url="https://x",
    )
    q0 = ctx["questions"][0]
    assert q0["stars"] == [0.0, 0.0, 0.0, 0.0, 0.0]
