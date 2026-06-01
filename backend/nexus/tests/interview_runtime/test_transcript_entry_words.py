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
