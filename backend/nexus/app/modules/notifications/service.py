"""Provider-agnostic email dispatch with dry-run mode.

When NOTIFICATIONS_DRY_RUN=true: logs the full email body and any invite URLs
to stdout. Copy the URL from the terminal to test the full Pipeline A flow
without Resend credentials.

When NOTIFICATIONS_DRY_RUN=false: sends via Resend API.
"""

import asyncio
import re
from pathlib import Path
from typing import Protocol

import structlog
from jinja2 import Environment, FileSystemLoader

from app.config import settings
from app.modules.notifications.schemas import SMSMessage

logger = structlog.get_logger()

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)

# DryRun helpers — extract the bits of an email that an engineer actually
# wants to copy (invite link, OTP code) so they stand out in the log stream
# instead of being buried in the HTML body.
_INVITE_URL_RE = re.compile(
    r'href=["\'](?P<url>https?://[^"\'<>\s]+/interview/[^"\'<>\s]+)["\']'
)
_OTP_CODE_RE = re.compile(r'>\s*(?P<code>\d{6})\s*<')


def render_template(template_name: str, **kwargs: object) -> str:
    """Render a Jinja2 email template."""
    template = _jinja_env.get_template(template_name)
    return template.render(**kwargs)


class EmailProvider(Protocol):
    """Provider-agnostic email interface."""
    async def send(self, *, to: str, subject: str, html: str) -> None: ...


class DryRunProvider:
    """Logs emails to stdout instead of sending. For local development.

    Extracts the invite link and OTP code from the HTML body and surfaces
    them as first-class log fields so engineers don't have to scan the full
    HTML in the terminal to pull the URL / code for manual testing.
    """
    async def send(self, *, to: str, subject: str, html: str) -> None:
        invite_url = None
        if match := _INVITE_URL_RE.search(html):
            invite_url = match.group("url")
        otp_code = None
        if match := _OTP_CODE_RE.search(html):
            otp_code = match.group("code")

        logger.info(
            "email.dry_run",
            to=to,
            subject=subject,
            invite_url=invite_url,
            otp_code=otp_code,
            html_length=len(html),
        )


class ResendProvider:
    """Sends emails via Resend API."""
    def __init__(self) -> None:
        import resend
        resend.api_key = settings.resend_api_key
        self._from = settings.email_from
        self._resend = resend

    async def send(self, *, to: str, subject: str, html: str) -> None:
        await asyncio.to_thread(
            self._resend.Emails.send,
            {"from": self._from, "to": to, "subject": subject, "html": html},
        )


def _create_provider() -> EmailProvider:
    if settings.notifications_dry_run:
        return DryRunProvider()
    return ResendProvider()


_provider: EmailProvider = _create_provider()


async def send_email(*, to: str, subject: str, html: str) -> None:
    """Send an email. Business logic calls this — never import a provider directly."""
    try:
        await _provider.send(to=to, subject=subject, html=html)
        logger.info("email.sent", to=to, subject=subject)
    except Exception as exc:
        logger.error("email.failed", to=to, subject=subject, error=str(exc))
        raise


async def send_sms(message: SMSMessage) -> bool:
    """Send an SMS through the configured provider (Twilio at MVP)."""
    logger.info("notifications.sms.send", to=message.to)
    # TODO: implement Twilio integration (Phase 5+)
    return True
