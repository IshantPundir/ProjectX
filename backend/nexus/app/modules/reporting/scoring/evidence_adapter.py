"""Adapter over the gen-3 engine's SessionEvidence contract (pure — no IO/LLM).

Turns the append-only evidence into the views the deterministic scorer needs.
The graded denominator is the PRIMARY-signal set derived from question records —
NOT SessionEvidence.signals[] (which is the full role set; see spec §3.1).
"""
from __future__ import annotations

from app.modules.interview_runtime.evidence import (
    EvidenceNote,
    EvidenceStance,
    Provenance,
    SessionEvidence,
    SignalEvidence,
    Speaker,
)


class EvidenceView:
    """Read-only projections of a SessionEvidence for the scorer."""

    def __init__(self, evidence: SessionEvidence) -> None:
        self._ev = evidence

    @property
    def evidence(self) -> SessionEvidence:
        return self._ev

    @property
    def primary_set(self) -> set[str]:
        """The graded denominator: every signal that is a question's primary_signal."""
        return {q.primary_signal for q in self._ev.questions}

    @property
    def signal_by_name(self) -> dict[str, SignalEvidence]:
        return {s.signal: s for s in self._ev.signals}

    @property
    def provenance_by_signal(self) -> dict[str, Provenance]:
        return {s.signal: s.provenance for s in self._ev.signals}

    @property
    def notes_by_signal(self) -> dict[str, list[EvidenceNote]]:
        out: dict[str, list[EvidenceNote]] = {}
        for n in self._ev.notes:
            out.setdefault(n.signal, []).append(n)
        return out

    @property
    def notes_by_question(self) -> dict[str, list[EvidenceNote]]:
        """Notes grouped by the question that elicited them (from_question_id)."""
        out: dict[str, list[EvidenceNote]] = {}
        for n in self._ev.notes:
            out.setdefault(n.from_question_id, []).append(n)
        return out

    @property
    def outcome_by_question(self) -> dict[str, str]:
        """question_id → outcome ('asked' | 'not_reached')."""
        return {
            q.question_id: (q.outcome.value if hasattr(q.outcome, "value") else q.outcome)
            for q in self._ev.questions
        }

    @property
    def demonstrated_secondaries(self) -> set[str]:
        """Non-primary signals that were cross-credited (upside-only path)."""
        primary = self.primary_set
        return {
            s.signal for s in self._ev.signals
            if s.signal not in primary and s.provenance == Provenance.cross_credited
        }

    @property
    def candidate_transcript_text(self) -> str:
        return "\n".join(
            t.text for t in self._ev.transcript if t.speaker == Speaker.candidate
        )

    def has_supporting_notes(self, signal: str) -> bool:
        return any(
            n.stance == EvidenceStance.supports
            for n in self.notes_by_signal.get(signal, [])
        )
