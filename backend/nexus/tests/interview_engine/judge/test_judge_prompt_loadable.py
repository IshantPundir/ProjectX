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
