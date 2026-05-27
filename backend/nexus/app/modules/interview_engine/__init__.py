"""Interview Engine — two-plane hybrid (brain + mouth + Directive).

The decision/conversation core (see
docs/superpowers/plans/2026-05-22-interview-engine-v2-master-plan.md). Dispatch
to the engine is unconditional — there is no longer a version-selection branch.

Pure artifacts (no livekit) are exported eagerly. `run` (the LiveKit entrypoint,
which pulls livekit) is exported lazily via __getattr__ so importing the pure
artifacts never loads livekit into the nexus/FastAPI process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.interview_engine.audit import TurnDecisionRecord
from app.modules.interview_engine.controller import DirectiveController
from app.modules.interview_engine.directive import (
    Directive,
    DirectiveAct,
    DirectiveTone,
)

if TYPE_CHECKING:  # static-only; never imported at runtime (keeps livekit out of nexus)
    from app.modules.interview_engine.agent import run as run  # noqa: F401
    from app.modules.interview_engine.agent import server as server  # noqa: F401

__all__ = [
    "Directive",
    "DirectiveAct",
    "DirectiveTone",
    "DirectiveController",
    "TurnDecisionRecord",
    "run",
    "server",
]


def __getattr__(name: str) -> Any:  # PEP 562 — lazy livekit-bearing export
    if name == "run":
        from app.modules.interview_engine.agent import run

        return run
    if name == "server":
        from app.modules.interview_engine.agent import server

        return server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
