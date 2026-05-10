"""Env-driven AI configuration.

Single source of truth for model IDs and reasoning_effort values. Never
hardcode a model name or effort level anywhere else. Swapping a model for a
specific task is a .env change + restart, no code change.

Future phase properties (reenrichment, generation, session, scoring) are
added to this class as each phase lands — not speculatively in 2A.

------------------------------------------------------------------
**Effort-gating contract (read before wiring a new evaluator).**

Per OpenAI's API: ``reasoning_effort`` is **not supported on
non-reasoning chat models** (``*-chat-latest``). Sending it returns
HTTP 400, which kills the call. Every ``*_effort`` property on this
class therefore follows the same discipline as
``interview_reasoning_effort`` (see ``app/ai/realtime.py::build_llm_plugin``):

  * The property defaults to empty string ``""``.
  * Caller code MUST gate on ``if ai_config.<role>_effort:`` before
    forwarding ``reasoning_effort=...`` to the OpenAI client.
  * An empty string means "do not send the parameter at all" — the
    correct contract whether the configured model is a reasoning
    model or a chat model.

Default-empty across all evaluator roles means the system is safe by
default if an operator overrides a model to a chat variant. Production
deploys opt into reasoning models per evaluator role by setting both
the ``EVALUATOR_<ROLE>_MODEL`` and ``EVALUATOR_<ROLE>_EFFORT`` env
vars together.
------------------------------------------------------------------
"""

from app.config import NoiseCancellationMode, Settings, settings


class AIConfig:
    def __init__(self, _settings: Settings | None = None) -> None:
        # When called with no arguments (the common test pattern), construct a
        # fresh Settings() so monkeypatch.setenv overrides are picked up.
        # The module-level `ai_config` singleton passes the already-constructed
        # module-level `settings` object explicitly, which is the zero-cost
        # production path — no re-parsing on every property access.
        self._settings = _settings if _settings is not None else Settings()

    @property
    def extraction_model(self) -> str:
        return self._settings.openai_extraction_model

    @property
    def extraction_effort(self) -> str:
        return self._settings.openai_extraction_effort

    @property
    def reenrichment_model(self) -> str:
        return self._settings.openai_reenrichment_model

    @property
    def reenrichment_effort(self) -> str:
        return self._settings.openai_reenrichment_effort

    # Phase 2C.2 — question generation
    @property
    def question_bank_model(self) -> str:
        return self._settings.openai_question_bank_model

    @property
    def question_bank_effort(self) -> str:
        return self._settings.openai_question_bank_effort

    @property
    def request_timeout_seconds(self) -> float:
        return self._settings.openai_request_timeout_seconds

    @property
    def max_schema_retries(self) -> int:
        return self._settings.openai_max_retries

    # Phase 3C.2 — Interview engine (realtime LLM/STT/TTS)
    @property
    def interview_llm_model(self) -> str:
        return self._settings.interview_llm_model

    @property
    def interview_reasoning_effort(self) -> str:
        return self._settings.interview_reasoning_effort

    @property
    def interview_stt_model(self) -> str:
        return self._settings.interview_stt_model

    @property
    def interview_stt_language(self) -> str:
        return self._settings.interview_stt_language

    @property
    def interview_tts_provider(self) -> str:
        return self._settings.interview_tts_provider

    @property
    def interview_tts_model(self) -> str:
        return self._settings.interview_tts_model

    @property
    def interview_tts_voice(self) -> str:
        return self._settings.interview_tts_voice

    @property
    def interview_tts_language(self) -> str:
        return self._settings.interview_tts_language

    @property
    def interview_turn_detector_unlikely_threshold(self) -> float | None:
        return self._settings.interview_turn_detector_unlikely_threshold

    @property
    def interview_noise_cancellation(self) -> NoiseCancellationMode:
        return self._settings.interview_noise_cancellation

    @property
    def interview_nc_enhancement_level(self) -> float:
        return self._settings.interview_nc_enhancement_level

    # Phase 3D — structured agent model selection
    @property
    def engine_judge_model(self) -> str:
        return self._settings.engine_judge_model

    @property
    def engine_speaker_model(self) -> str:
        return self._settings.engine_speaker_model


ai_config = AIConfig(settings)
