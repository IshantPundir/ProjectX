"""AIConfig surface for the v2 engine (default version + brain/mouth model map)."""

from app.ai.config import AIConfig
from app.config import Settings


def test_default_version_is_v1():
    assert AIConfig().interview_engine_default_version == "v1"


def test_default_version_env_override(monkeypatch):
    monkeypatch.setenv("INTERVIEW_ENGINE_DEFAULT_VERSION", "v2")
    assert AIConfig().interview_engine_default_version == "v2"


def test_brain_and_mouth_model_map():
    cfg = AIConfig()
    # Brain: FAST model (gpt-5.4-mini) + reasoning-FIRST FIELD, NO reasoning_effort. A reasoning
    # model (gpt-5) paid the thinking latency and timed out the budget every turn (2026-05-24).
    assert cfg.engine_brain_model.startswith("gpt-5")
    assert cfg.engine_brain_effort == ""             # latency-first; no reasoning effort sent
    assert cfg.engine_mouth_model.startswith("gpt-5.4-mini")
    assert cfg.engine_mouth_effort == ""             # latency-first; no reasoning effort sent
    assert cfg.engine_brain_prompt_version == "v3"
    assert cfg.engine_mouth_prompt_version == "v3"
    assert cfg.engine_brain_prompt_cache_key == "brain:v1"
    assert cfg.engine_mouth_prompt_cache_key == "mouth:v1"


def test_model_overrides(monkeypatch):
    monkeypatch.setenv("ENGINE_BRAIN_MODEL", "gpt-5.5-2026-04-24")
    monkeypatch.setenv("ENGINE_BRAIN_EFFORT", "")
    cfg = AIConfig()
    assert cfg.engine_brain_model == "gpt-5.5-2026-04-24"
    assert cfg.engine_brain_effort == ""


def test_engine_v2_eou_defaults():
    cfg = AIConfig()
    # EOU / endpointing — recalibrated on the M3 talk-test (2026-05-22, R1):
    # None => MultilingualModel documented default (restores complete-vs-incomplete
    # discrimination); max_delay 10.0 gives genuine mid-answer think-pauses room.
    assert cfg.engine_v2_turn_detector_unlikely_threshold is None
    assert cfg.engine_v2_endpointing_mode == "dynamic"
    assert cfg.engine_v2_endpointing_min_delay == 0.8
    assert cfg.engine_v2_endpointing_max_delay == 10.0
    # Hold-space (mid-answer think pause) — RE-ENABLED in M5 (decision E),
    # gated on incompleteness via delay-above-commit-latency proxy (R3).
    assert cfg.engine_v2_hold_space_enabled is True
    assert cfg.engine_v2_hold_space_delay_s == 3.0
    assert cfg.engine_v2_hold_space_message == "Take your time."
    # Unresponsive ladder.
    assert cfg.engine_v2_unresponsive_prompt_1_s == 7.0
    assert cfg.engine_v2_unresponsive_prompt_2_s == 15.0
    assert cfg.engine_v2_unresponsive_max_no_responses == 2
    assert cfg.engine_v2_unresponsive_message_1 == "Whenever you're ready."
    assert cfg.engine_v2_unresponsive_message_2 == "Are you still there?"
    # Backchannel gate (mirrors the LiveKit interruption min_words).
    assert cfg.engine_v2_backchannel_min_words == 2


def test_engine_v2_eou_env_override(monkeypatch):
    monkeypatch.setenv("ENGINE_V2_TURN_DETECTOR_UNLIKELY_THRESHOLD", "0.35")
    monkeypatch.setenv("ENGINE_V2_ENDPOINTING_MAX_DELAY", "5.0")
    monkeypatch.setenv("ENGINE_V2_HOLD_SPACE_DELAY_S", "3.0")
    cfg = AIConfig()
    assert cfg.engine_v2_turn_detector_unlikely_threshold == 0.35
    assert cfg.engine_v2_endpointing_max_delay == 5.0
    assert cfg.engine_v2_hold_space_delay_s == 3.0


def test_engine_mouth_persona_name_default():
    cfg = AIConfig()
    assert cfg.engine_mouth_persona_name == "Arjun"


def test_engine_mouth_persona_name_env_override(monkeypatch):
    monkeypatch.setenv("ENGINE_MOUTH_PERSONA_NAME", "Priya")
    cfg = AIConfig()
    assert cfg.engine_mouth_persona_name == "Priya"


def test_engine_v2_ack_messages_seed_fallback():
    # Task 7: the canned ack-mask seed + fallback used when no persona pre-render is available.
    s = Settings()
    assert isinstance(s.engine_v2_ack_messages, list)
    assert len(s.engine_v2_ack_messages) >= 1               # at least one safe fallback
    assert all(isinstance(m, str) and m.strip() for m in s.engine_v2_ack_messages)
    # content-free acks only — they must commit to nothing (D3); no question marks.
    assert all("?" not in m for m in s.engine_v2_ack_messages)


def test_triage_config_defaults():
    from app.ai.config import ai_config
    assert ai_config.engine_triage_model
    # gates the immediate voice, but must cover the mini cold-start/variance tail (fe3a5434:
    # nano fell back at the old 2500ms budget) — kept under 4000ms so the filler stays prompt.
    assert ai_config.engine_triage_total_budget_ms <= 4000
    from app.config import settings
    assert settings.engine_triage_hold_cap >= 1


def test_phase2_triage_budget_and_cue_config():
    from app.ai.config import ai_config
    # mini triage: budget covers cold-start/variance (fe3a5434 fell back at 2500) but stays prompt
    assert 2000 <= ai_config.engine_triage_total_budget_ms <= 4000
    from app.config import settings
    assert settings.engine_v2_cue_cooldown_s > 0
    assert settings.engine_v2_triage_brain_disagreement_log is False  # dev-only, off by default


def test_brain_budget_covers_observed_tail():
    from app.ai.config import ai_config
    # 046f21e3: a legitimate ~6s brain decision timed out at the old 6000ms budget -> the
    # fallback_advance skipped a candidate clarification. The budget must cover the real ~3-7s
    # tail (gpt-5.4-mini low-effort) so a slow-but-valid decision lands instead of falling back.
    assert ai_config.engine_brain_total_budget_ms >= 7000
