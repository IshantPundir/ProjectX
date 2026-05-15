"""Notifications module — provider-agnostic email + SMS dispatch + type IDs."""
from app.modules.notifications import types
from app.modules.notifications.service import render_template, send_email, send_sms

__all__ = ["render_template", "send_email", "send_sms", "types"]
