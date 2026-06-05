"""Interview Engine — gen-3 rebuild in progress.

The livekit-bearing exports (`run` = the LiveKit entrypoint, `server` = the
worker bootstrap) are exported lazily via __getattr__ so importing this package
never loads livekit into the nexus/FastAPI process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # static-only; never imported at runtime (keeps livekit out of nexus)
    from app.modules.interview_engine.agent import run as run  # noqa: F401
    from app.modules.interview_engine.agent import server as server  # noqa: F401

__all__ = ["run", "server"]


def __getattr__(name: str) -> Any:  # PEP 562 — lazy livekit-bearing export
    if name == "run":
        from app.modules.interview_engine.agent import run

        return run
    if name == "server":
        from app.modules.interview_engine.agent import server

        return server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
