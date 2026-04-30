"""Engine-mechanics config.

LLM / STT / TTS / model IDs / API keys are NOT here — they live in
nexus's ``app.ai.config.AIConfig`` and are read via the
``app.ai.realtime`` factory functions. This file only owns engine
mechanics: agent name, probe budget, time-warning threshold,
endpointing delays, the nexus internal API base URL, and the
results-fallback directory used when POSTing results to nexus fails.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class InterviewEngineConfig(BaseSettings):
    """Singleton-style settings object loaded from environment / .env.

    All fields here describe how the engine *behaves* mid-session, not
    which AI providers it talks to. Provider config is in
    ``nexus.app.ai.config.AIConfig``.
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # -- Agent identity ----------------------------------------------------
    # Must match the value nexus's dispatcher passes as `agent_name` on
    # CreateAgentDispatchRequest (see app/config.py::interview_agent_name).
    agent_name: str = "Dakota-1785"

    # -- Interview state machine -------------------------------------------
    max_probes_per_question: int = 3
    time_warning_threshold: float = 0.8  # warn at 80% elapsed

    # -- Turn detection / endpointing --------------------------------------
    # Interview-tuned: candidates pause to think mid-answer, but we want
    # snappy turn-taking on short replies. With dynamic endpointing
    # enabled in agent.py, these are the lower/upper bounds — the engine
    # adapts within this range based on observed pause statistics.
    #
    # min_delay = 0.3 — minimum wait after VAD silence before declaring
    #   the turn complete. Lower than the LiveKit default of 0.5 because
    #   interview turns tend to be punchy (yes/no, "let me think...",
    #   short factual answers); too long a floor feels sluggish on those.
    # max_delay = 2.5 — upper bound for "is the candidate still
    #   thinking?" pauses. Was 6.0 originally which made the agent feel
    #   unresponsive when the candidate had finished but the VAD wasn't
    #   confident. 2.5s is the LiveKit-doc recommended ceiling for
    #   conversational agents.
    endpointing_min_delay: float = 0.3
    endpointing_max_delay: float = 2.5

    # -- Silero VAD --------------------------------------------------------
    # Forwarded to silero.VAD.load() in agent.py::prewarm. Silero's own
    # defaults are activation_threshold=0.5, min_speech_duration=0.05,
    # min_silence_duration=0.55.
    #
    # activation_threshold = 0.3 (LiveKit default 0.5). Lower = more
    #   sensitive detection. We override down to 0.3 because ai_coustics
    #   noise cancellation runs *before* VAD and attenuates real voice
    #   alongside noise; at 0.5 the cleaned signal often doesn't cross
    #   the speech bar in real-world office environments. 0.3 also
    #   aligns with the AssemblyAI-recommended VAD floor when downstream
    #   STT does its own endpointing. Drop further (0.2-0.25) if even
    #   0.3 misses your voice; raise back to 0.4-0.5 if VAD trips on
    #   non-speech noise.
    # min_speech_duration = 0.05 (Silero default). Minimum continuous
    #   speech duration to start a speech chunk. Small so single-word
    #   replies ("yes", "okay") aren't dropped.
    # min_silence_duration = 0.55 (Silero default). Trailing silence
    #   needed to declare the speech chunk complete. The dynamic
    #   endpointing layer above stretches this further during real
    #   pauses, so leave at default.
    silero_activation_threshold: float = 0.3
    silero_min_speech_duration: float = 0.05
    silero_min_silence_duration: float = 0.55

    # -- Nexus internal API ------------------------------------------------
    # Compose-network hostname. Override via NEXUS_INTERNAL_BASE_URL env.
    nexus_internal_base_url: str = "http://nexus:8000"

    # -- Results fallback --------------------------------------------------
    # The engine POSTs SessionResult to nexus's /api/internal/sessions/{id}/results.
    # On POST failure (3 retries exhausted), it writes JSON to this directory
    # so the result isn't lost. Alerted via structlog CRITICAL.
    results_fallback_dir: Path = Path("/tmp/interview_results")

    # -- Audio pipeline observability --------------------------------------
    # When True, emit structlog records for every session event during the
    # interview: VAD state transitions, STT finality, EOU decisions,
    # agent-state changes, LLM/TTS metrics, function-tool calls, false
    # interruptions, overlapping speech, session usage, errors, close.
    # Each record carries ``elapsed_ms`` (relative to session start) and
    # ``wall_ms`` (event ``created_at``) so per-turn latency waterfalls
    # can be reconstructed from logs.
    #
    # Default ON because every field logged at this level is metadata only
    # (state names, finality flags, character counts, token counts, latency
    # numbers). No PII content is emitted from this flag alone.
    log_audio_events: bool = True

    # When True, verbose content fields are added to existing records:
    # - ``audio.stt.transcribed`` includes the verbatim STT transcript
    # - ``llm.message.added`` includes the assistant/user message body
    # - ``llm.tool.executed`` includes the function-tool args + output
    # DEV / LOCAL ONLY -- candidate transcripts and LLM I/O are PII per the
    # root CLAUDE.md discipline. Must never be enabled in production.
    # Implies ``log_audio_events``.
    log_user_transcripts: bool = False
