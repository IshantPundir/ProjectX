"""Phase 1 — engine-side OTel bootstrap.

The engine container historically didn't have an OTel TracerProvider
registered, which means livekit-agents' built-in spans went nowhere even
when an OTLP endpoint was configured. Phase 1 wires a TracerProvider
into the engine's prewarm hook.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from livekit.agents import JobProcess

from app.modules.interview_engine import agent as agent_mod


def test_prewarm_bootstraps_otel_tracer_provider() -> None:
    """prewarm should call app.ai.otel.bootstrap_tracer_provider() and
    register it as the global provider."""
    proc = MagicMock(spec=JobProcess)
    proc.userdata = {}

    fake_provider = MagicMock()
    with patch.object(agent_mod, "bootstrap_tracer_provider", return_value=fake_provider) as bsp, \
         patch.object(agent_mod, "_otel_set_global_provider") as set_global:
        agent_mod.prewarm(proc)
        bsp.assert_called_once_with()
        set_global.assert_called_once_with(fake_provider)
        # Provider stashed on proc.userdata so the close path can shut it down.
        assert proc.userdata["otel_provider"] is fake_provider


def test_prewarm_still_loads_silero_vad() -> None:
    """Adding OTel must not break the existing Silero load."""
    proc = MagicMock(spec=JobProcess)
    proc.userdata = {}
    with patch.object(agent_mod.silero.VAD, "load") as vad_load, \
         patch.object(agent_mod, "bootstrap_tracer_provider", return_value=MagicMock()), \
         patch.object(agent_mod, "_otel_set_global_provider"):
        agent_mod.prewarm(proc)
        vad_load.assert_called_once()
        assert "vad" in proc.userdata
