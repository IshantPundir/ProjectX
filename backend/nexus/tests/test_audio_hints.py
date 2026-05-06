"""Tests for the audio_processing_hints derivation in start_session."""

from __future__ import annotations

from app.modules.session.schemas import AudioProcessingHints
from app.modules.session.service import _compute_audio_processing_hints


def test_audio_hints_always_disable_browser_noise_suppression() -> None:
    hints = _compute_audio_processing_hints()
    assert hints == AudioProcessingHints(
        noise_suppression=False,
        echo_cancellation=True,
        auto_gain_control=True,
    )
