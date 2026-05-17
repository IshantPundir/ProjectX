from app.modules.interview_engine.models.ledger import (
    CoverageState, SignalLedgerSnapshot, SignalSnapshot,
)
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.state.lifecycle import (
    LifecycleSnapshot, LifecycleState,
)
from app.modules.interview_engine.state.checkpoint import EngineCheckpoint
from app.modules.interview_runtime.models import TranscriptEntry


def test_checkpoint_round_trip_via_dict():
    cp = EngineCheckpoint(
        schema_version=2,
        session_id="s-1",
        ledger=SignalLedgerSnapshot(
            entries=[],
            snapshots={"S1": SignalSnapshot(signal_value="S1", coverage=CoverageState.partial)},
            next_seq=1,
        ),
        queue=QuestionQueueSnapshot(questions=[], active_index=None),
        claims=ClaimsPoolSnapshot(entries=[]),
        lifecycle=LifecycleSnapshot(
            state=LifecycleState.active,
            time_budget_total_seconds=600.0,
            time_elapsed_seconds=10.0,
        ),
        last_audit_seq_flushed=42,
        captured_at_ms=12345,
        turn_count=3,
        transcript=[
            TranscriptEntry(role="candidate", text="hi", timestamp_ms=0),
            TranscriptEntry(role="agent", text="hello", timestamp_ms=1000, question_id="q1"),
        ],
        question_utterances={"t-1": "asking Q1"},
    )
    payload = cp.model_dump(mode="json")
    rebuilt = EngineCheckpoint.model_validate(payload)
    assert rebuilt == cp


def test_checkpoint_schema_version_default_is_v2():
    cp = EngineCheckpoint(
        session_id="s-1",
        ledger=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue=QuestionQueueSnapshot(),
        claims=ClaimsPoolSnapshot(),
        lifecycle=LifecycleSnapshot(
            state=LifecycleState.pre_start,
            time_budget_total_seconds=0.0,
        ),
        last_audit_seq_flushed=0,
        captured_at_ms=0,
    )
    assert cp.schema_version == 2


def test_checkpoint_v1_payload_backward_loads_with_safe_defaults():
    """A v1 checkpoint persisted before this change must still parse.

    v1 lacked turn_count / transcript / question_utterances. The new
    fields must accept that absence and default to 0/[]/{} so a
    crash-recovery load of a pre-upgrade checkpoint succeeds.
    """
    v1_payload = {
        "schema_version": 1,
        "session_id": "s-1",
        "ledger": {"entries": [], "snapshots": {}, "next_seq": 1},
        "queue": {"questions": [], "active_index": None},
        "claims": {"entries": []},
        "lifecycle": {
            "state": "pre_start",
            "time_budget_total_seconds": 0.0,
            "time_elapsed_seconds": 0.0,
        },
        "last_audit_seq_flushed": 0,
        "captured_at_ms": 0,
    }
    cp = EngineCheckpoint.model_validate(v1_payload)
    assert cp.schema_version == 1
    assert cp.turn_count == 0
    assert cp.transcript == []
    assert cp.question_utterances == {}
