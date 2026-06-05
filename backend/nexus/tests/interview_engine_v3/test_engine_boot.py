"""F2 — CI-style boot smoke for the gen-3 engine.

Imports the FULL livekit-bearing `agent.py` (which transitively wires the Ear,
the per-turn loop, the brain, the mouth, and the SessionDriver) and asserts the
worker/entrypoint surface + the F1 drive wiring are present. This catches
import/wiring regressions without needing a live LiveKit connection or the ONNX
models (those load at prewarm / first use, exercised in the F3 talk-test).

This test pulls livekit into the process, so it is NOT the `app.main`
livekit-isolation test (that one lives in test_engine_imports.py and runs in a
subprocess).
"""

from __future__ import annotations


def test_engine_agent_module_boot_imports():
    import app.modules.interview_engine.agent as agent

    # Worker/entrypoint bootstrap surface (preserved from the gen-2 infra).
    for sym in ("server", "run", "entrypoint", "_run_entrypoint", "prewarm", "_drive"):
        assert hasattr(agent, sym), f"agent.py missing {sym}"

    # Gen-3 Ear glue.
    for sym in ("build_ear", "setup_ear", "_EarAgent"):
        assert hasattr(agent, sym), f"agent.py missing Ear glue {sym}"

    # The AgentServer is constructed at import (the LiveKit worker entrypoint).
    assert agent.server.__class__.__name__ == "AgentServer"


def test_engine_drive_wiring_imports():
    # The F1 SessionDriver + factory import cleanly (they tie together notes,
    # brain, mouth, bridge, resolver, provenance + record_session_evidence).
    from app.modules.interview_engine.driver import SessionDriver, build_session_driver

    assert callable(build_session_driver)
    assert isinstance(SessionDriver, type)


def test_drive_stub_is_gone():
    # _drive must no longer raise NotImplementedError — it is the real loop now.
    import inspect

    from app.modules.interview_engine import agent

    src = inspect.getsource(agent._drive)
    assert "NotImplementedError" not in src, "_drive still raises NotImplementedError (F1 not wired)"
