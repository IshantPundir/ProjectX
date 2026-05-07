from app.modules.interview_engine.models.speaker import InstructionKind, SpeakerInput
from app.modules.interview_engine.models.claims import ClaimEntry
from app.modules.interview_runtime.schemas import TranscriptEntry


def test_instruction_kind_values():
    expected = {
        "deliver_first_question",
        "deliver_question",
        "deliver_probe",
        "clarify",
        "repeat",
        "redirect_off_topic",
        "redirect_abusive",
        "safe_redirect_injection",
        "acknowledge_no_experience",
        "polite_close",
    }
    assert {k.value for k in InstructionKind} == expected


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
