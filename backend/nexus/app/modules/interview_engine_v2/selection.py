"""Pure engine-version selector (no livekit). Lives apart from agent.py so the
legacy entrypoint can import it via the public API without pulling livekit, and so
the dispatch rule is unit-testable in isolation.
"""

from __future__ import annotations


def should_run_v2(config: object) -> bool:
    """True iff this session should run on the v2 engine. Defensive default: a config
    missing the field stays on v1."""
    return getattr(config, "interview_engine_version", "v1") == "v2"
