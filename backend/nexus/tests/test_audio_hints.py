"""Tests for the audio_processing_hints derivation in start_session."""

from __future__ import annotations

from app.modules.session.schemas import AudioProcessingHints
from app.modules.session.service import _compute_audio_processing_hints


def test_audio_hints_enable_browser_noise_suppression() -> None:
    """No server-side NC: the browser handles light noise suppression locally.
    EC stays on (full-duplex barge-in); AGC stabilizes input level."""
    hints = _compute_audio_processing_hints()
    assert hints == AudioProcessingHints(
        noise_suppression=True,
        echo_cancellation=True,
        auto_gain_control=True,
    )
