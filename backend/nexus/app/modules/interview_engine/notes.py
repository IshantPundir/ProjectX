"""Gen-3 NoteLog — append-only evidence-note accumulator.

NoteLog is the engine's live session-scoped ledger of `EvidenceNote` entries
(from `interview_runtime.evidence`). It is written to by the controller each
time the Brain emits a `BrainTurnOutput` with signal observations, and read
by `record_session_evidence` at session end to persist the final
`SessionEvidence` blob.

Key invariants:
- Append-only: notes are never mutated or deleted, only appended.
- Monotonic sequence: each note carries a `seq` that strictly increments.
- Provenance: each note carries the `turn_ref`, `question_id`, and source
  speaker (always `agent` for directive notes, `candidate` for observation notes).
- No livekit dependency: NoteLog is a pure Python data structure — it must
  never import any livekit module so it can be unit-tested without the engine.

Built in Phase C (C1 — append, to_session_evidence) as part of the drive-loop wiring.
"""

from __future__ import annotations


class NoteLog:
    """Append-only ledger of `EvidenceNote` entries for a single session.

    Constructed once by `run()` at session start and passed into `_drive()`.
    The Brain emits signal observations (as `BrainTurnOutput.observations`) and
    the controller converts them into `EvidenceNote` entries via `append()`.

    At session end, `to_session_evidence()` assembles the full `SessionEvidence`
    object that is persisted by `record_session_evidence`.

    All method bodies raise `NotImplementedError` until Phase C1.
    """

    def append(
        self,
        *,
        turn_ref: str,
        question_id: str | None,
        note: object,
    ) -> None:
        """Append one `EvidenceNote` to the log.

        Args:
            turn_ref: The engine turn reference (e.g. ``"t-3"``) at which this
                note was recorded.
            question_id: The bank question this note is associated with, or
                ``None`` for session-level notes (meta, knockout, close).
            note: An `EvidenceNote` instance (from `interview_runtime.evidence`).
                Typed as ``object`` here to avoid a runtime import of livekit-free
                evidence types at module scope — the controller passes the real
                ``EvidenceNote`` at call time.

        Raises:
            NotImplementedError: Until Phase C1.
        """
        raise NotImplementedError("NoteLog.append — implemented in Phase C (C1)")

    def to_session_evidence(self, *, meta: object) -> object:
        """Assemble a `SessionEvidence` from all accumulated notes.

        Args:
            meta: A `SessionMeta` instance (from `interview_runtime.evidence`)
                carrying session-level identifiers and timing.

        Returns:
            A `SessionEvidence` instance ready to be passed to
            `record_session_evidence`.

        Raises:
            NotImplementedError: Until Phase C1.
        """
        raise NotImplementedError("NoteLog.to_session_evidence — implemented in Phase C (C1)")
