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
    from livekit.agents.stt import STT as _BaseSTT
    from livekit.agents.tts import TTS as _BaseTTS
    from livekit.agents.voice.turn import TurnDetectionMode
    from livekit.plugins.openai import LLM

logger = structlog.get_logger("ai.realtime")


def build_stt_plugin(keyterms: list[str] | None = None) -> "_BaseSTT":
    """Construct the realtime STT plugin selected by AIConfig.

    Provider is chosen by ``AIConfig.interview_stt_provider``
    (env: ``INTERVIEW_STT_PROVIDER``). Default ``deepgram`` (``nova-3``);
    ``sarvam`` (``saaras:v3``) is the switchable alternate.

    Sarvam STT specializes in Indian languages (en-IN, hi-IN, code-mix).
    Both providers expose the same ``livekit.agents.stt.STT`` abstract
    surface, so VAD, turn detection, and adaptive interruption see
    identical event streams.

    ``keyterms`` is the Deepgram nova-3 keyterm-prompting list (10-50
    role-specific terms, LLM-extracted at bank-generation time and
    cached on stage_question_banks.extracted_keyterms; see spec
    docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md).
    Sarvam ignores the argument (its STT has no equivalent feature).
    Pass ``None`` (the default) to skip keyterm boosting entirely.
    """
    provider = ai_config.interview_stt_provider
    if provider == "sarvam":
        return _build_stt_sarvam()
    if provider == "deepgram":
        return _build_stt_deepgram(keyterms=keyterms)
    raise ValueError(
        f"Unknown interview_stt_provider {provider!r}; "
        "expected 'sarvam' or 'deepgram'."
    )


def _build_stt_sarvam() -> "_BaseSTT":
    """Sarvam STT (default). Indian-language tuned. Auth via SARVAM_API_KEY env.

    ``high_vad_sensitivity`` is intentionally left unset (None) so the
    plugin's internal VAD does not race with our Silero VAD.
    """
    from livekit.plugins import sarvam

    logger.info(
        "ai.realtime.stt.built",
        provider="sarvam",
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
        mode=ai_config.interview_stt_mode,
    )
    return sarvam.STT(
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
        mode=ai_config.interview_stt_mode,
    )


def _build_stt_deepgram(*, keyterms: list[str] | None = None) -> "_BaseSTT":
    """Deepgram STT (default). Auth via DEEPGRAM_API_KEY env.

    ``keyterms`` is forwarded as the Deepgram ``keyterm`` REST API
    parameter when non-empty. Nova-3 boosts recognition for each term
    (and multi-word phrase). The 50-term recommendation is enforced
    upstream by the LLM extractor (KeytermExtractionOutput.max_length=50).
    """
    from livekit.plugins import deepgram

    kwargs: dict[str, object] = {
        "model": ai_config.interview_stt_model,
        "language": ai_config.interview_stt_language,
    }
    if keyterms:
        kwargs["keyterm"] = keyterms

    logger.info(
        "ai.realtime.stt.built",
        provider="deepgram",
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
        keyterm_count=len(keyterms) if keyterms else 0,
    )
    return deepgram.STT(**kwargs)


def build_mouth_llm_plugin() -> "LLM":
    """Construct the realtime OpenAI LLM plugin for the v2 *mouth* (Conversation Plane).

    Reads `AIConfig.engine_mouth_model` + `engine_mouth_prompt_cache_key` (R6 — explicit,
    stable cache routing for the byte-stable persona prefix). `reasoning_effort` is
    forwarded ONLY when `engine_mouth_effort` is non-empty: non-reasoning chat models
    reject the param with HTTP 400, which would kill every mouth turn.
    """
    from livekit.plugins import openai

    kwargs: dict[str, object] = {
        "model": ai_config.engine_mouth_model,
        "prompt_cache_key": ai_config.engine_mouth_prompt_cache_key,
    }
    if ai_config.engine_mouth_effort:
        kwargs["reasoning_effort"] = ai_config.engine_mouth_effort

    logger.info(
        "ai.realtime.mouth_llm.built",
        provider="openai",
        model=ai_config.engine_mouth_model,
        prompt_cache_key=ai_config.engine_mouth_prompt_cache_key,
        reasoning_effort=ai_config.engine_mouth_effort or None,
    )
    return openai.LLM(**kwargs)


