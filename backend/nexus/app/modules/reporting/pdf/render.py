"""Render a report to HTML (Jinja) and to PDF (headless Chromium / Playwright).

The Playwright import is LAZY (inside render_report_pdf) so the helper/HTML
tests run in the lean nexus image, which has no Chromium. Only the nexus-pdf
worker image installs Playwright + Chromium + fonts.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

# Footer (page numbers) for Chromium's printToPDF.
_FOOTER = (
    '<div style="font-size:7px;color:#9a9aa6;width:100%;padding:0 14mm;'
    'display:flex;justify-content:space-between;font-family:Arial,sans-serif">'
    '<span>BINQLE.AI &middot; AI VIDEO INTERVIEW PLATFORM</span>'
    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
)
_HEADER = '<div></div>'  # empty header (hero handles page-1 branding)


def build_pdf_html(ctx: dict) -> str:
    """Render the print template to an HTML string (no browser)."""
    return _env.get_template("report.html.j2").render(**ctx)


async def render_report_pdf(ctx: dict) -> bytes:
    """Render the report HTML to PDF bytes via headless Chromium."""
    from playwright.async_api import async_playwright  # lazy: only in pdf worker

    html = build_pdf_html(ctx)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            pdf = await page.pdf(
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template=_HEADER,
                footer_template=_FOOTER,
                margin={"top": "14mm", "bottom": "16mm", "left": "0", "right": "0"},
            )
            return pdf
        finally:
            await browser.close()
