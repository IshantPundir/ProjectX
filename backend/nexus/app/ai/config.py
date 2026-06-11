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
class therefore follows the same discipline (gate on a non-empty effort
string before forwarding ``reasoning_effort`` — see
``app/ai/realtime.py::build_mouth_llm_plugin``):

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

Exception: ``report_scorer_effort`` defaults to ``"medium"`` (non-empty)
because the report scorer is explicitly configured to use a reasoning model
(``gpt-5.1``). Any component whose default model IS a reasoning model MAY
document a non-empty effort default; all others must default to ``""``.
------------------------------------------------------------------
"""

from app.config import Settings, settings


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
    def question_bank_keyterm_model(self) -> str:
        return self._settings.openai_question_bank_keyterm_model

    @property
    def question_bank_max_questions(self) -> int:
        return self._settings.openai_question_bank_max_questions

    @property
    def question_bank_prompt_version(self) -> str:
        return self._settings.question_bank_prompt_version

    @property
    def request_timeout_seconds(self) -> float:
        return self._settings.openai_request_timeout_seconds

    @property
    def max_schema_retries(self) -> int:
        return self._settings.openai_max_retries

    # Phase 3C.2 — Interview engine (realtime STT/TTS)
    @property
    def interview_stt_provider(self) -> str:
        return self._settings.interview_stt_provider

    @property
    def interview_stt_model(self) -> str:
        return self._settings.interview_stt_model

    @property
    def interview_stt_language(self) -> str:
        return self._settings.interview_stt_language

    @property
    def interview_stt_mode(self) -> str:
        return self._settings.interview_stt_mode

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
    def interview_tts_pace(self) -> float:
        return self._settings.interview_tts_pace

    @property
    def interview_tts_temperature(self) -> float:
        return self._settings.interview_tts_temperature

    # --- Interview engine (two-plane) ---
    @property
    def engine_brain_model(self) -> str:
        return self._settings.engine_brain_model

    @property
    def engine_brain_effort(self) -> str:
        return self._settings.engine_brain_effort

    @property
    def engine_mouth_model(self) -> str:
        return self._settings.engine_mouth_model

    @property
    def engine_mouth_effort(self) -> str:
        return self._settings.engine_mouth_effort

    @property
    def engine_brain_prompt_version(self) -> str:
        return self._settings.engine_brain_prompt_version

    @property
    def engine_mouth_prompt_version(self) -> str:
        return self._settings.engine_mouth_prompt_version

    @property
    def engine_brain_total_budget_ms(self) -> int:
        return self._settings.engine_brain_total_budget_ms

    @property
    def engine_triage_model(self) -> str:
        return self._settings.engine_triage_model

    @property
    def engine_triage_effort(self) -> str:
        return self._settings.engine_triage_effort

    @property
    def engine_triage_prompt_version(self) -> str:
        return self._settings.engine_triage_prompt_version

    @property
    def engine_triage_total_budget_ms(self) -> int:
        return self._settings.engine_triage_total_budget_ms

    @property
    def engine_brain_prompt_cache_key(self) -> str:
        return self._settings.engine_brain_prompt_cache_key

    @property
    def engine_mouth_prompt_cache_key(self) -> str:
        return self._settings.engine_mouth_prompt_cache_key

    @property
    def engine_mouth_persona_name(self) -> str:
        return self._settings.engine_mouth_persona_name

    # --- Interview engine — turn handling (gen-3 native turn detection) ---
    # Single source of truth: turn-detector patience + endpointing delays.
    # `unlikely_threshold=None` delegates to the MultilingualModel's per-language
    # tuned default (see config.py for the verified LiveKit endpointing semantics).
    @property
    def engine_turn_detector_unlikely_threshold(self) -> float | None:
        return self._settings.engine_turn_detector_unlikely_threshold

    @property
    def engine_endpointing_mode(self) -> str:
        return self._settings.engine_endpointing_mode

    @property
    def engine_endpointing_min_delay_s(self) -> float:
        return self._settings.engine_endpointing_min_delay_s

    @property
    def engine_endpointing_max_delay_s(self) -> float:
        return self._settings.engine_endpointing_max_delay_s

    @property
    def engine_probe_cap_per_thread(self) -> int:
        return self._settings.engine_probe_cap_per_thread

    @property
    def engine_assembly_enabled(self) -> bool:
        return self._settings.engine_assembly_enabled

    @property
    def engine_assembly_grace_s(self) -> float:
        return self._settings.engine_assembly_grace_s

    @property
    def engine_assembly_max_duration_s(self) -> float:
        return self._settings.engine_assembly_max_duration_s

    @property
    def engine_bridge_timeout_s(self) -> float:
        return self._settings.engine_bridge_timeout_s

    @property
    def engine_stall_reposes_before_advance(self) -> int:
        return self._settings.engine_stall_reposes_before_advance

    # --- Reporting — offline report scorer (Phase 3D+ post-session) ---
    @property
    def report_scorer_model(self) -> str:
        return self._settings.openai_report_scorer_model

    @property
    def report_scorer_effort(self) -> str:
        return self._settings.openai_report_scorer_effort

    @property
    def report_scorer_prompt_version(self) -> str:
        return self._settings.report_scorer_prompt_version

    @property
    def report_scorer_prompt_cache_key_prefix(self) -> str:
        return self._settings.report_scorer_prompt_cache_key_prefix

    # --- Reel Director (candidate-reel EDL selection) ---
    @property
    def reel_director_model(self) -> str:
        return self._settings.openai_reel_director_model

    @property
    def reel_director_effort(self) -> str:
        return self._settings.openai_reel_director_effort

    @property
    def reel_director_prompt_version(self) -> str:
        return self._settings.reel_director_prompt_version

    @property
    def reel_director_prompt_cache_key_prefix(self) -> str:
        return self._settings.reel_director_prompt_cache_key_prefix

    @property
    def report_narrative_model(self) -> str:
        return self._settings.openai_report_narrative_model

    @property
    def report_narrative_effort(self) -> str:
        return self._settings.openai_report_narrative_effort

    # --- Gen-3 deterministic resolver time-budget knobs ---
    # [VALIDATE] F3-tuned defaults; tune empirically on talk-tests.
    @property
    def engine_close_reserve_s(self) -> float:
        return self._settings.engine_close_reserve_s

    @property
    def engine_winding_down_s(self) -> float:
        return self._settings.engine_winding_down_s


ai_config = AIConfig(settings)
