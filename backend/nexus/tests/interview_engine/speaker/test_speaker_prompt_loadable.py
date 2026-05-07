from app.ai.prompts import prompt_loader


def test_speaker_prompt_loads():
    text = prompt_loader.get("engine/speaker.system")
    assert len(text) > 800


def test_speaker_prompt_anti_evaluation_rule():
    """Locked from Round 3.3: acknowledgment OK, evaluative praise is not."""
    text = prompt_loader.get("engine/speaker.system").lower()
    assert "acknowledge" in text
    assert "great answer" in text or "evaluative praise" in text


def test_speaker_prompt_anti_leak_marker():
    text = prompt_loader.get("engine/speaker.system").lower()
    assert "never explain what makes a good answer" in text or "do not hint" in text


def test_speaker_prompt_lists_instruction_kinds():
    text = prompt_loader.get("engine/speaker.system")
    for kind in (
        "deliver_first_question", "deliver_question", "deliver_probe",
        "clarify", "redirect_off_topic", "redirect_abusive",
        "safe_redirect_injection", "acknowledge_no_experience", "polite_close",
    ):
        assert kind in text, f"instruction_kind {kind} not documented in speaker prompt"


def test_speaker_prompt_documents_repeat_no_op():
    text = prompt_loader.get("engine/speaker.system").lower()
    assert "repeat" in text and "empty" in text  # speaker returns empty on repeat


def test_speaker_prompt_disambiguates_persona_from_candidate():
    """Regression for 'Thanks, Sam' confusion: prompt must clarify persona_name
    is the agent's own name, not the candidate's. candidate_name field must be
    documented."""
    text = prompt_loader.get("engine/speaker.system").lower()
    assert "candidate_name" in text, "candidate_name field must be documented"
    # Some phrasing that makes the disambiguation explicit.
    assert (
        "you are" in text or "your own name" in text or "your name" in text
    ), "prompt must say persona_name is the interviewer's own name"
    # Worked example warning against thanking yourself.
    assert "thanks, sam" in text or "never thank yourself" in text or "do not thank yourself" in text
