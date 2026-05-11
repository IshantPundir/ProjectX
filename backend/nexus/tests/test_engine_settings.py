import pytest

from app.ai.config import AIConfig
from app.config import Settings


def test_settings_engine_fields_present(monkeypatch):
    monkeypatch.setenv("ENGINE_JUDGE_MODEL", "gpt-5.4-mini-2026-03-17")
    monkeypatch.setenv("ENGINE_SPEAKER_MODEL", "gpt-5.4-mini-2026-03-17")
    monkeypatch.setenv("ENGINE_JUDGE_TOTAL_BUDGET_MS", "10000")
    monkeypatch.setenv("ENGINE_JUDGE_RETRY_WAIT_MS", "250")
    monkeypatch.setenv("ENGINE_SPEAKER_MAX_OUTPUT_TOKENS", "200")
    monkeypatch.setenv("ENGINE_CHECKPOINT_TURNS", "10")
    monkeypatch.setenv("ENGINE_CHECKPOINT_SECONDS", "30")
    monkeypatch.setenv("ENGINE_CLAIMS_POOL_MAX", "50")
    monkeypatch.setenv("ENGINE_JUDGE_PROMPT_VERSION", "v1")
    monkeypatch.setenv("ENGINE_SPEAKER_PROMPT_VERSION", "v1")

    s = Settings()
    assert s.engine_judge_model == "gpt-5.4-mini-2026-03-17"
    assert s.engine_speaker_model == "gpt-5.4-mini-2026-03-17"
    assert s.engine_judge_total_budget_ms == 10000
    assert s.engine_judge_retry_wait_ms == 250
    assert s.engine_speaker_max_output_tokens == 200
    assert s.engine_checkpoint_turns == 10
    assert s.engine_checkpoint_seconds == 30
    assert s.engine_claims_pool_max == 50
    assert s.engine_judge_prompt_version == "v1"
    assert s.engine_speaker_prompt_version == "v1"


def test_stale_settings_removed():
    """Stale fields from removed structured agent should not be on Settings."""
    s = Settings.model_fields
    for stale in (
        "engine_max_probes_per_question",
        "engine_time_warning_threshold",
        "interview_engine_jwt_secret",
    ):
        assert stale not in s


def test_aiconfig_exposes_engine_models(monkeypatch):
    monkeypatch.setenv("ENGINE_JUDGE_MODEL", "abc")
    monkeypatch.setenv("ENGINE_SPEAKER_MODEL", "def")
    cfg = AIConfig()
    assert cfg.engine_judge_model == "abc"
    assert cfg.engine_speaker_model == "def"


def test_engine_endpointing_max_delay_default_is_patient(monkeypatch):
    """The default endpointing max delay was raised from 2.5 → 6.0 in
    Phase 2 P2.2 (2026-05-08). Session 09e8fc33 showed candidate
    thinking pauses up to 22s and EOU delays p95 of 5.5s; the previous
    2.5s cap was firing turn-end mid-thought.

    The default lives in code, but the test asserts it explicitly so a
    silent regression back to a snappier value (which would re-introduce
    mid-sentence cutoffs) gets flagged.
    """
    monkeypatch.delenv("ENGINE_ENDPOINTING_MAX_DELAY", raising=False)
    s = Settings()
    assert s.engine_endpointing_max_delay == 6.0


def test_interview_turn_detector_unlikely_threshold_default_is_none(monkeypatch):
    """Phase 2 P2.2 (2026-05-08) dropped the explicit 0.15 override.
    The plugin's per-language tuned defaults (~0.3-0.5) are both more
    patient (higher threshold = require higher EOU confidence to commit
    turn-end) and more accurate than a single hand-picked override.
    """
    from app.ai.config import AIConfig

    monkeypatch.delenv("INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD", raising=False)
    cfg = AIConfig()
    assert cfg.interview_turn_detector_unlikely_threshold is None


def test_settings_have_sarvam_fields():
    """Sarvam-specific Settings fields exist with sensible defaults.

    Uses model_fields introspection to read the *coded* default rather than
    instantiating Settings — that way local-dev .env values for any of these
    keys don't interfere with the assertion.
    """
    fields = Settings.model_fields
    assert fields["sarvam_api_key"].default == ""
    assert fields["interview_stt_provider"].default == "sarvam"
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


