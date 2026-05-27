"""build_turn_detector accepts an explicit threshold override (v2) while the
no-arg v1 call still reads AIConfig. MultilingualModel pulls livekit native deps,
so we stub it via sys.modules to capture the constructor kwargs without loading
the real plugin (avoids the PyO3/3.13 segfault).
"""

import sys
import types

import pytest


@pytest.fixture
def captured(monkeypatch):
    """Stub livekit.plugins.turn_detector.multilingual.MultilingualModel."""
    calls: list[dict] = []

    class _FakeModel:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    mod = types.ModuleType("livekit.plugins.turn_detector.multilingual")
    mod.MultilingualModel = _FakeModel
    for name in ("livekit", "livekit.plugins", "livekit.plugins.turn_detector"):
        sys.modules.setdefault(name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "livekit.plugins.turn_detector.multilingual", mod)
    return calls


def test_v1_call_reads_aiconfig_default(captured):
    from app.ai import realtime
    realtime.build_turn_detector()  # no arg -> AIConfig default (0.5)
    assert captured[-1] == {"unlikely_threshold": 0.5}


def test_v2_override_used(captured):
    from app.ai import realtime
    realtime.build_turn_detector(unlikely_threshold=0.35)
    assert captured[-1] == {"unlikely_threshold": 0.35}


def test_explicit_none_uses_model_default(captured):
    from app.ai import realtime
    realtime.build_turn_detector(unlikely_threshold=None)
    assert captured[-1] == {}   # MultilingualModel() with no kwargs
