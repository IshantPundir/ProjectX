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
