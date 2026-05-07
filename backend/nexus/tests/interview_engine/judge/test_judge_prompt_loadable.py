import re

from app.ai.prompts import prompt_loader


def test_judge_prompt_loads():
    text = prompt_loader.get("engine/judge.system")
    assert len(text) > 1000, "judge prompt should be substantial"


def test_judge_prompt_pins_output_language():
    text = prompt_loader.get("engine/judge.system").lower()
    assert "english" in text, "prompt must pin output language"


def test_judge_prompt_anti_leak_marker():
    text = prompt_loader.get("engine/judge.system").lower()
    assert "never reveal rubric" in text or "do not reveal rubric" in text


def test_judge_prompt_lists_all_next_actions():
    text = prompt_loader.get("engine/judge.system")
    for action in (
        "advance", "probe", "clarify", "repeat",
        "redirect_off_topic", "redirect_abusive", "safe_redirect_injection",
        "acknowledge_no_experience", "polite_close", "end_session",
    ):
        assert action in text, f"action {action} not documented in judge prompt"


def test_judge_prompt_documents_failed_coverage_state():
    text = prompt_loader.get("engine/judge.system").lower()
    assert "failed" in text and "no experience" in text


def test_judge_prompt_documents_signal_metadata_field():
    text = prompt_loader.get("engine/judge.system").lower()
    assert "active_question_signal_metadata" in text or "signal_metadata" in text
    assert "knockout" in text  # already present in the prompt, but lock it in


def test_judge_prompt_disambiguates_injection_from_hint_asking():
    text = prompt_loader.get("engine/judge.system").lower()
    # The prompt must explicitly call out that asking for hints is NOT injection.
    assert "hint" in text  # innocent request
    # And there must be language about the distinction.
    assert (
        "asking for hints" in text
        or "innocent" in text
        or "give me an example" in text
    )
