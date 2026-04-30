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
    # Interview-tuned: candidates pause to think mid-answer.
    endpointing_min_delay: float = 0.5
    endpointing_max_delay: float = 6.0

    # -- Nexus internal API ------------------------------------------------
    # Compose-network hostname. Override via NEXUS_INTERNAL_BASE_URL env.
    nexus_internal_base_url: str = "http://nexus:8000"

    # -- Results fallback --------------------------------------------------
    # The engine POSTs SessionResult to nexus's /api/internal/sessions/{id}/results.
    # On POST failure (3 retries exhausted), it writes JSON to this directory
    # so the result isn't lost. Alerted via structlog CRITICAL.
    results_fallback_dir: Path = Path("/tmp/interview_results")

    # -- Audio pipeline observability --------------------------------------
    # When True, emit structlog records for every STT/VAD/EOU/agent-state
    # event during the session. Useful when debugging "the agent isn't
    # hearing me" or "the transcripts look wrong" in noisy environments.
    # Always logs the SHAPE of each event (state transitions, character
    # counts, latency metrics). The actual transcript text is only logged
    # when ``log_user_transcripts`` is also True (gated separately because
    # raw transcripts are PII per the root CLAUDE.md PII discipline rule).
    log_audio_events: bool = False

    # When True, ``audio.stt.transcribed`` records include the verbatim
    # transcript field. DEV / LOCAL ONLY -- raw transcripts are PII and
    # must never be enabled in production. Implies ``log_audio_events``.
    log_user_transcripts: bool = False
