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
    assert out.observations == []
    assert out.candidate_claims == []


def test_fallback_with_no_target_emits_polite_close():
    out = synthesize_fallback(
        reason=FallbackReason.parse_error, next_pending_mandatory_id=None,
    )
    assert out.next_action == NextAction.polite_close
    assert isinstance(out.next_action_payload, PoliteClosePayload)


@pytest.mark.parametrize("reason", list(FallbackReason))
def test_synthesized_output_carries_no_audit_fields(reason):
    """thought, probe_rationale, polite_close.reason were removed (audit-only,
    never read by the State Engine). Fallback context lives on the
    JUDGE_FALLBACK audit event payload's original_failure_context, not on
    the synthesized JudgeOutput itself."""
    out = synthesize_fallback(reason=reason, next_pending_mandatory_id="q1")
    assert not hasattr(out, "thought")
    # Synthesized fallback only emits the minimum fields required for the
    # State Engine to make a forward-progress decision.
    assert out.observations == []
    assert out.candidate_claims == []


def test_validation_error_synthesizes_clarify_not_advance() -> None:
    """Regression test for force-advance bug: validation_error must
    synthesize a clarify (no queue mutation), not an advance."""
    from app.modules.interview_engine.judge.fallback import (
        FallbackReason, synthesize_fallback,
    )
    from app.modules.interview_engine.models.judge import (
        NextAction, ClarifyPayload,
    )

    output = synthesize_fallback(
        reason=FallbackReason.validation_error,
        next_pending_mandatory_id="q-2",
    )
    assert output.next_action == NextAction.clarify
    assert isinstance(output.next_action_payload, ClarifyPayload)
    assert output.observations == []
    assert output.candidate_claims == []


def test_timeout_still_synthesizes_advance() -> None:
    """Sanity: non-validation_error reasons keep their existing
    advance-or-polite_close behavior."""
    from app.modules.interview_engine.judge.fallback import (
        FallbackReason, synthesize_fallback,
    )
    from app.modules.interview_engine.models.judge import (
        NextAction, AdvancePayload, PoliteClosePayload,
    )

    out = synthesize_fallback(
        reason=FallbackReason.timeout, next_pending_mandatory_id="q-3",
    )
    assert out.next_action == NextAction.advance
    assert isinstance(out.next_action_payload, AdvancePayload)
    assert out.next_action_payload.target_question_id == "q-3"

    out2 = synthesize_fallback(
        reason=FallbackReason.timeout, next_pending_mandatory_id=None,
    )
    assert out2.next_action == NextAction.polite_close
    assert isinstance(out2.next_action_payload, PoliteClosePayload)
