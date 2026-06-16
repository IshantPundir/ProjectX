"""Dramatiq worker entry point for the report_share queue only.

Run by the dedicated nexus-pdf-worker service (Dockerfile.pdf — Chromium +
fonts). Renders report PDFs with Playwright and emails them.

Why a separate entrypoint (not app.worker)? The lean nexus/nexus-worker image
has no Chromium; if app.worker registered share_report_pdf it would consume
report_share messages and crash on the lazy Playwright import. Registering the
actor ONLY here means only this process declares + consumes report_share.

Import-ordering: app.brokers must initialize the broker before any actor module.
"""
import structlog

from app import brokers  # noqa: F401  (side effect: init broker before actor import)
from app.config import settings
from app.model_registry import configure_all_models
from app.modules.reporting import actors  # noqa: F401  (register share_report_pdf)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.debug
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(10 if settings.debug else 20),
)

# Full ORM mapper registry — ReportShare/SessionReport FK to clients/sessions.
configure_all_models()
