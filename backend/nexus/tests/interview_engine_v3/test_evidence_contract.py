from app.modules.interview_runtime.evidence import (
    SessionEvidence, SignalEvidence, EvidenceNote, QuestionRecord, TranscriptTurn,
    SessionMeta, TimeSpan, Provenance, EvidenceStance, EvidenceTexture,
    QuestionOutcome, ThreadClosure, CoverageState, CompletionReason, SignalType, SignalPriority, Speaker,
)


def test_timespan_rejects_reversed():
    import pytest
    with pytest.raises(Exception):
        TimeSpan(start_ms=5, end_ms=1)


def test_append_only_retraction_keeps_original():
    notes = [
        EvidenceNote(seq=1, turn_ref="t-2", signal="SOAP/XML", stance=EvidenceStance.supports,
                     texture=EvidenceTexture.thin, quote="used SOAP a bit",
                     span=TimeSpan(start_ms=0, end_ms=900), from_question_id="q", via_probe=False),
        EvidenceNote(seq=2, turn_ref="t-3", signal="SOAP/XML", stance=EvidenceStance.contradicts,
                     texture=EvidenceTexture.thin, quote="actually no",
                     span=TimeSpan(start_ms=1000, end_ms=2000), from_question_id="q", via_probe=True,
                     retracts_seq=1),
    ]
    assert [n.seq for n in notes] == [1, 2]
    assert notes[1].retracts_seq == 1
