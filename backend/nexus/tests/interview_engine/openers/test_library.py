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


from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.openers.library import OpenerLibrary


def test_library_has_variants_for_every_required_pair():
    """Lock the (InstructionKind, SubContext) pairs that MUST have at least
    one variant. Missing any of these would cause runtime failures when
    the orchestrator tries to pick an opener for that pair."""
    lib = OpenerLibrary()
    required_pairs = [
        # deliver_question
        (InstructionKind.deliver_question, SubContext.DEFAULT),
        (InstructionKind.deliver_question, SubContext.POST_CAP_ADVANCE),
        # deliver_probe
        (InstructionKind.deliver_probe, SubContext.DEFAULT),
        # push_back — all four reason codes
        (InstructionKind.push_back, SubContext.VAGUE_ANSWER),
        (InstructionKind.push_back, SubContext.DEFLECTION),
        (InstructionKind.push_back, SubContext.MISSING_SPECIFICS),
        (InstructionKind.push_back, SubContext.UNANSWERED_SUBQUESTION),
        # clarify
        (InstructionKind.clarify, SubContext.DEFAULT),
        # redirect — all flag combinations
        (InstructionKind.redirect, SubContext.SOCIAL_OR_GREETING),
        (InstructionKind.redirect, SubContext.OFF_TOPIC),
        (InstructionKind.redirect, SubContext.ABUSIVE),
        (InstructionKind.redirect, SubContext.INJECTION),
        # acknowledge_no_experience
        (InstructionKind.acknowledge_no_experience, SubContext.DEFAULT),
        # polite_close
        (InstructionKind.polite_close, SubContext.KNOCKOUT),
        # repeat
        (InstructionKind.repeat, SubContext.DEFAULT),
    ]
    for kind, sub_ctx in required_pairs:
        variants = lib._variants_for(kind, sub_ctx)
        assert len(variants) >= 1, (
            f"({kind.value}, {sub_ctx.value}) has no variants — orchestrator "
            "will fail when this combination is selected"
        )


def test_library_vocabulary_does_not_use_chatbot_register():
    """Anti-regression: forbid service-industry phrases that crept in
    during early drafts (per brainstorming feedback)."""
    lib = OpenerLibrary()
    forbidden = ["Happy to", "Of course!", "No problem.", "Sure thing"]
    all_text = []
    for variants in lib._vocabulary.values():
        for v in variants:
            all_text.append(v.text)
    for phrase in forbidden:
        for text in all_text:
            assert phrase not in text, (
                f"Chatbot register leak: {phrase!r} appears in {text!r}"
            )


def test_pick_returns_opener_for_known_pair():
    lib = OpenerLibrary()
    sel = lib.pick(
        kind=InstructionKind.push_back,
        sub_context=SubContext.VAGUE_ANSWER,
        recent_openers=[],
    )
    assert sel.text in {
        "Got it.", "OK.", "Right —", "Mhm —", "Hmm —",
        "OK, let me press on that —",
    }
    # audio_iter is None in tests because cache hasn't been built.
    assert sel.audio_iter is None


def test_pick_excludes_recent_openers():
    """If 5 of 6 variants are in recent_openers, pick must return the 6th."""
    lib = OpenerLibrary()
    recent = ["Got it.", "OK.", "Right —", "Mhm —", "Hmm —"]
    sel = lib.pick(
        kind=InstructionKind.push_back,
        sub_context=SubContext.VAGUE_ANSWER,
        recent_openers=recent,
    )
    assert sel.text == "OK, let me press on that —"


def test_pick_falls_back_when_exclusion_empties_pool():
    """When ALL variants are in recent_openers, pick must still return
    something (the longest-ago entry — first in recent_openers)."""
    lib = OpenerLibrary()
    all_variants = [
        "Got it.", "OK.", "Right —", "Mhm —", "Hmm —",
        "OK, let me press on that —",
    ]
    sel = lib.pick(
        kind=InstructionKind.push_back,
        sub_context=SubContext.VAGUE_ANSWER,
        recent_openers=all_variants,
    )
    # Returns the longest-ago entry from recent_openers (first in list).
    assert sel.text == "Got it."


def test_pick_falls_back_to_default_when_subcontext_missing():
    """Sub-context with no variants falls back to DEFAULT."""
    lib = OpenerLibrary()
    # deliver_question has no variants for SOCIAL_OR_GREETING; should
    # fall back to deliver_question DEFAULT.
    sel = lib.pick(
        kind=InstructionKind.deliver_question,
        sub_context=SubContext.SOCIAL_OR_GREETING,
        recent_openers=[],
    )
    expected = {
        "Got it.", "Understood.", "Right.", "OK.", "Mhm.",
        "Thanks for walking me through that.", "Thanks.",
    }
    assert sel.text in expected


def test_pick_returns_text_none_when_kind_has_no_variants():
    """deliver_first_question has no library variants — opener IS the
    persona intro. pick() returns OpenerSelection(text=None)."""
    lib = OpenerLibrary()
    sel = lib.pick(
        kind=InstructionKind.deliver_first_question,
        sub_context=SubContext.DEFAULT,
        recent_openers=[],
    )
    assert sel.text is None
    assert sel.audio_iter is None
