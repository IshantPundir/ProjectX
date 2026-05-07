import pytest

from app.modules.interview_engine.models.ledger import (
    CoverageState, SignalLedgerSnapshot,
)
from app.modules.interview_engine.models.judge import (
    Observation, CoverageTransition,
)
from app.modules.interview_engine.state.ledger import SignalLedger, IllegalCoverageTransition


def test_initial_signal_state_is_none_for_known_signals():
    ledger = SignalLedger(signal_values=["S1", "S2"])
    snap = ledger.snapshot()
    assert snap.snapshots["S1"].coverage == CoverageState.none
    assert snap.snapshots["S2"].coverage == CoverageState.none
    assert snap.next_seq == 1
    assert snap.entries == []


def test_apply_observation_advances_coverage():
    ledger = SignalLedger(signal_values=["S1"])
    obs = Observation(
        signal_value="S1", anchor_id=0,
        evidence_quote="example",
        coverage_transition=CoverageTransition.none_to_partial,
    )
    ledger.apply_observation(obs, turn_id="t-1", recorded_at_ms=1000)
    snap = ledger.snapshot()
    assert snap.snapshots["S1"].coverage == CoverageState.partial
    assert snap.snapshots["S1"].anchors_hit == [0]
    assert len(snap.entries) == 1
    assert snap.entries[0].seq == 1
    assert snap.next_seq == 2


def test_apply_observation_rejects_illegal_backward():
    ledger = SignalLedger(signal_values=["S1"])
    ledger.apply_observation(
        Observation(signal_value="S1", anchor_id=0, evidence_quote="x",
                    coverage_transition=CoverageTransition.none_to_sufficient),
        turn_id="t-1", recorded_at_ms=1000,
    )
    bad = Observation(
        signal_value="S1", anchor_id=1, evidence_quote="y",
        coverage_transition=CoverageTransition.partial_to_partial,
    )
    with pytest.raises(IllegalCoverageTransition):
        ledger.apply_observation(bad, turn_id="t-2", recorded_at_ms=2000)


def test_apply_observation_unknown_signal_raises():
    ledger = SignalLedger(signal_values=["S1"])
    bad = Observation(
        signal_value="UNKNOWN", anchor_id=0, evidence_quote="z",
        coverage_transition=CoverageTransition.none_to_partial,
    )
    with pytest.raises(IllegalCoverageTransition):
        ledger.apply_observation(bad, turn_id="t-1", recorded_at_ms=1000)


def test_failed_to_failed_idempotent_writes_entry_no_state_change():
    ledger = SignalLedger(signal_values=["S1"])
    first = Observation(
        signal_value="S1", anchor_id=-1, evidence_quote="never used",
        coverage_transition=CoverageTransition.none_to_failed,
    )
    second = Observation(
        signal_value="S1", anchor_id=-1, evidence_quote="still never used",
        coverage_transition=CoverageTransition.failed_to_failed,
    )
    ledger.apply_observation(first, turn_id="t-1", recorded_at_ms=1000)
    ledger.apply_observation(second, turn_id="t-2", recorded_at_ms=2000)
    snap = ledger.snapshot()
    assert snap.snapshots["S1"].coverage == CoverageState.failed
    # Two entries written for audit fidelity, but coverage stays failed.
    assert len(snap.entries) == 2


def test_seq_monotonically_increases():
    ledger = SignalLedger(signal_values=["S1"])
    for i in range(3):
        ledger.apply_observation(
            Observation(signal_value="S1", anchor_id=i, evidence_quote=f"e{i}",
                        coverage_transition=CoverageTransition.none_to_partial if i == 0
                        else CoverageTransition.partial_to_partial),
            turn_id=f"t-{i}", recorded_at_ms=1000 + i,
        )
    snap = ledger.snapshot()
    seqs = [e.seq for e in snap.entries]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1


def test_anchors_hit_dedup():
    ledger = SignalLedger(signal_values=["S1"])
    ledger.apply_observation(
        Observation(signal_value="S1", anchor_id=0, evidence_quote="e",
                    coverage_transition=CoverageTransition.none_to_partial),
        turn_id="t-1", recorded_at_ms=1000,
    )
    ledger.apply_observation(
        Observation(signal_value="S1", anchor_id=0, evidence_quote="e2",
                    coverage_transition=CoverageTransition.partial_to_partial),
        turn_id="t-2", recorded_at_ms=2000,
    )
    snap = ledger.snapshot()
    # anchor 0 hit twice but stored once.
    assert snap.snapshots["S1"].anchors_hit == [0]
