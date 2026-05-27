"""Interview Engine — the three-tier conversation/control core.

A `triage` plane (fast turn classification + the immediate spoken beat) runs in
parallel with the `brain` (async control plane: rubric grading, signal coverage,
policy gates, emits one `Directive`); the `mouth` renders that directive as
natural spoken Indian English. The entrypoint builds the SessionConfig and runs
the engine unconditionally — there is a single engine, no version branch.

Pure artifacts (no livekit) are exported eagerly. The livekit-bearing exports —
`run` (the LiveKit entrypoint) and `server` (the worker bootstrap) — are exported
lazily via __getattr__ so importing the pure artifacts never loads livekit into
the nexus/FastAPI process.
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
