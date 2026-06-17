"""Tests for interview audio pipeline factories in app.ai.realtime."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from app.ai.realtime import (
    build_interruption_options,
    build_stt_plugin,
    build_tts_plugin,
    build_vad,
    prewarm_tts_plugin,
)


class TestBuildInterruptionOptions:
    def test_returns_vad_mode_with_gates(self) -> None:
        opts = build_interruption_options()
        assert opts == {
            "mode": "vad",
            "min_duration": 1.0,
            "min_words": 2,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }


class TestBuildVad:
    def test_returns_silero_vad(self) -> None:
        with patch("livekit.plugins.silero.VAD.load", return_value=MagicMock()) as load:
            result = build_vad()
        assert result is not None
        load.assert_called_once()
        assert "livekit.plugins.silero" in sys.modules

    def test_passes_configured_min_silence_duration(self) -> None:
        """build_vad raises Silero's end-of-speech silence window to the configured
        engine_vad_min_silence_s (patience for think-pauses) instead of the 0.55s
        default — the foundational gate that decides when the turn detector runs."""
        with (
            patch("livekit.plugins.silero.VAD.load", return_value=MagicMock()) as load,
            patch("app.ai.realtime.ai_config") as mock_config,
        ):
            mock_config.engine_vad_min_silence_s = 0.8
            build_vad()
        load.assert_called_once_with(min_silence_duration=0.8)


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


class TestBuildTtsPlugin:
    def test_sarvam_provider_loads_sarvam_plugin(self, monkeypatch) -> None:
        monkeypatch.setenv("SARVAM_API_KEY", "test-key")
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_tts_provider = "sarvam"
            mock_config.interview_tts_model = "bulbul:v3"
            mock_config.interview_tts_voice = "shubh"
            mock_config.interview_tts_language = "en-IN"
            mock_config.interview_tts_pace = 1.0
            mock_config.interview_tts_temperature = 0.6
            result = build_tts_plugin()
        assert result is not None
        assert "livekit.plugins.sarvam" in sys.modules

    def test_openai_provider_loads_openai_plugin(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_tts_provider = "openai"
            mock_config.interview_tts_model = "gpt-4o-mini-tts"
            mock_config.interview_tts_voice = "ash"
            result = build_tts_plugin()
        assert result is not None
        assert "livekit.plugins.openai" in sys.modules

    def test_cartesia_provider_loads_cartesia_plugin(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_tts_provider = "cartesia"
            mock_config.interview_tts_model = "sonic-2"
            mock_config.interview_tts_voice = "f8f5f1b2-f02d-4d8e-a40d-fd850a487b3d"
            mock_config.interview_tts_language = "en"
            result = build_tts_plugin()
        assert result is not None
        assert "livekit.plugins.cartesia" in sys.modules

    def test_unknown_provider_raises(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_tts_provider = "bogus"
            with pytest.raises(ValueError, match="Unknown interview_tts_provider"):
                build_tts_plugin()


class TestPrewarmTtsPlugin:
    """prewarm_tts_plugin() import-registers the configured TTS plugin.

    Import-only (no plugin instance → no API keys). It is what the vision
    worker calls on its main thread so the later worker-thread import reuses
    the cached module (LiveKit requires plugin registration on the main thread).
    """

    def test_default_provider_runs_and_caches_module(self) -> None:
        # Default provider is sarvam; calling must not raise and must leave the
        # plugin module registered in sys.modules.
        prewarm_tts_plugin()
        assert "livekit.plugins.sarvam" in sys.modules

    def test_idempotent(self) -> None:
        # A second call hits the sys.modules cache and re-runs nothing.
        prewarm_tts_plugin()
        prewarm_tts_plugin()
        assert "livekit.plugins.sarvam" in sys.modules

    def test_openai_provider_imports_openai_plugin(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_tts_provider = "openai"
            prewarm_tts_plugin()
        assert "livekit.plugins.openai" in sys.modules

    def test_cartesia_provider_imports_cartesia_plugin(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_tts_provider = "cartesia"
            prewarm_tts_plugin()
        assert "livekit.plugins.cartesia" in sys.modules
