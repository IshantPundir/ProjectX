"""Realtime plugin factory surface (Path A+ native turn detection).

The gen-3 manual Ear used a pipecat Smart Turn v3 ONNX detector
(``build_smart_turn`` / ``_SmartTurnDetector``). Path A+ retires that in favour
of LiveKit's native turn detector (``build_turn_detector`` →
``MultilingualModel``), which the AgentSession drives off the live STT stream.
These tests pin the factory surface without loading the heavy ONNX model.
"""
from __future__ import annotations


def test_build_turn_detector_is_callable():
    from app.ai.realtime import build_turn_detector

    assert callable(build_turn_detector)


def test_smart_turn_factory_is_removed():
    import app.ai.realtime as realtime

    # The manual Smart Turn audio EOU path is gone — no stale code.
    assert not hasattr(realtime, "build_smart_turn"), (
        "build_smart_turn must be removed (Path A+ native turn detection)"
    )
    assert not hasattr(realtime, "_SmartTurnDetector"), (
        "_SmartTurnDetector must be removed (Path A+ native turn detection)"
    )
