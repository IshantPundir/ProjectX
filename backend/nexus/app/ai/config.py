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

from app.config import settings


class AIConfig:
    @property
    def extraction_model(self) -> str:
        return settings.openai_extraction_model

    @property
    def extraction_effort(self) -> str:
        return settings.openai_extraction_effort

    @property
    def reenrichment_model(self) -> str:
        return settings.openai_reenrichment_model

    @property
    def reenrichment_effort(self) -> str:
        return settings.openai_reenrichment_effort

    # Phase 2C.2 — question generation
    @property
    def question_bank_model(self) -> str:
        return settings.openai_question_bank_model

    @property
    def question_bank_effort(self) -> str:
        return settings.openai_question_bank_effort

    @property
    def request_timeout_seconds(self) -> float:
        return settings.openai_request_timeout_seconds

    @property
    def max_schema_retries(self) -> int:
        return settings.openai_max_retries

    # Phase 3C.2 — Interview engine (realtime LLM/STT/TTS)
    @property
    def interview_llm_model(self) -> str:
        return settings.interview_llm_model

    @property
    def interview_reasoning_effort(self) -> str:
        return settings.interview_reasoning_effort

    @property
    def interview_stt_model(self) -> str:
        return settings.interview_stt_model

    @property
    def interview_stt_language(self) -> str:
        return settings.interview_stt_language

    @property
    def interview_tts_model(self) -> str:
        return settings.interview_tts_model

    @property
    def interview_tts_voice(self) -> str:
        return settings.interview_tts_voice

    @property
    def interview_tts_language(self) -> str:
        return settings.interview_tts_language

    @property
    def interview_turn_detector_unlikely_threshold(self) -> float | None:
        return settings.interview_turn_detector_unlikely_threshold

    # Phase 3D — Structured AI Screening Agent evaluators (batch OpenAI
    # calls, not realtime). Used by Sufficiency Checker (Phase D/E),
    # Intent Classifier (Phase F), and Disclaim Classifier (Phase H).
    # All three go through ``app.ai.client.get_openai_client()`` (already
    # an ``instructor.AsyncInstructor``) — see effort-gating contract in
    # this module's docstring.
    #
    # TODO(post-v1): unified field_validator across all *_effort properties
    # (extraction, reenrichment, question_bank, interview, evaluators) to
    # reject non-empty effort when paired with a *-chat-latest model — would
    # 400 every call. Today the runtime-gating contract (caller checks
    # ``if effort:`` before forwarding) is the only defense. Same risk
    # surface as ``interview_reasoning_effort``; the right scope is a single
    # validator covering all five effort properties, not just evaluators.
    @property
    def evaluator_intent_model(self) -> str:
        return settings.evaluator_intent_model

    @property
    def evaluator_intent_effort(self) -> str:
        return settings.evaluator_intent_effort

    @property
    def evaluator_disclaim_model(self) -> str:
        return settings.evaluator_disclaim_model

    @property
    def evaluator_disclaim_effort(self) -> str:
        return settings.evaluator_disclaim_effort

    @property
    def evaluator_sufficiency_model(self) -> str:
        return settings.evaluator_sufficiency_model

    @property
    def evaluator_sufficiency_effort(self) -> str:
        return settings.evaluator_sufficiency_effort


ai_config = AIConfig()
