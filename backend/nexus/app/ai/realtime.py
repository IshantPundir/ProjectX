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

import structlog

from app.ai.config import ai_config
from app.config import settings

if TYPE_CHECKING:
    # Forward-declared so type checkers see the right return types without
    # forcing a runtime import. Only the engine container has these
    # packages installed.
    from livekit.agents.voice.turn import TurnDetectionMode
    from livekit.plugins.cartesia import TTS
    from livekit.plugins.deepgram import STT
    from livekit.plugins.openai import LLM

logger = structlog.get_logger("ai.realtime")


def build_stt_plugin() -> "STT":
    """Construct the realtime Deepgram STT plugin from AIConfig."""
    from livekit.plugins import deepgram

    logger.info(
        "ai.realtime.stt.built",
        provider="deepgram",
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
    )
    return deepgram.STT(
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
    )


def build_llm_plugin() -> "LLM":
    """Construct the realtime OpenAI LLM plugin from AIConfig.

    ``reasoning_effort`` is forwarded only when ``AIConfig.interview_reasoning_effort``
    is non-empty. Per OpenAI's API contract, ``reasoning_effort`` is rejected
    by non-reasoning chat models (``*-chat-latest``) — sending it returns
    HTTP 400, which kills every LLM turn in the realtime pipeline. Reasoning
    models (``gpt-5.1``, ``o3``, ``o4-mini``, ``gpt-5-pro``, …) accept the
    parameter and benefit from the latency tuning it enables.
    """
    from livekit.plugins import openai

    kwargs: dict[str, object] = {"model": ai_config.interview_llm_model}
    if ai_config.interview_reasoning_effort:
        kwargs["reasoning_effort"] = ai_config.interview_reasoning_effort

    logger.info(
        "ai.realtime.llm.built",
        provider="openai",
        model=ai_config.interview_llm_model,
        reasoning_effort=ai_config.interview_reasoning_effort or None,
    )
    return openai.LLM(**kwargs)


def build_tts_plugin() -> "TTS":
    """Construct the realtime Cartesia TTS plugin from AIConfig."""
    from livekit.plugins import cartesia

    logger.info(
        "ai.realtime.tts.built",
        provider="cartesia",
        model=ai_config.interview_tts_model,
        voice=ai_config.interview_tts_voice,
        language=ai_config.interview_tts_language,
    )
    return cartesia.TTS(
        model=ai_config.interview_tts_model,
        voice=ai_config.interview_tts_voice,
        language=ai_config.interview_tts_language,
    )


def build_turn_detector() -> "TurnDetectionMode":
    """Construct the LiveKit multilingual turn-detector model.

    `MultilingualModel` accepts an `unlikely_threshold: float | None`
    tunable that raises the EOU (end-of-utterance) confidence floor.
    Useful in interview contexts where candidates pause to think and a
    too-eager turn-end fires a probe before the candidate finishes the
    answer, or in noisy environments where stray sound bursts can
    prematurely trigger end-of-turn.

    The threshold is sourced from `AIConfig.interview_turn_detector_unlikely_threshold`
    (env: `INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD`). Default is None,
    which delegates to the plugin's own default — only set this when you
    have real session data to tune against.
    """
    from livekit.plugins.turn_detector.multilingual import MultilingualModel

    threshold = ai_config.interview_turn_detector_unlikely_threshold
    if threshold is None:
        return MultilingualModel()
    return MultilingualModel(unlikely_threshold=threshold)


def build_interruption_options() -> dict[str, object]:
    """Construct the `interruption=` block for TurnHandlingOptions.

    Locked to adaptive mode (LK Cloud). The barge-in classifier handles
    backchannel detection. min_words=2 layers an STT-aligned word-count
    gate on top per the LK turn-handling-options reference.
    """
    logger.info("ai.realtime.interruption.built", mode="adaptive")
    return {
        "mode": "adaptive",
        "min_duration": 0.5,
        "min_words": 2,
        "false_interruption_timeout": 2.0,
        "resume_false_interruption": True,
    }


def build_noise_cancellation() -> object:
    """Construct the noise cancellation filter from AIConfig.

    Returns a LiveKit AudioFilter-protocol object suitable for
    passing into `room_io.AudioInputOptions(noise_cancellation=...)`.

    Locked to LK Cloud — at least one ML provider is always wired
    (no self-hosted fallback). Plugin imports stay LAZY for cold-start
    isolation.
    """
    nc = ai_config.interview_noise_cancellation
    logger.info(
        "ai.realtime.noise_cancellation.built",
        provider=nc,
        enhancement_level=ai_config.interview_nc_enhancement_level,
    )
    if nc == "ai_coustics_quail":
        from livekit.plugins import ai_coustics
        return ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_L,
            model_parameters=ai_coustics.ModelParameters(
                enhancement_level=ai_config.interview_nc_enhancement_level,
            ),
        )
    if nc == "ai_coustics_quail_vf":
        from livekit.plugins import ai_coustics
        return ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_VF_L,
            model_parameters=ai_coustics.ModelParameters(
                enhancement_level=ai_config.interview_nc_enhancement_level,
            ),
        )
    if nc == "krisp_nc":
        from livekit.plugins import noise_cancellation
        return noise_cancellation.NC()
    raise ValueError(f"Unknown interview_noise_cancellation: {nc!r}")


def build_vad() -> object:
    """Construct the VAD instance for the AgentSession.

    For ai_coustics modes (default), returns the built-in VAD adapter
    that reads speech/silence signals from the same ai-coustics
    inference that runs for noise cancellation. Saves a separate VAD
    model load and operates on the cleanest possible signal (the
    model's internal classification, not post-filter audio).

    For Krisp mode (no built-in VAD adapter), falls back to Silero.
    """
    nc = ai_config.interview_noise_cancellation
    if nc == "ai_coustics_quail" or nc == "ai_coustics_quail_vf":
        from livekit.plugins import ai_coustics
        logger.info("ai.realtime.vad.built", provider="ai_coustics")
        return ai_coustics.VAD()
    # Krisp branch: fall back to Silero
    from livekit.plugins import silero
    logger.info("ai.realtime.vad.built", provider="silero_fallback")
    return silero.VAD.load(
        activation_threshold=settings.engine_silero_activation_threshold,
        min_speech_duration=settings.engine_silero_min_speech_duration,
        min_silence_duration=settings.engine_silero_min_silence_duration,
    )

