"""Gen-3 NoteLog — append-only evidence-note accumulator.

NoteLog is the engine's live session-scoped ledger of `EvidenceNote` entries
(from `interview_runtime.evidence`). The drive-loop calls `NoteLog.append()`
once per signal observation the Brain emits each turn; at session end
`to_session_evidence()` packages the durable `SessionEvidence` contract.

KEY INVARIANTS
--------------
- APPEND-ONLY: notes are never mutated or deleted, only appended. A retraction
  is a new note (stance=contradicts, retracts_seq=<prior seq>) that links the
  note it walks back — both are kept forever.
- MONOTONIC SEQ: each note carries a `seq` that strictly increments from 1.
- quote = full utterance (always): the caller passes the complete candidate
  utterance as the proof. Precise sub-windows are carried by `span` and are
  re-derivable from the transcript's word-level timing.
- span = obs.quote_span if provided, else utterance_span: the observation's
  `quote_span` field narrows the relevance window when the brain identifies a
  precise sub-span; when absent the whole utterance span is used.
- NO LIVEKIT DEPENDENCY: NoteLog is a pure Python data structure — it must
  never import any livekit module so it can be unit-tested without the engine.

PROVENANCE (deferred to Phase C2): to_session_evidence() accepts the
already-provenance-stamped `signals` list from the caller. NoteLog does not
compute provenance — that lives in the session-close pass (C2).
"""

from __future__ import annotations

from app.modules.interview_runtime.evidence import (
    EvidenceNote,
    KnockoutOutcome,
    QuestionRecord,
    SessionEvidence,
    SessionMeta,
    SignalEvidence,
    TimeSpan,
    TranscriptTurn,
)

from app.modules.interview_engine.contracts import SignalObservation


class NoteLog:
    """Append-only ledger of `EvidenceNote` entries for a single session.

    Constructed once by the drive-loop at session start. The Brain emits signal
    observations (as `BrainTurnOutput.observations`) and the controller converts
    them into `EvidenceNote` entries via `append()`.

    At session end, `to_session_evidence()` assembles the full `SessionEvidence`
    object that is persisted by `record_session_evidence`. The caller passes the
    already-provenance-stamped `signals` list — provenance computation lives in
    the session-close pass (Phase C2), not here.
    """

    def __init__(self) -> None:
        self._notes: list[EvidenceNote] = []

    # ------------------------------------------------------------------
    # Core append
    # ------------------------------------------------------------------

    def append(
        self,
        obs: SignalObservation,
        *,
        turn_ref: str,
        utterance: str,
        utterance_span: TimeSpan,
        from_question_id: str,
        via_probe: bool,
    ) -> EvidenceNote:
        """Append one immutable `EvidenceNote` from a Brain signal observation.

        Args:
            obs: The `SignalObservation` emitted by the Brain for this turn.
            turn_ref: Engine turn reference (e.g. ``"t-3"``) at which this note
                was recorded.
            utterance: The full candidate utterance text — always stored as the
                proof. Precise sub-windows are carried by ``span`` and are
                re-derivable from the transcript's word-level timing.
            utterance_span: The `TimeSpan` of the full candidate utterance. Used
                as the note's span when ``obs.quote_span`` is None.
            from_question_id: The bank question on the floor when this was said.
            via_probe: True if elicited by a follow-up probe, False for the main
                question.

        Returns:
            The freshly created, frozen `EvidenceNote` (also appended internally).
        """
        seq = len(self._notes) + 1

        # Retraction: link to the most-recent prior note for the same signal.
        # If obs.retracts is True but no prior same-signal note exists, set None
        # (defensive — the Brain should not emit retracts=True on a fresh signal,
        # but the log must not crash).
        retracts_seq: int | None = None
        if obs.retracts:
            for prior in reversed(self._notes):
                if prior.signal == obs.signal:
                    retracts_seq = prior.seq
                    break

        # quote = full utterance (reliable proof; sub-window in span).
        quote = utterance

        # span = precise sub-window from observation, or the full utterance span.
        span = obs.quote_span if obs.quote_span is not None else utterance_span

        note = EvidenceNote(
            seq=seq,
            turn_ref=turn_ref,
            signal=obs.signal,
            stance=obs.stance,
            texture=obs.texture,
            quote=quote,
            span=span,
            from_question_id=from_question_id,
            via_probe=via_probe,
            retracts_seq=retracts_seq,
        )
        self._notes.append(note)
        return note

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def notes(self) -> list[EvidenceNote]:
        """A snapshot copy of accumulated notes in chronological (seq) order.

        Returns a new list so external mutation cannot corrupt the internal log.
        """
        return list(self._notes)

    def __len__(self) -> int:
        return len(self._notes)

    # ------------------------------------------------------------------
    # Session-end packaging
    # ------------------------------------------------------------------

    def to_session_evidence(
        self,
        *,
        meta: SessionMeta,
        signals: list[SignalEvidence],
        questions: list[QuestionRecord],
        transcript: list[TranscriptTurn],
        knockout: KnockoutOutcome | None = None,
    ) -> SessionEvidence:
        """Assemble a `SessionEvidence` from all accumulated notes.

        The caller passes the already-provenance-stamped `signals` list.
        Provenance computation lives in the session-close pass (Phase C2), not
        here. This method purely packages — it does not derive, score, or judge.

        Args:
            meta: A `SessionMeta` instance carrying session-level identifiers
                and timing.
            signals: Per-signal identity + engine-derived provenance (stamped
                by the caller's session-close pass).
            questions: What the screen did with each bank question.
            transcript: Word-timed turn list (the raw timing record).
            knockout: Recorded only when a mandatory signal was verified absent.
                None when no knockout occurred.

        Returns:
            A validated `SessionEvidence` ready to be passed to
            `record_session_evidence`.
        """
        return SessionEvidence(
            meta=meta,
            signals=signals,
            notes=list(self._notes),
            questions=questions,
            transcript=transcript,
            knockout=knockout,
        )
