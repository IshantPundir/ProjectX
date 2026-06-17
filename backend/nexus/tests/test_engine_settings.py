from app.ai.config import AIConfig
from app.config import Settings


def test_stale_settings_removed():
    """Stale fields from removed structured agent should not be on Settings."""
    s = Settings.model_fields
    for stale in (
        "engine_max_probes_per_question",
        "engine_time_warning_threshold",
        "interview_engine_jwt_secret",
        "engine_judge_model",
        "engine_speaker_model",
        "engine_judge_prompt_version",
        "engine_speaker_prompt_version",
        "engine_claims_pool_max",
        "engine_endpointing_max_delay",
        "interview_llm_model",
        "interview_reasoning_effort",
        "interview_turn_detector_unlikely_threshold",
        "interview_engine_default_version",
    ):
        assert stale not in s


def test_settings_have_sarvam_fields():
    """Sarvam-specific Settings fields exist with sensible defaults.

    Uses model_fields introspection to read the *coded* default rather than
    instantiating Settings — that way local-dev .env values for any of these
    keys don't interfere with the assertion.
    """
    fields = Settings.model_fields
    assert fields["sarvam_api_key"].default == ""
    # STT default flipped to "deepgram" on 2026-05-19 (see spec
    # docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md).
    # Sarvam-specific fields stay for the switchable-alternate path.
    assert fields["interview_stt_mode"].default == "transcribe"
    assert fields["interview_tts_pace"].default == 1.0
    assert fields["interview_tts_temperature"].default == 0.6


def test_settings_tts_provider_accepts_sarvam(monkeypatch):
    """Widened Literal accepts sarvam alongside openai/cartesia."""
    monkeypatch.setenv("INTERVIEW_TTS_PROVIDER", "sarvam")
    s = Settings()
    assert s.interview_tts_provider == "sarvam"


def test_settings_stt_provider_accepts_deepgram(monkeypatch):
    """interview_stt_provider Literal accepts deepgram (rollback path)."""
    monkeypatch.setenv("INTERVIEW_STT_PROVIDER", "deepgram")
    s = Settings()
    assert s.interview_stt_provider == "deepgram"


def test_aiconfig_exposes_sarvam_fields(monkeypatch):
    monkeypatch.setenv("INTERVIEW_STT_PROVIDER", "sarvam")
    monkeypatch.setenv("INTERVIEW_STT_MODE", "codemix")
    monkeypatch.setenv("INTERVIEW_TTS_PACE", "1.2")
    monkeypatch.setenv("INTERVIEW_TTS_TEMPERATURE", "0.4")
    cfg = AIConfig()
    assert cfg.interview_stt_provider == "sarvam"
    assert cfg.interview_stt_mode == "codemix"
    assert cfg.interview_tts_pace == 1.2
    assert cfg.interview_tts_temperature == 0.4


def test_settings_default_to_deepgram_stt_sarvam_tts():
    """Default STT is Deepgram nova-3 (2026-05-19); TTS stays Sarvam bulbul:v3.

    Migration: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md.
    STT was flipped because Sarvam mistranscribed tech vocabulary; Deepgram
    nova-3 pairs with per-bank LLM-extracted keyterm prompting cached on
    stage_question_banks.extracted_keyterms. TTS quality was fine, so Sarvam
    bulbul:v3 remains the TTS default.

    Uses model_fields introspection so local-dev .env values can't interfere
    with the assertion.
    """
    fields = Settings.model_fields
    # STT — flipped to Deepgram on 2026-05-19
    assert fields["interview_stt_provider"].default == "deepgram"
    assert fields["interview_stt_model"].default == "nova-3"
    assert fields["interview_stt_language"].default == "en-IN"
    # Sarvam-only mode kept; only consulted when toggled back via env
    assert fields["interview_stt_mode"].default == "transcribe"
    # TTS — unchanged
    assert fields["interview_tts_provider"].default == "sarvam"
    assert fields["interview_tts_model"].default == "bulbul:v3"
    assert fields["interview_tts_voice"].default == "shubh"
    assert fields["interview_tts_language"].default == "en-IN"


def test_turn_assembly_settings_defaults():
    """Turn-assembly knobs exist with the design-spec defaults.

    grace_s raised 0.5 → 0.7 (2026-06-17 patience pass) so a continuation that
    lands just after a fragment commits merges in rather than the brain reacting
    to a partial answer.
    """
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["engine_assembly_enabled"].default is True
    assert fields["engine_assembly_grace_s"].default == 0.7
    assert fields["engine_assembly_max_duration_s"].default == 45.0


def test_aiconfig_exposes_turn_assembly(monkeypatch):
    from app.ai.config import ai_config

    assert ai_config.engine_assembly_enabled is True
    assert ai_config.engine_assembly_grace_s == 0.7
    assert ai_config.engine_assembly_max_duration_s == 45.0


def test_endpointing_patience_defaults():
    """Endpointing + VAD-silence defaults reflect the 2026-06-17 patience pass.

    For Indian candidates who pause to think mid-answer:
      * min_delay 1.5 → 1.8 — modest floor bump (the turn-detector model handles
        think-pauses via max_delay, so min_delay only taxes confident-complete
        turns; kept tight on purpose).
      * max_delay 4.0 → 7.0 — THE patience lever: the wait when the detector reads
        the turn as unfinished (a mid-thought pause).
      * vad_min_silence 0.8 — Silero declares end-of-speech only after 0.8s of
        silence (default 0.55), so brief disfluent pauses never trigger turn close.
    Mode stays "dynamic"; unlikely_threshold stays None (model per-language default).
    """
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["engine_endpointing_mode"].default == "dynamic"
    assert fields["engine_endpointing_min_delay_s"].default == 1.8
    assert fields["engine_endpointing_max_delay_s"].default == 7.0
    assert fields["engine_turn_detector_unlikely_threshold"].default is None
    assert fields["engine_vad_min_silence_s"].default == 0.8


def test_aiconfig_exposes_endpointing_and_vad(monkeypatch):
    from app.ai.config import ai_config

    assert ai_config.engine_endpointing_mode == "dynamic"
    assert ai_config.engine_endpointing_min_delay_s == 1.8
    assert ai_config.engine_endpointing_max_delay_s == 7.0
    assert ai_config.engine_vad_min_silence_s == 0.8

