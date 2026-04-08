"""Env-driven AI configuration.

Single source of truth for model IDs and reasoning_effort values. Never
hardcode a model name or effort level anywhere else. Swapping a model for a
specific task is a .env change + restart, no code change.

Future phase properties (reenrichment, generation, session, scoring) are
added to this class as each phase lands — not speculatively in 2A."""

from app.config import settings


class AIConfig:
    @property
    def extraction_model(self) -> str:
        return settings.openai_extraction_model

    @property
    def extraction_effort(self) -> str:
        return settings.openai_extraction_effort

    @property
    def request_timeout_seconds(self) -> float:
        return settings.openai_request_timeout_seconds

    @property
    def max_schema_retries(self) -> int:
        return settings.openai_max_retries


ai_config = AIConfig()