def build_tts_plugin() -> "_BaseTTS":
    """Construct the realtime TTS plugin selected by AIConfig.

    Provider is chosen by ``AIConfig.interview_tts_provider``
    (env: ``INTERVIEW_TTS_PROVIDER``). Default ``sarvam`` (``bulbul:v3``);
    alternates: ``openai`` (``gpt-4o-mini-tts``), ``cartesia`` (``sonic-2``).

    Voice / model / language fields on AIConfig are interpreted by the
    chosen provider — passing a Sarvam speaker name to OpenAI (or an
    OpenAI preset name to Cartesia) raises at plugin construction. Keep
    voice + model in sync with the provider you pick.
    """
    provider = ai_config.interview_tts_provider
    if provider == "sarvam":
        return _build_tts_sarvam()
    if provider == "openai":
        return _build_tts_openai()
    if provider == "cartesia":
        return _build_tts_cartesia()
    raise ValueError(
        f"Unknown interview_tts_provider {provider!r}; "
        "expected 'sarvam', 'openai', or 'cartesia'."
    )


def _build_tts_sarvam() -> "_BaseTTS":
    """Sarvam TTS (default). Indian-language tuned (bulbul:v3, speaker
    ``shubh`` by default). Auth via SARVAM_API_KEY env.

    ``target_language_code`` is required by the plugin and is sourced
    from ``interview_tts_language`` (en-IN by default). ``temperature``
    only affects bulbul:v3 / bulbul:v3-beta; bulbul:v2 silently ignores
    it. ``pace`` applies to all bulbul models.
    """
    from livekit.plugins import sarvam

    logger.info(
        "ai.realtime.tts.built",
        provider="sarvam",
        model=ai_config.interview_tts_model,
        speaker=ai_config.interview_tts_voice,
        language=ai_config.interview_tts_language,
        pace=ai_config.interview_tts_pace,
        temperature=ai_config.interview_tts_temperature,
    )
    return sarvam.TTS(
        model=ai_config.interview_tts_model,
        target_language_code=ai_config.interview_tts_language,
        speaker=ai_config.interview_tts_voice,
        pace=ai_config.interview_tts_pace,
        temperature=ai_config.interview_tts_temperature,
    )


def _build_tts_openai() -> "_BaseTTS":
    """OpenAI TTS plugin (default). Uses gpt-4o-mini-tts.

    Authenticates via the same ``OPENAI_API_KEY`` env var the rest of the
    codebase uses (no separate key needed). OpenAI TTS auto-detects
    language from the input text — we don't pass ``interview_tts_language``.
    """
    from livekit.plugins import openai as openai_plugin

    logger.info(
        "ai.realtime.tts.built",
        provider="openai",
        model=ai_config.interview_tts_model,
        voice=ai_config.interview_tts_voice,
    )
    return openai_plugin.TTS(
        model=ai_config.interview_tts_model,
        voice=ai_config.interview_tts_voice,
    )


def _build_tts_cartesia() -> "_BaseTTS":
    """Cartesia TTS plugin (alternate)."""
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


def build_turn_detector(
    unlikely_threshold: "float | None",
) -> "TurnDetectionMode":
    """Construct the LiveKit multilingual turn-detector model.

    `MultilingualModel` accepts an `unlikely_threshold: float | None`
    tunable that raises the EOU (end-of-utterance) confidence floor.
    Useful in interview contexts where candidates pause to think and a
    too-eager turn-end fires a probe before the candidate finishes the
    answer, or in noisy environments where stray sound bursts can
    prematurely trigger end-of-turn.

    `unlikely_threshold` is required: pass an explicit float to raise the
    EOU floor, or `None` for the model's per-language tuned default. The
    engine passes `AIConfig.engine_v2_turn_detector_unlikely_threshold`.
    """
    from livekit.plugins.turn_detector.multilingual import MultilingualModel

    if unlikely_threshold is None:
        return MultilingualModel()
    return MultilingualModel(unlikely_threshold=unlikely_threshold)


def build_interruption_options() -> dict[str, object]:
    """Construct the `interruption=` block for TurnHandlingOptions.

    VAD-based barge-in — self-hostable, no LiveKit-Cloud dependency. The word-count
    and duration gates filter backchannel/noise; false-interruption recovery resumes
    the agent's line if no transcript follows.
    """
    logger.info("ai.realtime.interruption.built", mode="vad")
    return {
        "mode": "vad",
        "min_duration": 1.0,
        "min_words": 2,
        "false_interruption_timeout": 2.0,
        "resume_false_interruption": True,
    }


def build_vad() -> object:
    """Construct the Silero VAD. Blocking ONNX model load — call from prewarm()."""
    from livekit.plugins import silero

    logger.info("ai.realtime.vad.built", provider="silero")
    return silero.VAD.load()
