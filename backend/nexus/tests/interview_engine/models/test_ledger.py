import pytest
from pydantic import ValidationError

from app.modules.interview_engine.models.ledger import (
    CoverageState,
    LedgerEntry,
    SignalSnapshot,
    SignalLedgerSnapshot,
)


def test_coverage_state_values():
    assert CoverageState.none == "none"
    assert CoverageState.partial == "partial"
    assert CoverageState.sufficient == "sufficient"
    assert CoverageState.failed == "failed"
    # No "strong" — answer-quality grading lives in the Report Builder.
    assert "strong" not in [s.value for s in CoverageState]


def test_ledger_entry_required_fields():
    entry = LedgerEntry(
        seq=1,
        turn_id="11111111-1111-1111-1111-111111111111",
        signal_value="ScriptRunner expertise",
        anchor_id=0,
        evidence_quote="I built a custom validator using ScriptRunner.",
        coverage_before=CoverageState.none,
        coverage_after=CoverageState.partial,
        recorded_at_ms=1500,
    )
    assert entry.seq == 1
    assert entry.coverage_after == CoverageState.partial


def test_ledger_entry_failure_uses_negative_anchor():
    """Failure entries (no-experience disclosure) use anchor_id = -1 sentinel."""
    entry = LedgerEntry(
        seq=2,
        turn_id="22222222-2222-2222-2222-222222222222",
        signal_value="JQL fluency",
        anchor_id=-1,
        evidence_quote="I've never used JQL.",
        coverage_before=CoverageState.none,
        coverage_after=CoverageState.failed,
        recorded_at_ms=3200,
    )
    assert entry.anchor_id == -1
    assert entry.coverage_after == CoverageState.failed


def test_signal_snapshot_default_anchors_empty():
    snap = SignalSnapshot(signal_value="X", coverage=CoverageState.none)
    assert snap.anchors_hit == []
    assert snap.last_observation_seq is None


def test_signal_ledger_snapshot_keyed_by_signal_value():
    snap = SignalLedgerSnapshot(
        entries=[],
        snapshots={"X": SignalSnapshot(signal_value="X", coverage=CoverageState.partial)},
        next_seq=1,
    )
    assert snap.snapshots["X"].coverage == CoverageState.partial
