import pytest

from app.modules.interview_engine.bank_resolver import resolve_bank_text
from app.modules.interview_engine.models.judge import (
    AcknowledgeNoExperiencePayload, AdvancePayload, ClarifyPayload,
    EndSessionPayload, JudgeOutput, NextAction, PoliteClosePayload,
    ProbePayload, RedirectPayload, RepeatPayload, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind


def _q(qid="q1", text="Tell me about your work.", follow_ups=None):
    """Build a QuestionConfig matching the actual schema (positive_evidence/red_flags/rubric required)."""
    from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric
    return QuestionConfig(
        id=qid, position=0, text=text, signal_values=["S1"], estimated_minutes=2.0,
        is_mandatory=True, follow_ups=follow_ups or [],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="e", meets_bar="m", below_bar="b"),
        evaluation_hint="hint hint hint",
        question_kind="technical_depth",
    )


def _judge(action, payload):
    return JudgeOutput(
        thought="t", observations=[], candidate_claims=[],
        next_action=action, next_action_payload=payload,
        turn_metadata=TurnMetadata(),
    )


def test_advance_resolves_bank_text_to_active_question():
    j = _judge(NextAction.advance, AdvancePayload(target_question_id="q1"))
    r = resolve_bank_text(j, active_question=_q(text="Walk me through q1 please."), active_probe_index=None)
    assert r.instruction_kind == InstructionKind.deliver_question
    assert r.bank_text == "Walk me through q1 please."


def test_probe_resolves_to_followup_at_index():
    j = _judge(NextAction.probe, ProbePayload(probe_id="1", probe_rationale="r"))
    r = resolve_bank_text(
        j, active_question=_q(follow_ups=["fu0", "fu1", "fu2"]), active_probe_index=1,
    )
    assert r.instruction_kind == InstructionKind.deliver_probe
    assert r.bank_text == "fu1"


def test_acknowledge_no_experience_carries_failed_signal():
    j = _judge(
        NextAction.acknowledge_no_experience,
        AcknowledgeNoExperiencePayload(failed_signal_value="JQL"),
    )
    r = resolve_bank_text(j, active_question=_q(), active_probe_index=None)
    assert r.instruction_kind == InstructionKind.acknowledge_no_experience
    assert r.failed_signal_value == "JQL"
    assert r.bank_text is None


def test_redirect_with_no_active_question_has_no_bank_text():
    """Collapsed redirect action: bank_text mirrors active_question.text
    (so the Speaker can restate the current question), or None when there
    is no active question."""
    j = _judge(NextAction.redirect, RedirectPayload())
    r = resolve_bank_text(j, active_question=None, active_probe_index=None)
    assert r.instruction_kind == InstructionKind.redirect
    assert r.bank_text is None


def test_redirect_with_active_question_carries_question_text():
    """Collapsed redirect action: when an active question is present, the
    Speaker receives its text so it can restate the current question with
    a tone selected from turn_metadata."""
    j = _judge(NextAction.redirect, RedirectPayload())
    r = resolve_bank_text(
        j,
        active_question=_q(text="Walk me through your Jira workflow design."),
        active_probe_index=None,
    )
    assert r.instruction_kind == InstructionKind.redirect
    assert r.bank_text == "Walk me through your Jira workflow design."


def test_polite_close_no_bank_text():
    j = _judge(NextAction.polite_close, PoliteClosePayload(reason="x"))
    r = resolve_bank_text(j, active_question=None, active_probe_index=None)
    assert r.instruction_kind == InstructionKind.polite_close
    assert r.bank_text is None


def test_clarify_uses_active_question_text():
    j = _judge(NextAction.clarify, ClarifyPayload())
    r = resolve_bank_text(j, active_question=_q(text="What do you mean?"), active_probe_index=None)
    assert r.instruction_kind == InstructionKind.clarify
    assert r.bank_text == "What do you mean?"


def test_repeat_returns_no_bank_text():
    """Repeat is handled at orchestrator level via cached utterance."""
    j = _judge(NextAction.repeat, RepeatPayload())
    r = resolve_bank_text(j, active_question=_q(), active_probe_index=None)
    assert r.instruction_kind == InstructionKind.repeat
    assert r.bank_text is None
