"""Tests for new structured-agent event kinds.

Validates that the 14 new audit-envelope event kinds for the structured agent
are registered in the ALL_EVENT_KINDS registry (added 2026-05-07).
"""
from __future__ import annotations

from app.modules.interview_engine.event_kinds import ALL_EVENT_KINDS


def test_new_engine_event_kinds_registered():
    """Asserts that all 14 new structured-agent event kinds are present
    in the ALL_EVENT_KINDS registry."""
    expected = {
        "turn.started",
        "turn.completed",
        "judge.call",
        "judge.synthetic",
        "judge.fallback",
        "judge.validation",
        "state.mutation",
        "speaker.call",
        "speaker.cached",
        "speaker.output",
        "speaker.error",
        "lifecycle.transition",
        "checkpoint.written",
        "frontend.attribute.published",
    }
    assert expected.issubset(set(ALL_EVENT_KINDS))


def test_event_kinds_unique():
    """Asserts no duplicate kind strings in the registry (copy-paste bugs
    would silently shadow entries in the frozenset)."""
    assert len(ALL_EVENT_KINDS) == len(set(ALL_EVENT_KINDS))
