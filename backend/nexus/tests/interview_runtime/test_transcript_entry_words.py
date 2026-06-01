import pytest
from pydantic import ValidationError

from app.modules.interview_runtime.models import TranscriptEntry, WordTiming


def test_word_timing_round_trips():
    w = WordTiming(text="hello", start_ms=0, end_ms=320, confidence=0.98)
    assert WordTiming.model_validate(w.model_dump()) == w


def test_transcript_entry_accepts_words_and_bounds():
    entry = TranscriptEntry(
        role="candidate",
        text="hello world",
        timestamp_ms=42000,
        question_id="q1",
        start_ms=41000,
        end_ms=42000,
        words=[
            WordTiming(text="hello", start_ms=0, end_ms=320, confidence=0.98),
            WordTiming(text="world", start_ms=360, end_ms=700, confidence=0.95),
        ],
    )
    dumped = entry.model_dump(mode="json")
    assert dumped["words"][1]["text"] == "world"
    assert TranscriptEntry.model_validate(dumped) == entry


def test_transcript_entry_backward_compatible():
    entry = TranscriptEntry(role="agent", text="Hi.", timestamp_ms=3049)
    assert entry.words is None
    assert entry.start_ms is None
    assert entry.end_ms is None


def test_word_timing_rejects_negative_start():
    with pytest.raises(ValidationError):
        WordTiming(text="x", start_ms=-1, end_ms=100, confidence=0.9)


def test_word_timing_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        WordTiming(text="x", start_ms=0, end_ms=100, confidence=1.01)


def test_word_timing_rejects_end_before_start():
    with pytest.raises(ValidationError):
        WordTiming(text="x", start_ms=500, end_ms=100, confidence=0.9)


# --- regression locks: persistence + report-player safety ---

def test_enriched_transcript_survives_jsonb_round_trip():
    # Mimics record_session_result's model_dump(mode="json") -> JSONB -> reload.
    import json

    entry = TranscriptEntry(
        role="candidate", text="six years", timestamp_ms=42000, question_id="q1",
        start_ms=41100, end_ms=42000,
        words=[WordTiming(text="six", start_ms=0, end_ms=320, confidence=0.99)],
    )
    blob = json.loads(json.dumps(entry.model_dump(mode="json")))  # JSONB hop
    assert TranscriptEntry.model_validate(blob) == entry


def test_recording_build_transcript_ignores_word_fields():
    # The report player reads only role/text/timestamp_ms; the new word fields
    # must be silently ignored (no schema break) by the existing builder.
    from app.modules.session.recording import _build_transcript

    raw = [{
        "role": "candidate", "text": "six years", "timestamp_ms": 42000,
        "question_id": "q1", "start_ms": 41100, "end_ms": 42000,
        "words": [{"text": "six", "start_ms": 0, "end_ms": 320, "confidence": 0.99}],
    }]
    segs = _build_transcript(raw)
    assert len(segs) == 1
    assert segs[0].text == "six years"
    assert segs[0].t_ms == 42000
