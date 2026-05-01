"""Notifications module — provider-agnostic email + SMS dispatch."""
from app.modules.notifications.service import render_template, send_email, send_sms

__all__ = ["render_template", "send_email", "send_sms"]
