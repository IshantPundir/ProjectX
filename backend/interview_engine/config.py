"""Interview engine configuration.

All settings are env-driven.  The interview engine runs as a separate
process from Nexus with its own .env file.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class InterviewEngineConfig(BaseSettings):
    """Singleton-style settings object loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # -- Context source ----------------------------------------------------
    context_source: Literal["fixture", "room_metadata", "nexus_api"] = "fixture"
    fixture_path: str = "fixtures/sample_session.json"

    # -- Results output ----------------------------------------------------
    results_dir: str = "results"

    # -- LLM (via LiveKit inference gateway) -------------------------------
    interview_llm_model: str = "openai/gpt-5.3-chat-latest"
    interview_reasoning_effort: str = "low"

    # -- TTS ---------------------------------------------------------------
    tts_model: str = "cartesia/sonic-3"
    tts_voice: str = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
    tts_language: str = "en"

    # -- STT ---------------------------------------------------------------
    stt_model: str = "deepgram/nova-3"
    stt_language: str = "en"

    # -- Interview constraints ---------------------------------------------
    max_probes_per_question: int = 2
    time_warning_threshold: float = 0.8  # warn at 80% elapsed

    # -- Agent identity ----------------------------------------------------
    agent_name: str = "Dakota"
