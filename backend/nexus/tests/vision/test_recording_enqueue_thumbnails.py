# tests/vision/test_recording_enqueue_thumbnails.py
"""Tests that _enqueue_vision_analysis always sends generate_session_thumbnails
(unconditional — report feature) and only sends analyze_session_proctoring when
AUTO_ANALYZE_PROCTORING is enabled.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.session import recording as rec


def test_thumbnail_actor_always_sent_proctoring_enabled(monkeypatch):
    """When proctoring is ON: both generate_session_thumbnails and
    analyze_session_proctoring are enqueued."""
    import app.modules.vision as vision
    from app.config import settings

    monkeypatch.setattr(settings, "auto_analyze_proctoring", True)

    thumb_send = MagicMock()
    proc_send = MagicMock()
    monkeypatch.setattr(vision.generate_session_thumbnails, "send", thumb_send)
    monkeypatch.setattr(vision.analyze_session_proctoring, "send", proc_send)

    rec._enqueue_vision_analysis("sid-1", "tid-1")

    thumb_send.assert_called_once_with("sid-1", "tid-1")
    proc_send.assert_called_once_with("sid-1", "tid-1")


def test_thumbnail_actor_always_sent_proctoring_disabled(monkeypatch):
    """When proctoring is OFF: generate_session_thumbnails IS still enqueued;
    analyze_session_proctoring is NOT."""
    import app.modules.vision as vision
    from app.config import settings

    monkeypatch.setattr(settings, "auto_analyze_proctoring", False)

    thumb_send = MagicMock()
    proc_send = MagicMock()
    monkeypatch.setattr(vision.generate_session_thumbnails, "send", thumb_send)
    monkeypatch.setattr(vision.analyze_session_proctoring, "send", proc_send)

    rec._enqueue_vision_analysis("sid-2", "tid-2")

    # thumbnail actor is ALWAYS sent, decoupled from proctoring gate
    thumb_send.assert_called_once_with("sid-2", "tid-2")
    # proctoring actor must NOT be sent
    proc_send.assert_not_called()
