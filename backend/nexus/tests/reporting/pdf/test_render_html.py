from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.pdf.render import build_pdf_html
from tests.reporting.pdf.test_context import _min_report


def test_build_pdf_html_contains_key_content():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/coming-soon",
    )
    html = build_pdf_html(ctx)
    assert "Ishant Pundir" in html
    assert "APPROVED" in html          # stamp text
    assert "IP" in html                # monogram (no photo)
    assert "Technical" in html
    assert "Behavioral" not in html    # un-assessed dim omitted
    assert "Solid screen." in html     # summary
    assert "See full session" in html


def test_build_pdf_html_uses_photo_when_present():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url="https://r2/photo.jpg", full_session_url="https://x",
    )
    html = build_pdf_html(ctx)
    assert "https://r2/photo.jpg" in html


def test_build_pdf_html_hero_at_top_and_tone_chip():
    """The dark hero is flush to the page top (no white brand bar), the Overall
    gauge gets a label, and question chips follow the answer's tone (not always
    green)."""
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x",
    )
    html = build_pdf_html(ctx)
    assert "brandhead" not in html      # white header removed — hero is flush at top
    assert "Overall" in html            # overall gauge label
    assert "chip-ok" in html            # status_badge "passed" → tone "ok"
    assert "chip-pass" not in html      # legacy always-green chip class is gone
