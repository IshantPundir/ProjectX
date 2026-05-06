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
