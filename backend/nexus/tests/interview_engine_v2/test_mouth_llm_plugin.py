"""build_mouth_llm_plugin passes engine_mouth_model + prompt_cache_key, and forwards
reasoning_effort ONLY when engine_mouth_effort is non-empty (the AIConfig contract).
openai.LLM pulls native deps, so stub it to capture ctor kwargs."""

import sys
import types

import pytest


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    class _FakeLLM:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    mod = types.ModuleType("livekit.plugins.openai")
    mod.LLM = _FakeLLM
    for name in ("livekit", "livekit.plugins"):
        sys.modules.setdefault(name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "livekit.plugins.openai", mod)
    return calls


def test_mouth_llm_uses_engine_mouth_model_and_cache_key(captured, monkeypatch):
    monkeypatch.setenv("ENGINE_MOUTH_MODEL", "gpt-5.4-mini-2026-03-17")
    monkeypatch.setenv("ENGINE_MOUTH_EFFORT", "")          # default: no reasoning_effort
    monkeypatch.setenv("ENGINE_MOUTH_PROMPT_CACHE_KEY", "mouth:v1")
    # Rebuild AIConfig so the env overrides take effect.
    from app.ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "ai_config", cfg_mod.AIConfig())
    from app.ai import realtime
    monkeypatch.setattr(realtime, "ai_config", cfg_mod.ai_config)

    realtime.build_mouth_llm_plugin()
    kw = captured[-1]
    assert kw["model"] == "gpt-5.4-mini-2026-03-17"
    assert kw["prompt_cache_key"] == "mouth:v1"
    assert "reasoning_effort" not in kw           # empty effort -> omitted


def test_mouth_llm_forwards_effort_when_set(captured, monkeypatch):
    monkeypatch.setenv("ENGINE_MOUTH_EFFORT", "low")
    from app.ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "ai_config", cfg_mod.AIConfig())
    from app.ai import realtime
    monkeypatch.setattr(realtime, "ai_config", cfg_mod.ai_config)

    realtime.build_mouth_llm_plugin()
    assert captured[-1]["reasoning_effort"] == "low"
