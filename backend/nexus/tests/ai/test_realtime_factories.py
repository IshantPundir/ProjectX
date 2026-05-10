"""Tests for interview audio pipeline factories in app.ai.realtime."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from app.ai.realtime import (
    build_interruption_options,
    build_noise_cancellation,
    build_stt_plugin,
    build_tts_plugin,
    build_vad,
)


class TestBuildInterruptionOptions:
    def test_returns_adaptive_classifier_friendly_defaults(self) -> None:
        opts = build_interruption_options()
        assert opts == {
            "mode": "adaptive",
            "min_duration": 0.5,
            "min_words": 2,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }


class TestBuildNoiseCancellation:
    def test_ai_coustics_quail_returns_audio_enhancement(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is not None
        assert "livekit.plugins.ai_coustics" in sys.modules

    def test_ai_coustics_quail_vf_returns_audio_enhancement(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail_vf"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is not None
        assert "livekit.plugins.ai_coustics" in sys.modules

    def test_unknown_value_raises(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "bogus"
            mock_config.interview_nc_enhancement_level = 0.5
            with pytest.raises(ValueError, match="Unknown interview_noise_cancellation"):
                build_noise_cancellation()


class TestBuildVad:
    def test_returns_ai_coustics_vad(self) -> None:
        result = build_vad()
        assert result is not None
        assert "livekit.plugins.ai_coustics" in sys.modules


class TestBuildSttPlugin:
    def test_sarvam_provider_loads_sarvam_plugin(self, monkeypatch) -> None:
        monkeypatch.setenv("SARVAM_API_KEY", "test-key")
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_stt_provider = "sarvam"
            mock_config.interview_stt_model = "saaras:v3"
            mock_config.interview_stt_language = "en-IN"
            mock_config.interview_stt_mode = "transcribe"
            result = build_stt_plugin()
        assert result is not None
        assert "livekit.plugins.sarvam" in sys.modules

    def test_deepgram_provider_loads_deepgram_plugin(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_stt_provider = "deepgram"
            mock_config.interview_stt_model = "nova-3"
            mock_config.interview_stt_language = "en"
            result = build_stt_plugin()
        assert result is not None
        assert "livekit.plugins.deepgram" in sys.modules

    def test_unknown_provider_raises(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_stt_provider = "bogus"
            with pytest.raises(ValueError, match="Unknown interview_stt_provider"):
                build_stt_plugin()
