"""Provider-agnostic email/SMS dispatch.

Business logic calls send_email / send_sms — never Resend, Twilio, SES, or SNS directly.
Swapping providers requires changing only the implementation here, not the callers.
"""

import structlog

from app.modules.notifications.schemas import EmailMessage, SMSMessage

logger = structlog.get_logger()


async def send_email(message: EmailMessage) -> bool:
    """Send an email through the configured provider (Resend at MVP)."""
    logger.info("notifications.email.send", to=message.to, subject=message.subject)
    # TODO: implement Resend integration
    return True


async def send_sms(message: SMSMessage) -> bool:
    """Send an SMS through the configured provider (Twilio at MVP)."""
    logger.info("notifications.sms.send", to=message.to)
    # TODO: implement Twilio integration
    return True
