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


def test_speaker_input_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import SpeakerInputPayload

    payload = SpeakerInputPayload(
        turn_id="t-1",
        speaker_input={
            "instruction_kind": "deliver_question",
            "bank_text": "tell me about a time you scaled a service",
            "persona_name": "Punar",
        },
    )
    dumped = payload.model_dump()
    assert dumped["turn_id"] == "t-1"
    assert dumped["speaker_input"]["instruction_kind"] == "deliver_question"


def test_state_snapshot_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import StateSnapshotPayload

    payload = StateSnapshotPayload(
        turn_id="t-1",
        ledger={"snapshots": {}, "entries": [], "next_seq": 1},
        queue={"questions": [], "active_index": None},
        claims={"entries": []},
        lifecycle={"state": "active", "knockout_failures": [],
                   "time_budget_total_seconds": 1800.0,
                   "time_elapsed_seconds": 0.0, "last_outcome": None},
    )
    assert payload.model_dump()["lifecycle"]["state"] == "active"


# -- 2026-05-17 continuation payloads -------------------------------------------------

def test_turn_stitched_continuation_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import (
        TurnStitchedContinuationPayload,
    )

    p = TurnStitchedContinuationPayload(
        turn_id="t-new",
        prior_chars=120,
        current_chars=80,
        combined_chars=201,
        gap_ms=3000,
    )
    d = p.model_dump()
    assert d["turn_id"] == "t-new"
    assert d["prior_chars"] == 120
    assert d["combined_chars"] == 201
    assert TurnStitchedContinuationPayload.model_validate(d) == p


def test_turn_aborted_for_continuation_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import (
        TurnAbortedForContinuationPayload,
    )

    p = TurnAbortedForContinuationPayload(
        turn_id="t-abort",
        phase="judge",
        elapsed_ms=500,
        text_chars=120,
        consecutive_aborts=1,
    )
    d = p.model_dump()
    assert d["phase"] == "judge"
    assert d["consecutive_aborts"] == 1
    assert TurnAbortedForContinuationPayload.model_validate(d) == p


def test_turn_aborted_for_continuation_payload_rejects_invalid_phase() -> None:
    from pydantic import ValidationError

    from app.modules.interview_engine.audit_events import (
        TurnAbortedForContinuationPayload,
    )

    with pytest.raises(ValidationError):
        TurnAbortedForContinuationPayload(
            turn_id="t-abort",
            phase="post_speaker",  # invalid — only judge / pre_speaker / speaker_pre_commit allowed
            elapsed_ms=500,
            text_chars=120,
            consecutive_aborts=1,
        )


def test_turn_loop_guard_fired_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import (
        TurnLoopGuardFiredPayload,
    )

    p = TurnLoopGuardFiredPayload(turn_id="t-x", consecutive_aborts=3)
    assert TurnLoopGuardFiredPayload.model_validate(p.model_dump()) == p


def test_state_snapshot_taken_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import StateSnapshotTakenPayload

    p = StateSnapshotTakenPayload(
        turn_id="t-1", transcript_entries=4, queue_active_index=1,
    )
    d = p.model_dump()
    assert d["queue_active_index"] == 1
    assert StateSnapshotTakenPayload.model_validate(d) == p

    # active_index can be None at session start.
    p2 = StateSnapshotTakenPayload(
        turn_id="t-syn", transcript_entries=0, queue_active_index=None,
    )
    assert p2.queue_active_index is None


def test_state_snapshot_restored_and_committed_payloads_round_trip() -> None:
    from app.modules.interview_engine.audit_events import (
        StateSnapshotCommittedPayload,
        StateSnapshotRestoredPayload,
    )

    r = StateSnapshotRestoredPayload(turn_id="t-1")
    c = StateSnapshotCommittedPayload(turn_id="t-1")
    assert StateSnapshotRestoredPayload.model_validate(r.model_dump()) == r
    assert StateSnapshotCommittedPayload.model_validate(c.model_dump()) == c
