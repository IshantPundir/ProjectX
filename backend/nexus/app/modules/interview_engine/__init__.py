"""Interview engine module — clean-slate generic-LLM harness.

The structured controller + per-question task brain was removed on
2026-05-04. This package exposes the LiveKit AgentServer entrypoint;
a future structured agent will re-attach to the same SessionConfig +
EventCollector plumbing this file already wires.

`server` is exposed via lazy ``__getattr__`` rather than an eager
``from .agent import server`` line. Eager loading would force
``agent.py`` (and its transitive imports of ``app.modules.interview_runtime``)
to run whenever any submodule under ``app.modules.interview_engine``
is imported — which creates a partial-init cycle for the
``interview_runtime.schemas → engine.models → engine.agent →
interview_runtime.schemas`` round-trip introduced when SessionResult
gained the engine-snapshot forward-ref fields. The lazy attribute
preserves the public API for any caller doing
``from app.modules.interview_engine import server``.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.interview_engine.agent import server  # noqa: F401

__all__ = ["server"]


def __getattr__(name: str) -> Any:
    if name == "server":
        from app.modules.interview_engine.agent import server as _server
        return _server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
