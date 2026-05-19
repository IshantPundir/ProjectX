from app.modules.interview_engine.models.speaker import InstructionKind, SpeakerInput
from app.modules.interview_engine.models.claims import ClaimEntry
from app.modules.interview_runtime.schemas import TranscriptEntry


def test_instruction_kind_values():
    expected = {
        "intro_brief",
        "deliver_first_question",
        "deliver_question",
        "deliver_probe",
        "clarify",
        "repeat",
        "redirect",
        "acknowledge_no_experience",
        "polite_close",
        "push_back",
    }
    assert {k.value for k in InstructionKind} == expected


def test_instruction_kind_redirect_value():
    from app.modules.interview_engine.models.speaker import InstructionKind
    assert InstructionKind.redirect.value == "redirect"


def test_speaker_input_accepts_turn_metadata():
    from app.modules.interview_engine.models.judge import TurnMetadata
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    si = SpeakerInput(
        instruction_kind=InstructionKind.redirect,
        bank_text="Walk me through your Jira workflow design.",
        last_candidate_utterance="Hi",
        persona_name="Sam",
        candidate_name="Ishant",
        turn_metadata=TurnMetadata(candidate_social_or_greeting=True),
    )
    assert si.turn_metadata is not None
    assert si.turn_metadata.candidate_social_or_greeting is True


def test_speaker_input_recent_turns_uncapped():
    """The 8-turn cap is removed; SpeakerInput accepts arbitrary length."""
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    from app.modules.interview_runtime import TranscriptEntry
    long_history = [
        TranscriptEntry(
            role="agent" if i % 2 == 0 else "candidate",
            text=f"turn {i}",
            timestamp_ms=i * 1000,
            question_id=None,
        )
        for i in range(50)
    ]
    si = SpeakerInput(
        instruction_kind=InstructionKind.deliver_question,
        bank_text="Q",
        recent_turns=long_history,
        persona_name="Sam",
    )
    assert len(si.recent_turns) == 50


def test_speaker_input_minimum_fields():
    s = SpeakerInput(
        instruction_kind=InstructionKind.deliver_first_question,
        bank_text="What is your experience with X?",
        last_candidate_utterance=None,
        recent_turns=[],
        claims_pool_snapshot=[],
        persona_name="Sam",
    )
    assert s.failed_signal_value is None


def test_speaker_input_for_acknowledge_no_experience_carries_failed_signal():
    s = SpeakerInput(
        instruction_kind=InstructionKind.acknowledge_no_experience,
        bank_text=None,
        last_candidate_utterance="I've never used JQL.",
        recent_turns=[],
        claims_pool_snapshot=[],
        persona_name="Sam",
        failed_signal_value="JQL fluency",
    )
    assert s.failed_signal_value == "JQL fluency"


def test_speaker_input_candidate_name_optional_default_none():
    s = SpeakerInput(
        instruction_kind=InstructionKind.deliver_first_question,
        bank_text="What is your experience?",
        last_candidate_utterance=None,
        recent_turns=[], claims_pool_snapshot=[],
        persona_name="Sam",
    )
    assert s.candidate_name is None


def test_speaker_input_candidate_name_carries_through():
    s = SpeakerInput(
        instruction_kind=InstructionKind.deliver_first_question,
        bank_text="What is your experience?",
        last_candidate_utterance=None,
        recent_turns=[], claims_pool_snapshot=[],
        persona_name="Sam",
        candidate_name="Alice",
    )
    assert s.candidate_name == "Alice"


def test_speaker_input_has_no_rubric_fields():
    """Anti-leak guarantee: SpeakerInput must NEVER carry rubric content.

    The Judge sees rubric and decides; the Speaker sees only what the State Engine
    prepared. The input builder enforces this via field-level discipline; this test
    locks it in at the model level so a future developer who adds a "convenience"
    field is caught by CI.
    """
    forbidden = {
        "anchors",
        "positive_evidence",
        "red_flags",
        "signal_metadata",
        "evaluation_hint",
        "rubric",
    }
    assert not forbidden & set(SpeakerInput.model_fields)


def test_speaker_input_has_no_opener_layer_fields():
    """The opener layer was removed in 2026-05-11. SpeakerInput must not
    expose pre_spoken_opener (which used to inform the Speaker which
    canned opener had pre-played). The Speaker now owns the entire
    utterance per turn."""
    forbidden = {"pre_spoken_opener"}
    assert not forbidden & set(SpeakerInput.model_fields)


def test_speaker_input_recent_reply_starts_field_default_empty():
    """recent_reply_starts is the anti-repetition signal that replaced
    the orchestrator-managed opener variety. Default empty list."""
    s = SpeakerInput(
        instruction_kind=InstructionKind.deliver_first_question,
        bank_text="What is your experience with X?",
        last_candidate_utterance=None,
        recent_turns=[],
        claims_pool_snapshot=[],
        persona_name="Sam",
    )
    assert s.recent_reply_starts == []


def test_speaker_input_recent_reply_starts_carries_through():
    s = SpeakerInput(
        instruction_kind=InstructionKind.push_back,
        bank_text="What about X?",
        last_candidate_utterance="Vague answer.",
        recent_turns=[],
        claims_pool_snapshot=[],
        persona_name="Sam",
        recent_reply_starts=["Got it.", "Right —"],
    )
    assert s.recent_reply_starts == ["Got it.", "Right —"]
