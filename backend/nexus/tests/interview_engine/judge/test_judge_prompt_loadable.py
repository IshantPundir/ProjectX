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
        "push_back",
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
    """Judge prompt should document the collapsed `redirect` action — a
    single bucket for everything not signal-bearing (greeting / off-topic
    / abusive / injection / hint-fishing). The legacy split-action names
    must be gone."""
    body = prompt_loader.get("engine/judge.system")
    # The collapsed redirect action must be documented.
    assert "redirect" in body.lower()
    # The legacy section / action names must NOT be present.
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


# ---------------------------------------------------------------------------
# Phase 9.2 — push_back action + observation quality grading
# ---------------------------------------------------------------------------


def test_judge_prompt_documents_push_back_reason_codes():
    """All four reason_code values must be documented in §3 push_back."""
    body = prompt_loader.get("engine/judge.system")
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
    body = prompt_loader.get("engine/judge.system")
    assert "active_question_push_back_count" in body
    assert "cap" in body.lower() or "2" in body  # cap=2 must be documented


def test_judge_prompt_documents_quality_grading_section():
    """§4.5 quality grading must define all three grades verbatim."""
    body = prompt_loader.get("engine/judge.system")
    assert "QUALITY GRADING" in body or "quality grading" in body.lower()
    for grade in ("thin", "concrete", "strong"):
        assert grade in body, f"quality grade {grade!r} not defined in judge prompt"


def test_judge_prompt_anti_verbosity_bias_rule():
    """§4.5 must contain the anti-verbosity-bias rule — long-but-vague
    answers must not be graded as concrete."""
    body = prompt_loader.get("engine/judge.system").lower()
    assert "length is not quality" in body or "anti-verbosity" in body


def test_judge_prompt_documents_advance_quality_gate():
    """The State Engine downgrades advance->push_back when no observation
    on the active question reaches concrete/strong. The prompt must teach
    this so the Judge emits push_back itself rather than letting the
    State Engine do the downgrade."""
    body = prompt_loader.get("engine/judge.system").lower()
    # State Engine downgrade behavior must be documented somewhere
    assert "downgrade" in body or "downgrades" in body


def test_judge_prompt_priority_order_is_push_back_first():
    """§8 final discipline: priority order should put push_back FIRST
    when answers are thin (this is the load-bearing change for the
    robotic-advancement bug)."""
    body = prompt_loader.get("engine/judge.system")
    # Find the priority block; push_back must appear before advance in
    # the numbered list.
    push_back_idx = body.lower().find("1. push_back")
    advance_idx = body.lower().find("3. advance")
    assert push_back_idx > 0, "priority order must list push_back first"
    assert advance_idx > push_back_idx, "advance must come AFTER push_back in priority"


def test_judge_prompt_forbids_empty_target_on_advance():
    """§8 must explicitly forbid emitting advance with empty target_question_id
    (the turn-21 bug from session 4cf43291)."""
    body = prompt_loader.get("engine/judge.system").lower()
    assert "empty" in body and "target_question_id" in body


def test_judge_prompt_documents_dont_know_count_input_field():
    """Phase 9.4 Fix #2 — Judge must know about the new input field
    so it can escalate to acknowledge_no_experience after first
    'I don't know' on an experience signal."""
    body = prompt_loader.get("engine/judge.system")
    assert "active_question_consecutive_dont_know_count" in body
    # The escalation rule must be documented in §3 acknowledge_no_experience.
    body_lower = body.lower()
    assert "consecutive-i-dont-know escalation" in body_lower or "consecutive i-don't-know escalation" in body_lower or "death-spiral" in body_lower
    # Acknowledge action must be the explicit escape hatch.
    assert "acknowledge_no_experience" in body


def test_judge_prompt_repeat_trigger_lists_repeat_and_again_keywords():
    """Phase 9.4 Fix #3 — repeat trigger MUST list 'repeat' and 'again'
    as explicit keywords. Mis-classifying 'What was the question again?'
    as clarify (instead of repeat) caused session f665498d turn 2 bug."""
    body = prompt_loader.get("engine/judge.system")
    body_lower = body.lower()
    # The trigger section must reference both keywords.
    assert "repeat" in body_lower
    assert '"again"' in body_lower or "'again'" in body_lower or "again." in body_lower
    # Specific phrasings must be enumerated as MUST-trigger examples.
    assert "what was the question again" in body_lower or "what was that again" in body_lower


def test_judge_prompt_has_push_back_examples_d_and_e():
    """§7 examples must include D (push_back vague_answer) and E
    (push_back deflection). E placed last as the most representative
    example per few-shot best practice."""
    body = prompt_loader.get("engine/judge.system")
    assert "EXAMPLE D" in body
    assert "EXAMPLE E" in body
    # E (deflection) is the failure mode from session 4cf43291 turn 18
    e_idx = body.find("EXAMPLE E")
    d_idx = body.find("EXAMPLE D")
    assert e_idx > d_idx, "Example E must come after Example D"
    # Both examples must show "push_back" in next_action
    examples_block = body[d_idx:]
    assert examples_block.count("push_back") >= 2
