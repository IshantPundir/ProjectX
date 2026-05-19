"""Verifies the intro_brief InstructionKind and new SpeakerInput fields."""
from app.modules.interview_engine.models.speaker import (
    InstructionKind,
    SpeakerInput,
)


def test_intro_brief_kind_exists():
    assert InstructionKind.intro_brief == "intro_brief"


def test_speaker_input_accepts_intro_fields():
    si = SpeakerInput(
        instruction_kind=InstructionKind.intro_brief,
        persona_name="Arjun",
        candidate_name="Punar",
        job_title="Sr. Integration Engineer",
        hiring_company_name="Workato",
        role_summary="Lead end-to-end delivery of enterprise integrations.",
        session_duration_minutes=15,
        question_count=5,
    )
    assert si.instruction_kind == InstructionKind.intro_brief
    assert si.hiring_company_name == "Workato"
    assert si.session_duration_minutes == 15
    assert si.question_count == 5


def test_intro_fields_default_none_for_other_kinds():
    si = SpeakerInput(
        instruction_kind=InstructionKind.deliver_question,
        persona_name="Arjun",
        bank_text="A scenario question",
    )
    assert si.job_title is None
    assert si.hiring_company_name is None
    assert si.role_summary is None
    assert si.session_duration_minutes is None
    assert si.question_count is None


def test_speaker_input_intro_kind_accepts_minimal_construction():
    """A SpeakerInput(instruction_kind=intro_brief, ...) constructs cleanly.

    Built directly by orchestrator._build_intro_speaker_input, NOT via
    speaker/input_builder.build_speaker_input. This test confirms the
    Pydantic model accepts the intro_brief kind with all required
    persona/candidate fields and the optional intro context fields.
    """
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    si = SpeakerInput(
        instruction_kind=InstructionKind.intro_brief,
        persona_name="Arjun",
        candidate_name="Punar",
        job_title="Sr. Integration Engineer",
        hiring_company_name="Workato",
        role_summary="A summary describing the role and core stack.",
        session_duration_minutes=15,
        question_count=5,
    )
    assert si.instruction_kind == InstructionKind.intro_brief
    assert si.bank_text is None


def test_speaker_input_accepts_is_post_phase_transition():
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    si = SpeakerInput(
        instruction_kind=InstructionKind.deliver_question,
        persona_name="Arjun",
        bank_text="Walk me through how you'd design X for real-time use cases.",
        is_post_phase_transition=True,
    )
    assert si.is_post_phase_transition is True


def test_is_post_phase_transition_defaults_false():
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    si = SpeakerInput(
        instruction_kind=InstructionKind.deliver_question,
        persona_name="Arjun",
        bank_text="A scenario question to evaluate technical depth at length.",
    )
    assert si.is_post_phase_transition is False
