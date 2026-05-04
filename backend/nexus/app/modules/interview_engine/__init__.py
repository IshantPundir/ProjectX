"""Interview engine module — clean-slate generic-LLM harness.

The structured controller + per-question task brain was removed on
2026-05-04. This package now exposes only the LiveKit AgentServer
entrypoint; a future structured agent will re-attach to the same
SessionConfig + EventCollector plumbing this file already wires.
"""

from app.modules.interview_engine.agent import server  # noqa: F401

__all__ = ["server"]
