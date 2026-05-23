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
    assert cfg.engine_brain_model.startswith("gpt-5.4")
    assert cfg.engine_brain_effort == "low"          # reasoning-first, low effort by design
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
    # Hold-space (mid-answer think pause) — DISABLED by default (2026-05-23):
    # the dumb 2.5s reflex interrupted think-pauses; M5's brain owns HOLD instead.
    assert cfg.engine_v2_hold_space_enabled is False
    assert cfg.engine_v2_hold_space_delay_s == 2.5
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