def test_settings_default_to_sarvam_values():
    """In-code defaults select the Sarvam pipeline so a no-.env-override boot works.

    Uses model_fields introspection so local-dev .env values can't interfere
    with the assertion (consistent with test_settings_have_sarvam_fields).
    """
    fields = Settings.model_fields
    assert fields["interview_stt_provider"].default == "sarvam"
    assert fields["interview_stt_model"].default == "saaras:v3"
    assert fields["interview_stt_language"].default == "en-IN"
    assert fields["interview_stt_mode"].default == "transcribe"
    assert fields["interview_tts_provider"].default == "sarvam"
    assert fields["interview_tts_model"].default == "bulbul:v3"
    assert fields["interview_tts_voice"].default == "shubh"
    assert fields["interview_tts_language"].default == "en-IN"


def test_settings_tts_prewarm_concurrency_default_is_four():
    """Conservative default cap on the opener-cache + intro TTS burst.

    Sized to fit inside Sarvam's per-second rate-limit window on the
    starter tier; production tiers with higher limits can raise this
    via INTERVIEW_TTS_PREWARM_CONCURRENCY for faster first-session
    warmup.
    """
    assert Settings.model_fields["interview_tts_prewarm_concurrency"].default == 4


def test_settings_tts_prewarm_concurrency_rejects_below_one(monkeypatch):
    """Validator rejects values < 1 (which would deadlock the cache build)."""
    import pytest
    from pydantic import ValidationError
    monkeypatch.setenv("INTERVIEW_TTS_PREWARM_CONCURRENCY", "0")
    with pytest.raises(ValidationError, match=r"must be in \[1, 16\]"):
        Settings()


def test_settings_tts_prewarm_concurrency_rejects_above_sixteen(monkeypatch):
    """Validator rejects values > 16 (no realistic provider benefits)."""
    import pytest
    from pydantic import ValidationError
    monkeypatch.setenv("INTERVIEW_TTS_PREWARM_CONCURRENCY", "17")
    with pytest.raises(ValidationError, match=r"must be in \[1, 16\]"):
        Settings()


def test_settings_tts_prewarm_concurrency_accepts_boundary_values(monkeypatch):
    """The [1, 16] interval is inclusive on both ends."""
    monkeypatch.setenv("INTERVIEW_TTS_PREWARM_CONCURRENCY", "1")
    assert Settings().interview_tts_prewarm_concurrency == 1
    monkeypatch.setenv("INTERVIEW_TTS_PREWARM_CONCURRENCY", "16")
    assert Settings().interview_tts_prewarm_concurrency == 16


def test_aiconfig_exposes_prewarm_concurrency(monkeypatch):
    monkeypatch.setenv("INTERVIEW_TTS_PREWARM_CONCURRENCY", "6")
    cfg = AIConfig()
    assert cfg.interview_tts_prewarm_concurrency == 6


def test_settings_have_coalesce_fields():
    """Coalescing-related Settings fields exist with sensible defaults."""
    fields = Settings.model_fields
    assert fields["engine_coalesce_enabled"].default is True
    assert fields["engine_coalesce_window_ms"].default == 5000


def test_settings_coalesce_window_rejects_below_one(monkeypatch):
    import pytest
    from pydantic import ValidationError
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "0")
    with pytest.raises(ValidationError, match=r"must be in \[1, 30000\]"):
        Settings()


def test_settings_coalesce_window_rejects_above_thirty_thousand(monkeypatch):
    import pytest
    from pydantic import ValidationError
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "30001")
    with pytest.raises(ValidationError, match=r"must be in \[1, 30000\]"):
        Settings()


def test_settings_coalesce_window_accepts_boundary_values(monkeypatch):
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "1")
    assert Settings().engine_coalesce_window_ms == 1
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "30000")
    assert Settings().engine_coalesce_window_ms == 30000


def test_aiconfig_exposes_coalesce_fields(monkeypatch):
    monkeypatch.setenv("ENGINE_COALESCE_ENABLED", "false")
    monkeypatch.setenv("ENGINE_COALESCE_WINDOW_MS", "2500")
    cfg = AIConfig()
    assert cfg.engine_coalesce_enabled is False
    assert cfg.engine_coalesce_window_ms == 2500
