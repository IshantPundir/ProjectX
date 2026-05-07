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
        "redirect",
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


def test_judge_prompt_has_redirect_section():
    """Judge prompt should have a single REDIRECT section after the
    redirect collapse (no separate redirect_off_topic / redirect_abusive
    / safe_redirect_injection sections)."""
    body = prompt_loader.get("engine/judge.system")
    # The collapsed redirect section is the new entry point.
    assert "REDIRECT" in body
    # The legacy section names should be gone.
    assert "redirect_off_topic" not in body
    assert "redirect_abusive" not in body
    assert "safe_redirect_injection" not in body


def test_judge_prompt_emphasizes_anchor_minus_one_for_failed():
    body = prompt_loader.get("engine/judge.system")
    # The hardened rule must appear verbatim somewhere.
    assert "anchor_id == -1" in body or "anchor_id = -1" in body or "sentinel" in body.lower()


def test_judge_prompt_warns_against_failed_with_positive_anchor():
    body = prompt_loader.get("engine/judge.system")
    # The negative example block must exist.
    assert "DO NOT" in body or "WRONG" in body
