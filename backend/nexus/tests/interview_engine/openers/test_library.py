"""Unit tests for the OpenerLibrary module."""
from app.modules.interview_engine.openers.library import (
    OpenerSelection, OpenerVariant, SubContext,
)


def test_subcontext_enum_values():
    """Lock the set of sub-context discriminators per spec §4.1."""
    expected = {
        "default", "post_cap_advance",
        "social_or_greeting", "off_topic", "abusive", "injection",
        "vague_answer", "deflection", "missing_specifics",
        "unanswered_subquestion", "knockout",
    }
    assert {s.value for s in SubContext} == expected


def test_opener_variant_default_audio_none():
    v = OpenerVariant(text="Got it.")
    assert v.text == "Got it."
    assert v.audio_frames is None


def test_opener_selection_carries_text_and_audio_iter():
    """OpenerSelection wraps the chosen variant for orchestrator use."""
    sel = OpenerSelection(text="Got it.", audio_iter=None)
    assert sel.text == "Got it."
    assert sel.audio_iter is None
