"""Render a report to HTML (Jinja) and to PDF (headless Chromium / Playwright).

The Playwright import is LAZY (inside render_report_pdf) so the helper/HTML
tests run in the lean nexus image, which has no Chromium. Only the nexus-pdf
worker image installs Playwright + Chromium + fonts.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_ASSET_DIR = Path(__file__).parent / "assets"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    """The BinQle wordmark as a base64 data URI (embedded — no network fetch).

    Returns "" if the asset is missing so rendering degrades gracefully.
    """
    path = _ASSET_DIR / "binqle-wordmark.png"
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

# Footer (page numbers) for Chromium's printToPDF.
_FOOTER = (
    '<div style="font-size:7px;color:#9a9aa6;width:100%;padding:0 14mm;'
    'display:flex;justify-content:space-between;font-family:Arial,sans-serif">'
    '<span>BINQLE.AI &middot; AI VIDEO INTERVIEW PLATFORM</span>'
    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
)
_HEADER = '<div></div>'  # empty header (hero handles page-1 branding)


def build_pdf_html(ctx: dict) -> str:
    """Render the print template to an HTML string (no browser).

    The embedded logo data URI is injected here (the rendering layer) so the
    pure context builder stays I/O-free.
    """
    return _env.get_template("report.html.j2").render(
        logo_src=_logo_data_uri(), **ctx
    )


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
                # top:0 → the brand header is flush to the page top (no empty
                # white band); bottom margin reserves room for the page footer.
                margin={"top": "0", "bottom": "14mm", "left": "0", "right": "0"},
            )
            return pdf
        finally:
            await browser.close()
