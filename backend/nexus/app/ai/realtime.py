"""LiveKit realtime plugin factories.

Single blessed import site for `livekit.plugins.*`. Business logic and the
interview engine import these factories instead of touching the LiveKit
plugin packages directly. Mirrors the same provider-abstraction discipline
that `app.ai.client.get_openai_client()` enforces for batch LLM calls.

Reads model IDs / voices / effort from `AIConfig` — never from env directly.
Adding a new realtime provider is a single-file change here.

Imports of `livekit.plugins.*` are LAZY — the modules are loaded only when
a factory is called. This keeps the FastAPI nexus process from pulling in
the realtime plugin packages (which are installed only in the
interview-engine container per docker-compose). Calling any factory from
the FastAPI process will raise ImportError if the engine plugins aren't
installed; that's intentional.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.config import ai_config

if TYPE_CHECKING:
    # Forward-declared so type checkers see the right return types without
    # forcing a runtime import. Only the engine container has these
    # packages installed.
    from livekit.agents import TurnDetectionMode
    from livekit.plugins.ai_coustics import AudioEnhancement
    from livekit.plugins.cartesia import TTS
    from livekit.plugins.deepgram import STT
    from livekit.plugins.openai import LLM


def build_stt_plugin() -> "STT":
    """Construct the realtime Deepgram STT plugin from AIConfig."""
    from livekit.plugins import deepgram

    return deepgram.STT(
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
    )


def build_llm_plugin() -> "LLM":
    """Construct the realtime OpenAI LLM plugin from AIConfig."""
    from livekit.plugins import openai

    return openai.LLM(model=ai_config.interview_llm_model)


def build_tts_plugin() -> "TTS":
    """Construct the realtime Cartesia TTS plugin from AIConfig."""
    from livekit.plugins import cartesia

    return cartesia.TTS(
        model=ai_config.interview_tts_model,
        voice=ai_config.interview_tts_voice,
        language=ai_config.interview_tts_language,
    )


def build_turn_detector() -> "TurnDetectionMode":
    """Construct the LiveKit multilingual turn-detector model.

    Currently no AIConfig knobs — the model is the only option in
    livekit.plugins.turn_detector.multilingual today. If future versions
    expose tunables, expose them on AIConfig and read here.
    """
    from livekit.plugins.turn_detector.multilingual import MultilingualModel

    return MultilingualModel()


def build_noise_cancellation() -> "AudioEnhancement":
    """Construct the ai_coustics noise-cancellation enhancement.

    Defaults to the Quail VF L model — the engine's existing choice.
    Hoisted here for consistency; AIConfig knob can be added later if
    we want to tune this per-deploy.
    """
    from livekit.plugins import ai_coustics

    return ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_L)
