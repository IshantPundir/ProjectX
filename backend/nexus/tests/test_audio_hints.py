"""Tests for the audio_processing_hints derivation in start_session."""

from __future__ import annotations

from unittest.mock import patch

from app.modules.session.schemas import AudioProcessingHints
from app.modules.session.service import _compute_audio_processing_hints


class TestComputeAudioProcessingHints:
    def test_self_hosted_mode_all_true(self) -> None:
        with patch("app.modules.session.service.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "off"
            hints = _compute_audio_processing_hints()
        assert hints == AudioProcessingHints(
            noise_suppression=True,
            echo_cancellation=True,
            auto_gain_control=True,
        )

    def test_cloud_mode_disables_browser_noise_suppression(self) -> None:
        with patch("app.modules.session.service.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail"
            hints = _compute_audio_processing_hints()
        assert hints == AudioProcessingHints(
            noise_suppression=False,
            echo_cancellation=True,
            auto_gain_control=True,
        )

    def test_cloud_mode_with_quail_vf(self) -> None:
        with patch("app.modules.session.service.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail_vf"
            hints = _compute_audio_processing_hints()
        assert hints.noise_suppression is False

    def test_krisp_nc_also_disables_browser_noise_suppression(self) -> None:
        with patch("app.modules.session.service.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "krisp_nc"
            hints = _compute_audio_processing_hints()
        assert hints.noise_suppression is False
