"""Append-only SignalLedger — evidence event log + per-signal coverage snapshots."""
from __future__ import annotations

from app.modules.interview_engine.models.judge import (
    CoverageTransition, Observation,
)
from app.modules.interview_engine.models.ledger import (
    CoverageState, LedgerEntry, SignalLedgerSnapshot, SignalSnapshot,
)


class IllegalCoverageTransition(Exception):
    """Raised when an Observation cannot be legally applied to the current ledger state."""


# Map every legal transition to (before, after) state pair.
_TRANSITION_TABLE: dict[CoverageTransition, tuple[CoverageState, CoverageState]] = {
    CoverageTransition.none_to_partial:
        (CoverageState.none, CoverageState.partial),
    CoverageTransition.partial_to_partial:
        (CoverageState.partial, CoverageState.partial),
    CoverageTransition.partial_to_sufficient:
        (CoverageState.partial, CoverageState.sufficient),
    CoverageTransition.none_to_sufficient:
        (CoverageState.none, CoverageState.sufficient),
    CoverageTransition.none_to_failed:
        (CoverageState.none, CoverageState.failed),
    CoverageTransition.partial_to_failed:
        (CoverageState.partial, CoverageState.failed),
    CoverageTransition.sufficient_to_failed:
        (CoverageState.sufficient, CoverageState.failed),
    CoverageTransition.failed_to_failed:
        (CoverageState.failed, CoverageState.failed),
}


class SignalLedger:
    """Append-only event log + denormalized per-signal coverage snapshots.

    Constructed with the list of known signal_values from SessionConfig.
    Apply observations one by one; illegal transitions raise IllegalCoverageTransition.
    """

    def __init__(self, *, signal_values: list[str]) -> None:
        self._snapshots: dict[str, SignalSnapshot] = {
            v: SignalSnapshot(signal_value=v, coverage=CoverageState.none)
            for v in signal_values
        }
        self._entries: list[LedgerEntry] = []
        self._next_seq: int = 1

    def apply_observation(
        self, observation: Observation, *, turn_id: str, recorded_at_ms: int,
    ) -> LedgerEntry:
        """Validate the observation against current state, then append + update snapshot."""
        if observation.signal_value not in self._snapshots:
            raise IllegalCoverageTransition(
                f"Unknown signal_value: {observation.signal_value!r}"
            )

        expected_before, expected_after = _TRANSITION_TABLE[observation.coverage_transition]
        current_state = self._snapshots[observation.signal_value].coverage
        if current_state != expected_before:
            raise IllegalCoverageTransition(
                f"Transition {observation.coverage_transition.value} requires "
                f"current state {expected_before.value}, but signal "
                f"{observation.signal_value!r} is {current_state.value}"
            )

        entry = LedgerEntry(
            seq=self._next_seq,
            turn_id=turn_id,
            signal_value=observation.signal_value,
            anchor_id=observation.anchor_id,
            evidence_quote=observation.evidence_quote,
            coverage_before=expected_before,
            coverage_after=expected_after,
            recorded_at_ms=recorded_at_ms,
        )
        self._entries.append(entry)
        self._next_seq += 1

        snap = self._snapshots[observation.signal_value]
        snap.coverage = expected_after
        if observation.anchor_id >= 0 and observation.anchor_id not in snap.anchors_hit:
            snap.anchors_hit.append(observation.anchor_id)
        snap.last_observation_seq = entry.seq
        return entry

    def snapshot(self) -> SignalLedgerSnapshot:
        """Return a deep-copied snapshot of the ledger state."""
        return SignalLedgerSnapshot(
            entries=[e.model_copy() for e in self._entries],
            snapshots={k: v.model_copy() for k, v in self._snapshots.items()},
            next_seq=self._next_seq,
        )

    @classmethod
    def from_snapshot(cls, snap: SignalLedgerSnapshot, *, signal_values: list[str]) -> "SignalLedger":
        """Reconstruct a ledger from a serialized snapshot (for crash recovery)."""
        ledger = cls(signal_values=signal_values)
        ledger._entries = [e.model_copy() for e in snap.entries]
        ledger._snapshots = {k: v.model_copy() for k, v in snap.snapshots.items()}
        ledger._next_seq = snap.next_seq
        return ledger
