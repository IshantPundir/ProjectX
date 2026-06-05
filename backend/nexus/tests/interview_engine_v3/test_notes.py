"""Tests for NoteLog — the append-only evidence-note accumulator (Phase C1).

Design under test (notes.py):
- append(obs, *, turn_ref, utterance, utterance_span, from_question_id, via_probe) → EvidenceNote
  - seq is monotonically assigned from len(_notes)+1
  - note.quote == utterance (full utterance; precise sub-window in span)
  - note.span == obs.span if obs.span is not None else utterance_span
  - note.retracts_seq == seq of most-recent prior note with same signal (if obs.retracts is True),
    else None (and None if obs.retracts is True but no prior same-signal note exists)
- notes property returns a copy of the accumulated list
- to_session_evidence(meta, signals, questions, transcript, knockout) → SessionEvidence
  - packages accumulated notes with the supplied objects; round-trips via model_dump/model_validate
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.interview_engine.notes import NoteLog
from app.modules.interview_engine.contracts import SignalObservation, CoverageState
from app.modules.interview_runtime.evidence import (
    EvidenceNote,
    EvidenceStance,
    EvidenceTexture,
    TimeSpan,
    SessionEvidence,
    SessionMeta,
    SignalEvidence,
    QuestionRecord,
    TranscriptTurn,
    KnockoutOutcome,
    SignalType,
    SignalPriority,
    Provenance,
    QuestionTier,
    QuestionOutcome,
    Speaker,
    CompletionReason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(
    signal: str = "test_signal",
    stance: EvidenceStance = EvidenceStance.supports,
    texture: EvidenceTexture = EvidenceTexture.concrete,
    coverage_after: CoverageState = CoverageState.partial,
    span: TimeSpan | None = None,
    quote: str | None = None,
    retracts: bool = False,
) -> SignalObservation:
    return SignalObservation(
        signal=signal,
        stance=stance,
        texture=texture,
        coverage_after=coverage_after,
        span=span,
        quote=quote,
        retracts=retracts,
    )


def _utterance_span() -> TimeSpan:
    return TimeSpan(start_ms=0, end_ms=5000)


def _session_meta() -> SessionMeta:
    now = datetime.now(tz=timezone.utc)
    return SessionMeta(
        session_id="sess-1",
        job_id="job-1",
        candidate_id="cand-1",
        stage_id="stage-1",
        started_at=now,
        ended_at=now,
        duration_s=300.0,
        time_budget_s=300.0,
        completion=CompletionReason.completed,
        questions_asked=3,
        questions_core_total=3,
        questions_overflow_asked=0,
    )


def _signal_evidence(signal: str = "test_signal") -> SignalEvidence:
    return SignalEvidence(
        signal=signal,
        signal_type=SignalType.competency,
        weight=2,
        priority=SignalPriority.preferred,
        knockout=False,
        provenance=Provenance.asked_directly,
    )


def _question_record(question_id: str = "q-1") -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id,
        primary_signal="test_signal",
        tier=QuestionTier.core,
        outcome=QuestionOutcome.asked,
        probes_available=2,
    )


def _transcript_turn(turn_ref: str = "t-1") -> TranscriptTurn:
    return TranscriptTurn(
        turn_ref=turn_ref,
        speaker=Speaker.candidate,
        text="I have worked with Python for five years.",
        span=TimeSpan(start_ms=0, end_ms=4000),
        pre_turn_gap_ms=200,
    )


# ---------------------------------------------------------------------------
# Test 1: append assigns monotonic seq + fills the note
# ---------------------------------------------------------------------------

def test_append_assigns_seq_and_fills_note():
    """append with span=None: seq==1, quote==utterance, span==utterance_span."""
    log = NoteLog()
    utterance = "I worked with Python for five years, built APIs and ML pipelines."
    uspan = _utterance_span()
    obs = _obs(signal="python_experience", span=None)

    note = log.append(
        obs,
        turn_ref="t-1",
        utterance=utterance,
        utterance_span=uspan,
        from_question_id="q-1",
        via_probe=False,
    )

    assert isinstance(note, EvidenceNote)
    assert note.seq == 1
    assert note.signal == "python_experience"
    assert note.stance == EvidenceStance.supports
    assert note.texture == EvidenceTexture.concrete
    assert note.quote == utterance          # full utterance stored as the proof
    assert note.span == uspan              # fallback to utterance_span when obs.span is None
    assert note.from_question_id == "q-1"
    assert note.via_probe is False
    assert note.retracts_seq is None


# ---------------------------------------------------------------------------
# Test 2: obs.span sets the note span (but quote is still the full utterance)
# ---------------------------------------------------------------------------

def test_span_sets_note_span_quote_stays_full_utterance():
    """When obs.span is set, note.span == obs.span; note.quote still == full utterance."""
    log = NoteLog()
    utterance = "Well, mostly I used REST, but I dabbled in SOAP for a legacy integration."
    uspan = TimeSpan(start_ms=0, end_ms=6000)
    precise = TimeSpan(start_ms=3100, end_ms=5900)  # the SOAP sub-window
    obs = _obs(signal="SOAP_experience", span=precise)

    note = log.append(
        obs,
        turn_ref="t-2",
        utterance=utterance,
        utterance_span=uspan,
        from_question_id="q-2",
        via_probe=True,
    )

    assert note.span == precise            # precise sub-window from obs.span
    assert note.quote == utterance         # full utterance is the proof
    assert note.seq == 1
    assert note.via_probe is True


# ---------------------------------------------------------------------------
# Test 3: multi-signal turn → multiple notes sharing turn_ref
# ---------------------------------------------------------------------------

def test_multi_signal_turn_shares_turn_ref():
    """Two observations in one turn produce seq 1 and 2, both with the same turn_ref."""
    log = NoteLog()
    uspan = _utterance_span()
    utterance = "I have Python experience and also know Docker."

    obs_a = _obs(signal="python_experience")
    obs_b = _obs(signal="docker_experience", texture=EvidenceTexture.thin)

    note_a = log.append(obs_a, turn_ref="t-3", utterance=utterance, utterance_span=uspan,
                         from_question_id="q-1", via_probe=False)
    note_b = log.append(obs_b, turn_ref="t-3", utterance=utterance, utterance_span=uspan,
                         from_question_id="q-1", via_probe=False)

    assert note_a.seq == 1
    assert note_b.seq == 2
    assert note_a.turn_ref == "t-3"
    assert note_b.turn_ref == "t-3"
    assert len(log.notes) == 2


# ---------------------------------------------------------------------------
# Test 4: retraction keeps original + links via retracts_seq
# ---------------------------------------------------------------------------

def test_retraction_keeps_original_and_links():
    """Append supports note for X (seq 1), then retraction for X (seq 2).
    Both notes must remain in the log. retracts_seq on note 2 == 1."""
    log = NoteLog()
    uspan = _utterance_span()

    # First note: candidate claims they know SOAP
    obs_support = _obs(signal="SOAP_experience", stance=EvidenceStance.supports, retracts=False)
    note_1 = log.append(obs_support, turn_ref="t-2", utterance="Used SOAP before.",
                         utterance_span=uspan, from_question_id="q-1", via_probe=False)

    # Second note: candidate walks it back
    obs_retract = _obs(signal="SOAP_experience", stance=EvidenceStance.contradicts, retracts=True)
    note_2 = log.append(obs_retract, turn_ref="t-4", utterance="Actually, only briefly.",
                         utterance_span=uspan, from_question_id="q-1", via_probe=True)

    assert note_1.seq == 1
    assert note_2.seq == 2
    assert note_2.retracts_seq == 1      # links the original
    assert len(log.notes) == 2           # original kept — append-only
    # Original is unchanged
    assert log.notes[0].stance == EvidenceStance.supports
    assert log.notes[0].retracts_seq is None


# ---------------------------------------------------------------------------
# Test 5: retracts=True with no prior same-signal note → retracts_seq is None
# ---------------------------------------------------------------------------

def test_retraction_with_no_prior_note_gives_none():
    """A retracts=True observation for a signal with no prior note yields retracts_seq=None."""
    log = NoteLog()
    uspan = _utterance_span()

    # First append a note for a DIFFERENT signal (so "SOAP_experience" has no prior)
    obs_other = _obs(signal="python_experience")
    log.append(obs_other, turn_ref="t-1", utterance="Python yes.", utterance_span=uspan,
               from_question_id="q-1", via_probe=False)

    # Now retract a signal that has never appeared before
    obs_retract = _obs(signal="SOAP_experience", stance=EvidenceStance.contradicts, retracts=True)
    note = log.append(obs_retract, turn_ref="t-2", utterance="SOAP? Never used it.",
                      utterance_span=uspan, from_question_id="q-1", via_probe=False)

    assert note.retracts_seq is None     # defensive: no prior same-signal note to link


# ---------------------------------------------------------------------------
# Test 6: to_session_evidence packages everything + round-trips
# ---------------------------------------------------------------------------

def test_to_session_evidence_packages_and_round_trips():
    """After appending notes, to_session_evidence returns a valid SessionEvidence
    carrying the exact notes (in seq order) and all supplied metadata."""
    log = NoteLog()
    uspan = _utterance_span()

    obs_1 = _obs(signal="python_experience")
    obs_2 = _obs(signal="sql_experience", texture=EvidenceTexture.strong)
    log.append(obs_1, turn_ref="t-1", utterance="Python: five years.", utterance_span=uspan,
               from_question_id="q-1", via_probe=False)
    log.append(obs_2, turn_ref="t-2", utterance="SQL: heavy indexing work.", utterance_span=uspan,
               from_question_id="q-2", via_probe=True)

    meta = _session_meta()
    signals = [_signal_evidence("python_experience"), _signal_evidence("sql_experience")]
    questions = [_question_record("q-1"), _question_record("q-2")]
    transcript = [_transcript_turn("t-1"), _transcript_turn("t-2")]

    evidence = log.to_session_evidence(
        meta=meta,
        signals=signals,
        questions=questions,
        transcript=transcript,
        knockout=None,
    )

    assert isinstance(evidence, SessionEvidence)
    # Notes are passed through in seq order
    assert len(evidence.notes) == 2
    assert evidence.notes[0].seq == 1
    assert evidence.notes[1].seq == 2
    assert evidence.notes[0].signal == "python_experience"
    assert evidence.notes[1].signal == "sql_experience"
    # Metadata carried through
    assert evidence.meta == meta
    assert evidence.signals == signals
    assert evidence.questions == questions
    assert evidence.transcript == transcript
    assert evidence.knockout is None

    # Round-trip: must survive model_dump → model_validate
    dumped = evidence.model_dump()
    restored = SessionEvidence.model_validate(dumped)
    assert restored.notes[0].seq == 1
    assert restored.notes[1].seq == 2


# ---------------------------------------------------------------------------
# Test 7: notes property returns a copy (external mutation does not affect log)
# ---------------------------------------------------------------------------

def test_notes_property_returns_copy():
    """Mutating the returned notes list does not corrupt the internal log."""
    log = NoteLog()
    uspan = _utterance_span()
    obs = _obs()
    log.append(obs, turn_ref="t-1", utterance="Test.", utterance_span=uspan,
               from_question_id="q-1", via_probe=False)

    notes_copy = log.notes
    notes_copy.clear()  # mutate the returned copy

    assert len(log.notes) == 1  # internal log is unaffected


# ---------------------------------------------------------------------------
# Test 8: retraction links the MOST RECENT prior note, not the oldest
# ---------------------------------------------------------------------------

def test_retraction_links_most_recent_prior_note():
    """If there are two prior notes for the same signal, retracts_seq points at the most recent."""
    log = NoteLog()
    uspan = _utterance_span()

    obs_1 = _obs(signal="X", stance=EvidenceStance.supports)
    obs_2 = _obs(signal="X", stance=EvidenceStance.supports, texture=EvidenceTexture.strong)
    obs_retract = _obs(signal="X", stance=EvidenceStance.contradicts, retracts=True)

    log.append(obs_1, turn_ref="t-1", utterance="X yes.", utterance_span=uspan,
               from_question_id="q-1", via_probe=False)
    log.append(obs_2, turn_ref="t-2", utterance="X definitely.", utterance_span=uspan,
               from_question_id="q-1", via_probe=True)
    note_ret = log.append(obs_retract, turn_ref="t-3", utterance="Actually no.",
                           utterance_span=uspan, from_question_id="q-1", via_probe=False)

    # Should link seq=2 (most recent), not seq=1
    assert note_ret.retracts_seq == 2
