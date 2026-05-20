import re

from app.ai.prompts import PromptLoader

_loader = PromptLoader(version="v2")


def test_judge_prompt_loads():
    text = _loader.get("engine/judge.system")
    assert len(text) > 1000, "judge prompt should be substantial"


def test_judge_prompt_pins_output_language():
    text = _loader.get("engine/judge.system").lower()
    assert "english" in text, "prompt must pin output language"


def test_judge_prompt_anti_leak_marker():
    text = _loader.get("engine/judge.system").lower()
    assert "never reveal rubric" in text or "do not reveal rubric" in text


def test_judge_prompt_lists_all_next_actions():
    # v2 uses UPPERCASE action names in the decision tree; check case-insensitively.
    text = _loader.get("engine/judge.system").lower()
    for action in (
        "advance", "probe", "clarify", "repeat",
        "redirect",
        "acknowledge_no_experience", "polite_close", "end_session",
        "push_back",
    ):
        assert action in text, f"action {action} not documented in judge prompt"


def test_judge_prompt_documents_failed_coverage_state():
    text = _loader.get("engine/judge.system").lower()
    assert "failed" in text and "no experience" in text


def test_judge_prompt_documents_signal_metadata_field():
    text = _loader.get("engine/judge.system").lower()
    assert "active_question_signal_metadata" in text or "signal_metadata" in text
    assert "knockout" in text  # already present in the prompt, but lock it in


def test_judge_prompt_has_redirect_section():
    """Judge prompt should document the collapsed `redirect` action — a
    single bucket for everything not signal-bearing (greeting / off-topic
    / abusive / injection / hint-fishing). The legacy split-action names
    must be gone."""
    body = _loader.get("engine/judge.system")
    # The collapsed redirect action must be documented.
    assert "redirect" in body.lower()
    # The legacy section / action names must NOT be present.
    assert "redirect_off_topic" not in body
    assert "redirect_abusive" not in body
    assert "safe_redirect_injection" not in body


def test_judge_prompt_emphasizes_anchor_minus_one_for_failed():
    body = _loader.get("engine/judge.system")
    # The hardened rule must appear verbatim somewhere.
    assert "anchor_id == -1" in body or "anchor_id = -1" in body or "sentinel" in body.lower()


def test_judge_prompt_warns_against_failed_with_positive_anchor():
    body = _loader.get("engine/judge.system")
    # The negative example block must exist.
    assert "DO NOT" in body or "WRONG" in body


# ---------------------------------------------------------------------------
# Phase 9.2 — push_back action + observation quality grading
# ---------------------------------------------------------------------------


def test_judge_prompt_documents_push_back_reason_codes():
    """All four reason_code values must be documented in the push_back section."""
    body = _loader.get("engine/judge.system")
    for code in (
        "vague_answer",
        "deflection",
        "missing_specifics",
        "unanswered_subquestion",
    ):
        assert code in body, f"reason_code {code!r} not documented in judge prompt"


def test_judge_prompt_documents_push_back_count_input_field():
    """The Judge must know about the active_question_push_back_count input
    field and the cap=2 rule that depends on it."""
    body = _loader.get("engine/judge.system")
    assert "active_question_push_back_count" in body
    assert "cap" in body.lower() or "2" in body  # cap=2 must be documented


def test_judge_prompt_documents_quality_grading_section():
    """Quality grading must define all three grades verbatim."""
    body = _loader.get("engine/judge.system")
    assert "QUALITY GRADING" in body or "quality grading" in body.lower()
    for grade in ("thin", "concrete", "strong"):
        assert grade in body, f"quality grade {grade!r} not defined in judge prompt"


def test_judge_prompt_anti_verbosity_bias_rule():
    """The anti-verbosity-bias rule — long-but-vague answers must not be
    graded as concrete."""
    body = _loader.get("engine/judge.system").lower()
    assert "length is not quality" in body or "anti-verbosity" in body


def test_judge_prompt_documents_advance_quality_gate():
    """The State Engine downgrades advance->push_back when no observation
    on the active question reaches concrete/strong. The prompt must teach
    this so the Judge emits push_back itself rather than letting the
    State Engine do the downgrade."""
    body = _loader.get("engine/judge.system").lower()
    # State Engine downgrade behavior must be documented somewhere
    assert "downgrade" in body or "downgrades" in body


def test_judge_prompt_forbids_empty_target_on_advance():
    """The prompt must explicitly forbid emitting advance with empty
    target_question_id (the turn-21 bug from session 4cf43291)."""
    body = _loader.get("engine/judge.system").lower()
    assert "empty" in body and "target_question_id" in body


def test_judge_prompt_documents_still_confused_count_input_field():
    """A8 — Judge must know about the active_question_still_confused_count
    input field and the new contract: Judge sets candidate_still_confused,
    State Engine owns the 2-attempt cap and escalation (not the Judge).
    The old 'death-spiral' / CONSECUTIVE-DON'T-KNOW escalation language
    is deleted in A8 because it conflicted with the validator rule that
    candidate_still_confused is only valid with next_action=clarify."""
    body = _loader.get("engine/judge.system")
    assert "active_question_still_confused_count" in body
    # The new flag rule must be documented — Judge sets the flag, engine counts.
    body_lower = body.lower()
    assert "candidate_still_confused" in body_lower
    assert "state engine" in body_lower  # engine owns the escalation
    # Acknowledge action must still be documented (explicit no-experience trigger).
    assert "acknowledge_no_experience" in body
    # The old Judge-owned escalation rules must be GONE.
    assert "death-spiral" not in body_lower
    assert "consecutive-don't-know escalation" not in body_lower
    assert "still_confused_count >= 1" not in body


def test_judge_prompt_repeat_trigger_lists_repeat_and_again_keywords():
    """Phase 9.4 Fix #3 — repeat trigger MUST reference both 'repeat' and
    'again' as explicit keywords. Mis-classifying 'What was the question
    again?' as clarify (instead of repeat) caused session f665498d turn 2
    bug."""
    body = _loader.get("engine/judge.system")
    body_lower = body.lower()
    # Both keywords must appear in the prompt.
    assert "repeat" in body_lower
    # v2 uses "Say it again." as an explicit example
    assert '"again"' in body_lower or "'again'" in body_lower or "again." in body_lower


def test_judge_prompt_documents_push_back_examples():
    """Examples section must include at least one push_back scenario and
    at least one redirect scenario."""
    body = _loader.get("engine/judge.system")
    # v2 has EXAMPLE D (push_back vague_answer) and EXAMPLE E (redirect greeting)
    assert "EXAMPLE D" in body
    assert "EXAMPLE E" in body
    # At least one push_back example must be present in the examples block
    examples_start = body.find("§8")
    assert examples_start > 0, "§8 WORKED EXAMPLES section must exist"
    examples_block = body[examples_start:]
    assert "push_back" in examples_block, "examples must include a push_back scenario"
    assert "redirect" in examples_block, "examples must include a redirect scenario"
