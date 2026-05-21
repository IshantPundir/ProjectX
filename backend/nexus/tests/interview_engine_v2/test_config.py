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
