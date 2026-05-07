from app.modules.interview_engine.models.ledger import (
    CoverageState, SignalLedgerSnapshot, SignalSnapshot,
)
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.state.lifecycle import (
    LifecycleSnapshot, LifecycleState,
)
from app.modules.interview_engine.state.checkpoint import EngineCheckpoint


def test_checkpoint_round_trip_via_dict():
    cp = EngineCheckpoint(
        schema_version=1,
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
    )
    payload = cp.model_dump(mode="json")
    rebuilt = EngineCheckpoint.model_validate(payload)
    assert rebuilt == cp


def test_checkpoint_schema_version_default():
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
    assert cp.schema_version == 1
