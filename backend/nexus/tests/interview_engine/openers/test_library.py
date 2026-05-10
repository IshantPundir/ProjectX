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
