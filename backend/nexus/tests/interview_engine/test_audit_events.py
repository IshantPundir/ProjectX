import pytest
from pydantic import ValidationError

from app.modules.interview_engine.audit_events import (
    JudgeCallPayload, JudgeSyntheticPayload, JudgeFallbackPayload,
    JudgeValidationPayload, StateMutationPayload,
    SpeakerCallPayload, SpeakerCachedPayload, SpeakerErrorPayload,
    SpeakerOutputPayload, TurnStartedPayload, TurnCompletedPayload,
    LifecycleTransitionPayload, CheckpointWrittenPayload,
    FrontendAttributePayload,
)


def test_judge_fallback_reason_enum():
    p = JudgeFallbackPayload(
        turn_id="t-1", reason="timeout",
        original_failure_context={"exc": "TimeoutError"},
        synthesized_output={"thought": "fallback"},
    )
    assert p.reason == "timeout"
    with pytest.raises(ValidationError):
        JudgeFallbackPayload(
            turn_id="t-1", reason="banana",
            original_failure_context={}, synthesized_output={},
        )


def test_judge_validation_levels():
    JudgeValidationPayload(turn_id="t-1", level="warning", code="x", details={})
    JudgeValidationPayload(turn_id="t-1", level="error", code="x", details={})
    with pytest.raises(ValidationError):
        JudgeValidationPayload(turn_id="t-1", level="info", code="x", details={})


def test_speaker_cached_carries_source_turn_id():
    p = SpeakerCachedPayload(
        turn_id="t-3", instruction_kind="repeat",
        source_turn_id="t-1", final_utterance="hello",
    )
    assert p.source_turn_id == "t-1"


def test_state_mutation_kinds():
    valid = [
        "ledger.append", "queue.advance", "queue.probe", "queue.complete",
        "claims.add", "claims.drop_oldest",
        "lifecycle.transition", "knockout.recorded",
    ]
    for k in valid:
        StateMutationPayload(turn_id="t-1", seq=1, kind=k, before=None, after={"x": 1})
    with pytest.raises(ValidationError):
        StateMutationPayload(turn_id="t-1", seq=1, kind="random.kind", before=None, after={})
