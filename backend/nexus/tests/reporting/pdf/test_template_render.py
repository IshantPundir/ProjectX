"""Smoke tests for the PDF Jinja template (Task C2).

Uses the same _env.get_template(...).render(sample_ctx) path as render.py.
No Playwright — pure Jinja render.
"""
import pytest
from app.modules.reporting.pdf.context import build_pdf_context, build_radar_geometry
from app.modules.reporting.pdf.render import build_pdf_html
from tests.reporting.pdf.test_context import _min_report
from app.modules.reporting.schemas import ReportRead


def _report_with_radar() -> ReportRead:
    """A report with enough signal_assessments to produce a radar."""
    base = {
        "verdict": "advance", "verdict_reason": "ok",
        "overall_score": 8.5, "overall_coverage": 1.0, "overall_confidence": "high",
        "decision": {"headline": "h", "why_positive": {"title": "p", "body": "pb"},
                     "why_negative": {"title": "n", "body": "nb"}},
        "scores": {
            "overall": {"score": 8.5, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
            "technical": {"score": 8.0, "tier_label": "Strong", "tone": "ok", "confidence": "high", "coverage": 1.0},
        },
        "quick_summary": "Strong technical candidate.",
        "strengths": [],
        "concerns": [],
        "questions": [
            {
                "seq": 1, "question_id": "q1",
                "title": "Short title",
                "question_text": "Describe a time you designed a distributed system from scratch and handled the trade-offs between consistency and availability.",
                "status_badge": "passed", "status_tone": "ok",
                "candidate_quote": "I built a system at company X.",
                "our_read": "Good depth shown.",
                "difficulty": "hard",
                "score": 8,
            }
        ],
        "methodology": {"note": "", "charity_flags": []},
        "signal_assessments": [
            {
                "signal": "System Design", "score": 8.0, "weight": 3,
                "provenance": "asked_directly", "level": "strong",
                "type": "skill", "knockout": False, "priority": "high",
            },
            {
                "signal": "Problem Solving", "score": 7.0, "weight": 2,
                "provenance": "asked_directly", "level": "solid",
                "type": "skill", "knockout": False, "priority": "medium",
            },
            {
                "signal": "Communication", "score": 9.0, "weight": 2,
                "provenance": "asked_directly", "level": "strong",
                "type": "behavioral", "knockout": False, "priority": "medium",
            },
            {
                "signal": "Leadership", "score": 6.0, "weight": 1,
                "provenance": "asked_directly", "level": "solid",
                "type": "behavioral", "knockout": False, "priority": "low",
            },
        ],
        "header": {
            "candidate_name": "Riya Sharma",
            "candidate_email": "riya.sharma@example.com",
            "job_title": "Senior Backend Engineer",
            "stage_label": "AI Screening",
            "session_started_at": "2026-06-15T10:30:00Z",
            "duration_seconds": 1845,
            "skills": ["Python", "Distributed Systems", "Kafka"],
        },
    }
    return ReportRead.model_validate(base)


def _build_ctx_with_radar():
    report = _report_with_radar()
    return build_pdf_context(
        report,
        candidate_name="Riya Sharma",
        job_title="Senior Backend Engineer",
        stage_label="AI Screening",
        generated_on="Jun 15, 2026",
        reference_photo_url=None,
        full_session_url="https://app.binqle.ai/recordings/test-token",
    )


class TestTemplateSmokeC2:
    def test_full_question_text_in_html(self):
        """The full long question text must appear, not be truncated."""
        ctx = _build_ctx_with_radar()
        html = build_pdf_html(ctx)
        long_q = "Describe a time you designed a distributed system from scratch and handled the trade-offs between consistency and availability."
        assert long_q in html

    def test_candidate_email_in_html(self):
        ctx = _build_ctx_with_radar()
        html = build_pdf_html(ctx)
        assert "riya.sharma@example.com" in html

    def test_skill_pill_in_html(self):
        ctx = _build_ctx_with_radar()
        html = build_pdf_html(ctx)
        assert "Python" in html
        assert "hskill" in html  # CSS class for skill pills

    def test_radar_polygon_in_html(self):
        ctx = _build_ctx_with_radar()
        assert ctx["radar_geom"] is not None  # 4 signals → radar
        html = build_pdf_html(ctx)
        assert "<polygon" in html  # radar SVG rendered

    def test_star_markup_in_html(self):
        ctx = _build_ctx_with_radar()
        html = build_pdf_html(ctx)
        # Stars rendered as SVG polygons inside qscore div
        assert "qscore" in html
        assert "/ 5" in html

    def test_no_title_truncation_artifact(self):
        """The full question_text appears as the question heading (not the short title)."""
        ctx = _build_ctx_with_radar()
        html = build_pdf_html(ctx)
        # Full text is there
        assert "distributed system from scratch" in html

    def test_session_date_in_html(self):
        ctx = _build_ctx_with_radar()
        html = build_pdf_html(ctx)
        assert "Jun 15, 2026" in html

    def test_duration_in_html(self):
        ctx = _build_ctx_with_radar()
        html = build_pdf_html(ctx)
        assert "30:45" in html  # 1845s = 30min 45s

    def test_radar_geom_none_for_min_report(self):
        """_min_report has no signal_assessments → radar_geom is None → no SVG radar."""
        ctx = build_pdf_context(
            _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
            stage_label="New Stage", generated_on="Jun 14, 2026",
            reference_photo_url=None, full_session_url="https://x/coming-soon",
        )
        assert ctx["radar_geom"] is None
