"""Tests for the pure turn-taking helpers (gen-2 parity, no LLM)."""
from __future__ import annotations

import pytest

from app.modules.interview_engine.turn_taking import is_backchannel


@pytest.mark.parametrize("text", [
    "yeah", "Mm", "uh-huh", "okay", "haan", "achha", "right", "sure",
    "Mm-hmm.", "Yeah, yeah.", "okay okay", "  hmm  ", "",
])
def test_pure_backchannel_is_detected(text):
    assert is_backchannel(text) is True


@pytest.mark.parametrize("text", [
    "threshold value",          # a real fragment — must NOT be dropped
    "No. No. Please continue.", # "no" is not a backchannel token (yes/no answers protected)
    "Yes, Python.",
    "Workato, around one and a half years.",
    "So, like,",                # a fragment with real words
    "What was the question?",
])
def test_real_turns_are_not_backchannel(text):
    assert is_backchannel(text) is False
