"""Interview Engine — gen-3 rebuild in progress.

The livekit-bearing exports (`run` = the LiveKit entrypoint, `server` = the
worker bootstrap) are exported lazily via __getattr__ so importing this package
never loads livekit into the nexus/FastAPI process.

Pure gen-3 contracts (Directive, DirectiveAct, DirectiveTone) are exported
eagerly — contracts.py is livekit-free and safe to import from app.main.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Eager exports — livekit-free; safe to import from the FastAPI process.
from app.modules.interview_engine.contracts import (
    Directive,
    DirectiveAct,
    DirectiveTone,
)

if TYPE_CHECKING:  # static-only; never imported at runtime (keeps livekit out of nexus)
    from app.modules.interview_engine.agent import run as run  # noqa: F401
    from app.modules.interview_engine.agent import server as server  # noqa: F401

__all__ = ["Directive", "DirectiveAct", "DirectiveTone", "run", "server"]


def __getattr__(name: str) -> Any:  # PEP 562 — lazy livekit-bearing export
    if name == "run":
        from app.modules.interview_engine.agent import run

        return run
    if name == "server":
        from app.modules.interview_engine.agent import server

        return server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
