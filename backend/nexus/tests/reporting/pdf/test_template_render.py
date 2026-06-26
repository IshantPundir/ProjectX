"""Smoke tests for the PDF Jinja template (pure Jinja render — no Playwright)."""
from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.pdf.render import build_pdf_html
from tests.reporting.pdf.test_context import _report_full, _ctx, _min_report


class TestTemplateSmoke:
    def test_full_question_text_in_html(self):
        html = build_pdf_html(_ctx(_report_full()))
        assert "Describe a distributed system you built." in html

    def test_candidate_email_and_skills_in_html(self):
        html = build_pdf_html(_ctx(_report_full()))
        assert "riya@example.com" in html
        assert "Python" in html and "hskill" in html

    def test_competency_bars_in_html(self):
        html = build_pdf_html(_ctx(_report_full()))
        assert "System Design" in html        # must-have bar
        assert "Problem Solving" in html       # other bar
        assert "bar-track" in html             # threshold bar markup
        assert "polygon" not in html or "qstars" in html  # stars use polygons, radar removed

    def test_stars_and_grade_in_html(self):
        html = build_pdf_html(_ctx(_report_full()))
        assert "qstars" in html
        assert "/ 5" in html

    def test_session_date_and_duration_in_html(self):
        html = build_pdf_html(_ctx(_report_full()))
        assert "Jun 15, 2026" in html
        assert "30:45" in html   # 1845s

    def test_question_text_wins_over_short_title(self):
        base = _report_full().model_dump()
        base["questions"][0]["title"] = "SHORT_TITLE_ARTIFACT"
        from app.modules.reporting.schemas import ReportRead
        html = build_pdf_html(_ctx(ReportRead.model_validate(base)))
        assert "Describe a distributed system" in html
        assert "SHORT_TITLE_ARTIFACT" not in html

    def test_min_report_renders_without_competencies(self):
        html = build_pdf_html(_ctx(_min_report()))
        assert "Must-have competencies" not in html   # no signals
        assert "Other competencies" not in html
        assert "Quick summary" in html                # sections still render
