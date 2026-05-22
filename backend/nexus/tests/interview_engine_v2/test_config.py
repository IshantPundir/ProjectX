"""AIConfig surface for the v2 engine (default version + brain/mouth model map)."""

import pytest

from app.ai.config import AIConfig


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
    # EOU / endpointing — v2 starts at v1's current values (isolated knobs).
    assert cfg.engine_v2_turn_detector_unlikely_threshold == 0.5
    assert cfg.engine_v2_endpointing_mode == "dynamic"
    assert cfg.engine_v2_endpointing_min_delay == 0.8
    assert cfg.engine_v2_endpointing_max_delay == 4.5
    # Hold-space (mid-answer think pause).
    assert cfg.engine_v2_hold_space_enabled is True
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
