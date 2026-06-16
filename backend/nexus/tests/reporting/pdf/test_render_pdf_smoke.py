import pytest

from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.pdf.render import render_report_pdf
from tests.reporting.pdf.test_context import _min_report

pytest.importorskip("playwright")


@pytest.mark.asyncio
async def test_render_report_pdf_returns_pdf_bytes():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/coming-soon",
    )
    pdf = await render_report_pdf(ctx)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000
