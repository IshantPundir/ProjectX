"""Interview engine — LiveKit Agent worker (in-process with nexus).

This module is the per-session voice agent dispatched by LiveKit when
nexus's /api/candidate-session/{token}/start mints a candidate room and
publishes a CreateAgentDispatchRequest.

Phase 3 of the modular-monolith uplift moved this module into nexus's
source tree and replaced the HTTP boundary at /api/internal/* with
direct in-process calls into ``app.modules.interview_runtime.service``.

Run as: ``python -m app.modules.interview_engine`` (see __main__.py).
"""

from app.modules.interview_engine.agent import server  # noqa: F401

__all__ = ["server"]
