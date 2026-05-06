"""Tests for interview audio pipeline factories in app.ai.realtime."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.ai.realtime import build_interruption_options


class TestBuildInterruptionOptions:
    def test_adaptive_mode_returns_classifier_friendly_defaults(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_interruption_mode = "adaptive"
            opts = build_interruption_options()
        assert opts == {
            "mode": "adaptive",
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }

    def test_vad_mode_returns_backchannel_gating_defaults(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_interruption_mode = "vad"
            opts = build_interruption_options()
        assert opts == {
            "mode": "vad",
            "min_duration": 0.8,
            "min_words": 3,
            "false_interruption_timeout": 2.5,
            "resume_false_interruption": True,
        }


import sys

from app.ai.realtime import build_noise_cancellation


class TestBuildNoiseCancellation:
    def test_off_returns_none_and_does_not_import_plugins(self) -> None:
        # Pre-clear caches so we can assert lazy-import discipline.
        for mod_name in [
            "livekit.plugins.ai_coustics",
            "livekit.plugins.noise_cancellation",
        ]:
            sys.modules.pop(mod_name, None)
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "off"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is None
        assert "livekit.plugins.ai_coustics" not in sys.modules
        assert "livekit.plugins.noise_cancellation" not in sys.modules

    def test_ai_coustics_quail_returns_audio_enhancement(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is not None

    def test_ai_coustics_quail_vf_returns_audio_enhancement(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail_vf"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is not None

    def test_krisp_nc_returns_filter(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "krisp_nc"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is not None

    def test_unknown_value_raises(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "bogus"
            mock_config.interview_nc_enhancement_level = 0.5
            with pytest.raises(ValueError, match="Unknown interview_noise_cancellation"):
                build_noise_cancellation()
