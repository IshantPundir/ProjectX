"""SignalLedger — the runtime spine of the structured interview agent.

Tracks per-signal coverage, evidence quotes, and the monotonic
sequence number used to detect gaps in async Redis writes (A.4).

Read by the Orchestrator on every decision; written by the Sufficiency
Checker (Phase D+) after each substantive turn and by the Disclaim
Classifier (Phase H) when a knockout is confirmed. Final state is
serialized into the audit envelope as a single
``orchestrator.ledger.snapshot`` event at session close.

Invariants enforced here:

1. **Append-only evidence.** Quotes are never removed; only added.
2. **Forward-only normal coverage.** ``none → partial → sufficient``.
   Regressions raise ``LedgerInvariantError``.
3. **`failed` is reachable from any non-`failed` state**, but only via
   the disclaim path (``mark_failed``). It is terminal — once a signal
   is failed, further updates raise.
4. **Monotonic sequence number.** Every mutation increments it.

Pure code: no I/O, no LLM, no DB. Persistence (Redis fire-and-forget)
lives in ``orchestrator/persistence.py`` (A.4).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.interview_runtime import SignalMetadata

CoverageStatus = Literal["none", "partial", "sufficient", "failed"]
EvidenceStrength = Literal["weak", "strong"]


class LedgerInvariantError(RuntimeError):
    """Raised when a ledger mutation would violate an invariant.

    Examples: regressing coverage from `sufficient` back to `partial`,
    updating a signal already marked `failed`, updating a signal_value
    not in the ledger.
    """


class EvidenceQuote(BaseModel):
    """A single piece of evidence the candidate produced for a signal.

    Quotes are exact transcript spans (verified by the Sufficiency
    Checker / Disclaim Classifier — Phase D+ / H). Strength is the
    evaluator's classification; the Report Builder weighs final scoring
    using strength + count + context.
    """

    quote: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    source_question_id: str = Field(min_length=1)
    strength: EvidenceStrength
    timestamp: datetime


class SignalState(BaseModel):
    """Per-signal runtime state.

    Keyed in the ledger by ``signal_value`` (the string form, since
    QuestionConfig.signal_values is a list of strings — see
    impl prompt §6.1).
    """

    signal_value: str = Field(min_length=1)
    weight: Literal[1, 2, 3]
    is_knockout: bool
    priority: Literal["required", "preferred"]
    coverage: CoverageStatus = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_quotes: list[EvidenceQuote] = Field(default_factory=list)
    last_updated_turn: str | None = None
    notes: list[str] = Field(default_factory=list)


# Allowed forward transitions in the normal-flow state machine.
# `failed` is intentionally NOT here — it has its own dedicated
# `mark_failed` entry point gated by the Disclaim Classifier path.
_NORMAL_PROGRESSION: dict[CoverageStatus, frozenset[CoverageStatus]] = {
    "none": frozenset({"none", "partial", "sufficient"}),
    "partial": frozenset({"partial", "sufficient"}),
    "sufficient": frozenset({"sufficient"}),
    # "failed" is terminal — handled separately.
    "failed": frozenset(),
}


class SignalLedger(BaseModel):
    """The canonical signal-coverage state for an in-flight session.

    ``signals`` is keyed by signal_value (string). Construction from a
    list of ``SignalMetadata`` is the standard path:
    ``SignalLedger.from_metadata(session.signal_metadata)``.
    """

    signals: dict[str, SignalState] = Field(default_factory=dict)
    sequence_number: int = 0

    @classmethod
    def from_metadata(
        cls, signal_metadata: list[SignalMetadata]
    ) -> SignalLedger:
        """Build an initial ledger from the SessionConfig signal metadata.

        All signals start at ``coverage="none"`` with empty evidence.
        Order is preserved (Python dicts are insertion-ordered) so
        question-selection logic that iterates can rely on it.
        """
        signals: dict[str, SignalState] = {}
        for sm in signal_metadata:
            signals[sm.value] = SignalState(
                signal_value=sm.value,
                weight=sm.weight,
                is_knockout=sm.knockout,
                priority=sm.priority,
            )
        return cls(signals=signals, sequence_number=0)

    # ------------------------------------------------------------------
    # Mutations — every method that changes state increments
    # `sequence_number` exactly once. The sequence number is consumed
    # by `orchestrator/persistence.py` to detect gaps in async Redis
    # writes (A.4).
    # ------------------------------------------------------------------

    def append_evidence(
        self,
        signal_value: str,
        *,
        evidence: EvidenceQuote,
        new_coverage: CoverageStatus | None = None,
        new_confidence: float | None = None,
        note: str | None = None,
    ) -> None:
        """Record evidence for a signal and (optionally) advance coverage.

        Coverage can only progress forward (``none → partial → sufficient``).
        Backward transitions raise ``LedgerInvariantError``. ``failed``
        is unreachable through this entry point — use ``mark_failed``.

        ``last_updated_turn`` is set to ``evidence.turn_id``.
        """
        state = self._require_signal(signal_value)
        if state.coverage == "failed":
            raise LedgerInvariantError(
                f"Cannot append evidence to failed signal '{signal_value}'"
            )

        if new_coverage is not None:
            self._require_forward_transition(state.coverage, new_coverage)
            state.coverage = new_coverage

        state.evidence_quotes.append(evidence)
        state.last_updated_turn = evidence.turn_id
        if new_confidence is not None:
            state.confidence = new_confidence
        if note:
            state.notes.append(note)
        self.sequence_number += 1

    def add_note(self, signal_value: str, note: str, *, turn_id: str) -> None:
        """Attach a note to a signal without changing coverage / evidence.

        Used by the Sufficiency Checker rationale path when no quote
        survives verification but the rationale itself is worth keeping.
        """
        state = self._require_signal(signal_value)
        if state.coverage == "failed":
            raise LedgerInvariantError(
                f"Cannot annotate failed signal '{signal_value}'"
            )
        state.notes.append(note)
        state.last_updated_turn = turn_id
        self.sequence_number += 1

    def mark_failed(
        self,
        signal_value: str,
        *,
        evidence: EvidenceQuote,
    ) -> None:
        """Mark a signal as `failed` via the disclaim path.

        Terminal transition — no further mutations on this signal are
        allowed afterward. The disclaim quote is appended as evidence
        so the Report Builder can audit the trigger.
        """
        state = self._require_signal(signal_value)
        if state.coverage == "failed":
            raise LedgerInvariantError(
                f"Signal '{signal_value}' is already failed (terminal)"
            )
        state.coverage = "failed"
        state.confidence = 1.0
        state.evidence_quotes.append(evidence)
        state.last_updated_turn = evidence.turn_id
        self.sequence_number += 1

    # ------------------------------------------------------------------
    # Read helpers — pure, no mutation, no sequence-number bump.
    # ------------------------------------------------------------------

    def get(self, signal_value: str) -> SignalState | None:
        return self.signals.get(signal_value)

    def coverage_of(self, signal_value: str) -> CoverageStatus | None:
        state = self.signals.get(signal_value)
        return state.coverage if state else None

    def signals_by_coverage(
        self, coverage: CoverageStatus
    ) -> list[SignalState]:
        return [s for s in self.signals.values() if s.coverage == coverage]

    def all_mandatory_sufficient(self) -> bool:
        """True iff every required signal has reached `sufficient`.

        `failed` does NOT count as sufficient — a knockout-disclaimed
        required signal is still uncovered (and the session would have
        early-exited via the knockout path before reaching this query
        in normal flow).
        """
        for s in self.signals.values():
            if s.priority == "required" and s.coverage != "sufficient":
                return False
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_signal(self, signal_value: str) -> SignalState:
        try:
            return self.signals[signal_value]
        except KeyError:
            raise LedgerInvariantError(
                f"Unknown signal_value '{signal_value}' (not in ledger)"
            ) from None

    @staticmethod
    def _require_forward_transition(
        current: CoverageStatus, target: CoverageStatus
    ) -> None:
        allowed = _NORMAL_PROGRESSION[current]
        if target not in allowed:
            raise LedgerInvariantError(
                f"Illegal coverage transition: {current!r} → {target!r} "
                f"(allowed: {sorted(allowed)})"
            )
