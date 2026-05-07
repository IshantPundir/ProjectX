import pytest

from app.modules.interview_engine.judge.fallback import (
    FallbackReason, synthesize_fallback,
)
from app.modules.interview_engine.models.judge import (
    AdvancePayload, NextAction, PoliteClosePayload,
)


def test_fallback_with_target_emits_advance():
    out = synthesize_fallback(
        reason=FallbackReason.timeout, next_pending_mandatory_id="q2",
    )
    assert out.next_action == NextAction.advance
    assert isinstance(out.next_action_payload, AdvancePayload)
    assert out.next_action_payload.target_question_id == "q2"
    assert out.thought == "judge_fallback_timeout"
    assert out.observations == []
    assert out.candidate_claims == []


def test_fallback_with_no_target_emits_polite_close():
    out = synthesize_fallback(
        reason=FallbackReason.parse_error, next_pending_mandatory_id=None,
    )
    assert out.next_action == NextAction.polite_close
    assert isinstance(out.next_action_payload, PoliteClosePayload)
    assert out.next_action_payload.reason == "judge_fallback_no_advance_target"
    assert out.thought == "judge_fallback_parse_error"


@pytest.mark.parametrize("reason", list(FallbackReason))
def test_thought_encodes_reason(reason):
    out = synthesize_fallback(reason=reason, next_pending_mandatory_id="q1")
    assert out.thought == f"judge_fallback_{reason.value}"
