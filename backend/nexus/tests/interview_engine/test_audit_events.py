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


def test_speaker_output_empty_payload():
    from app.modules.interview_engine.audit_events import SpeakerOutputEmptyPayload
    p = SpeakerOutputEmptyPayload(
        turn_id="abc",
        instruction_kind="redirect",
        fallback_text="Let me restate that. Walk me through Jira.",
    )
    assert p.turn_id == "abc"
    assert p.instruction_kind == "redirect"


def test_speaker_opener_played_payload_shape():
    from app.modules.interview_engine.audit_events import (
        SpeakerOpenerPlayedPayload,
    )
    p = SpeakerOpenerPlayedPayload(
        turn_id="t-1",
        instruction_kind="push_back",
        sub_context="vague_answer",
        opener_text="Got it.",
        cache_hit=True,
    )
    assert p.turn_id == "t-1"
    assert p.cache_hit is True


def test_speaker_opener_played_payload_default_is_session_intro_false():
    """Backward compatibility — existing emitters that don't pass
    is_session_intro get False by default."""
    from app.modules.interview_engine.audit_events import SpeakerOpenerPlayedPayload
    p = SpeakerOpenerPlayedPayload(
        turn_id="t-1",
        instruction_kind="push_back",
        sub_context="vague_answer",
        opener_text="Got it.",
        cache_hit=True,
    )
    assert p.is_session_intro is False


def test_speaker_opener_played_payload_accepts_is_session_intro_true():
    from app.modules.interview_engine.audit_events import SpeakerOpenerPlayedPayload
    p = SpeakerOpenerPlayedPayload(
        turn_id="t-0",
        instruction_kind="deliver_first_question",
        sub_context="default",
        opener_text="Hi, I'm Sam. To start —",
        cache_hit=True,
        is_session_intro=True,
    )
    assert p.is_session_intro is True


def test_turn_coalesced_payload_roundtrip():
    """TurnCoalescedPayload serializes and validates correctly."""
    from app.modules.interview_engine.audit_events import TurnCoalescedPayload

    payload = TurnCoalescedPayload(
        prior_turn_id="prior-abc",
        current_turn_id="current-xyz",
        prior_text="First one, like, I would communicate with the client.",
        current_text="They are trying to achieve what their existing workflow is.",
        combined_text=(
            "First one, like, I would communicate with the client. "
            "They are trying to achieve what their existing workflow is."
        ),
        prior_instruction_kind="push_back",
        prior_sub_context="missing_specifics",
        gap_ms=850,
        coalesce_window_ms=5000,
    )
    dumped = payload.model_dump()
    assert dumped["prior_turn_id"] == "prior-abc"
    assert dumped["current_turn_id"] == "current-xyz"
    assert dumped["gap_ms"] == 850
    assert dumped["coalesce_window_ms"] == 5000
    assert dumped["prior_instruction_kind"] == "push_back"
    assert dumped["prior_sub_context"] == "missing_specifics"
    # Roundtrip
    restored = TurnCoalescedPayload.model_validate(dumped)
    assert restored == payload


def test_turn_coalesced_payload_rejects_negative_gap_ms():
    """gap_ms is a duration; negatives are model bugs."""
    from pydantic import ValidationError
    from app.modules.interview_engine.audit_events import TurnCoalescedPayload

    with pytest.raises(ValidationError):
        TurnCoalescedPayload(
            prior_turn_id="a",
            current_turn_id="b",
            prior_text="x",
            current_text="y",
            combined_text="x y",
            prior_instruction_kind="push_back",
            prior_sub_context="default",
            gap_ms=-1,
            coalesce_window_ms=5000,
        )
