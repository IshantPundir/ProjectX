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

    Reads `AIConfig.interview_interruption_mode`. Cloud mode uses
    `mode="adaptive"` and lets the LK barge-in classifier handle
    backchannel detection. Per the LK turn-handling-options reference,
    `min_words` is honoured in both adaptive and vad modes when STT is
    enabled — setting `min_words=2` in adaptive mode is a safe additive
    guard rail: even if the adaptive classifier says "interrupt," the
    STT transcript must contain ≥2 words before the agent actually yields.
    Self-hosted mode uses `mode="vad"` and compensates with `min_words=3`
    (gates 1-2 word backchannel via Deepgram word-aligned transcripts)
    and tighter `min_duration`.
    """
    mode = ai_config.interview_interruption_mode
    logger.info("ai.realtime.interruption.built", mode=mode)
    if mode == "adaptive":
        return {
            "mode": "adaptive",
            "min_duration": 0.5,
            "min_words": 2,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }
    if mode == "vad":
        return {
            "mode": "vad",
            "min_duration": 0.8,
            "min_words": 3,
            "false_interruption_timeout": 2.5,
            "resume_false_interruption": True,
        }
    raise ValueError(f"Unknown interview_interruption_mode: {mode!r}")


def build_noise_cancellation() -> object | None:
    """Construct the noise cancellation filter from AIConfig.

    Returns a LiveKit AudioFilter-protocol object suitable for passing into
    `room_io.AudioInputOptions(noise_cancellation=...)`, or None when the
    configured value is `"off"` (self-hosted default — no Cloud-side NC).

    Plugin imports are LAZY: a self-hosted deploy with `interview_noise_cancellation=off`
    never imports the Cloud-only `ai_coustics` / `noise_cancellation` plugin
    packages. Critical because the plugins fail at import-time on platforms
    that don't ship the underlying native libraries.

    Note: typed as ``object | None`` because the LiveKit plugin packages do
    not export a stable public return type for ``ai_coustics.audio_enhancement``
    or ``noise_cancellation.NC()``. Both call sites in ``room_io.AudioInputOptions``
    treat the return value as an opaque protocol-conforming object. Tighten
    the type if/when the plugins expose stable public types.
    """
    nc = ai_config.interview_noise_cancellation
    if nc == "off":
        return None
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

