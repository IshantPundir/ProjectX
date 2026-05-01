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

if TYPE_CHECKING:
    # Forward-declared so type checkers see the right return types without
    # forcing a runtime import. Only the engine container has these
    # packages installed.
    from livekit.agents.voice.turn import TurnDetectionMode
    from livekit.plugins.ai_coustics import AudioEnhancement
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


def build_noise_cancellation() -> "AudioEnhancement":
    """Construct the ai_coustics noise-cancellation enhancement.

    Model + ``enhancement_level`` are env-driven via ``AIConfig``. When the
    level is None, the plugin's built-in default is used. Lowering the
    level reduces how aggressively the model processes audio — important
    when over-suppression attenuates a soft-spoken candidate's voice
    enough that downstream Silero VAD never crosses its activation
    threshold.

    Model name is resolved against ``ai_coustics.EnhancerModel`` at call
    time so config can name any model the plugin exposes (QUAIL_S,
    QUAIL_L, QUAIL_VF_L, QUAIL_BV, …) without an engine code change.
    """
    from livekit.plugins import ai_coustics

    model_name = ai_config.interview_noise_cancellation_model
    enhancement_level = ai_config.interview_noise_cancellation_level

    try:
        model = getattr(ai_coustics.EnhancerModel, model_name)
    except AttributeError as exc:
        raise ValueError(
            f"Unknown ai_coustics enhancer model: {model_name!r}. "
            f"Set INTERVIEW_NOISE_CANCELLATION_MODEL to one of the values "
            f"in ai_coustics.EnhancerModel."
        ) from exc

    kwargs: dict[str, object] = {"model": model}
    if enhancement_level is not None:
        kwargs["model_parameters"] = ai_coustics.ModelParameters(
            enhancement_level=enhancement_level,
        )

    logger.info(
        "ai.realtime.noise_cancellation.built",
        provider="ai_coustics",
        model=model_name,
        enhancement_level=enhancement_level,
    )
    return ai_coustics.audio_enhancement(**kwargs)
