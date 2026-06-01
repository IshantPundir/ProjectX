from __future__ import annotations

from app.modules.interview_runtime import question_asked_at_ms, relative_words, turn_bounds
from app.modules.interview_runtime.models import WordTiming


# --- question_asked_at_ms (pre-existing; restored after an accidental overwrite) ---

def test_picks_earliest_agent_timestamp_per_question():
    transcript = [
        {"role": "agent", "text": "Q1?", "timestamp_ms": 1000, "question_id": "q1"},
        {"role": "candidate", "text": "...", "timestamp_ms": 1500, "question_id": "q1"},
        {"role": "agent", "text": "probe q1", "timestamp_ms": 2000, "question_id": "q1"},
        # earlier timestamp appearing LATER in the list must still win (out-of-order guard):
        {"role": "agent", "text": "resend q1", "timestamp_ms": 500, "question_id": "q1"},
        {"role": "agent", "text": "Q2?", "timestamp_ms": 3000, "question_id": "q2"},
    ]
    assert question_asked_at_ms(transcript) == {"q1": 500, "q2": 3000}


def test_ignores_candidate_and_untagged_lines():
    transcript = [
        {"role": "agent", "text": "filler", "timestamp_ms": 100, "question_id": None},
        {"role": "candidate", "text": "hi", "timestamp_ms": 200, "question_id": "q1"},
        {"role": "agent", "text": "Q1?", "timestamp_ms": 300, "question_id": "q1"},
    ]
    assert question_asked_at_ms(transcript) == {"q1": 300}


def test_empty_transcript_returns_empty():
    assert question_asked_at_ms([]) == {}


# --- relative_words / turn_bounds (Phase 1 candidate-reel helpers) ---

def test_relative_words_anchors_to_first_word():
    # (text, start_seconds, end_seconds, confidence) on the STT stream clock.
    raw = [
        ("six", 12.40, 12.72, 0.99),
        ("years", 12.80, 13.30, 0.97),
    ]
    out = relative_words(raw)
    assert out == [
        WordTiming(text="six", start_ms=0, end_ms=320, confidence=0.99),
        WordTiming(text="years", start_ms=400, end_ms=900, confidence=0.97),
    ]


def test_relative_words_empty():
    assert relative_words([]) == []


def test_relative_words_clamps_negative_drift_to_zero():
    # A later fragment whose stream time precedes the first word (clock jitter)
    # must never produce a negative offset.
    raw = [("a", 5.00, 5.10, 0.9), ("b", 4.98, 5.20, 0.9)]
    out = relative_words(raw)
    assert out[0].start_ms == 0
    assert out[1].start_ms == 0  # clamped, not -20


def test_turn_bounds_anchors_end_to_commit_and_back_off_duration():
    words = relative_words([("six", 12.40, 12.72, 0.99), ("years", 12.80, 13.30, 0.97)])
    start_ms, end_ms = turn_bounds(anchor_ms=42000, words=words)
    assert end_ms == 42000
    assert start_ms == 42000 - 900


def test_turn_bounds_no_words_returns_anchor_for_both():
    assert turn_bounds(anchor_ms=42000, words=[]) == (42000, 42000)


def test_turn_bounds_never_negative():
    words = relative_words([("x", 0.0, 50.0, 0.9)])  # 50s "word" (pathological)
    start_ms, end_ms = turn_bounds(anchor_ms=1000, words=words)
    assert start_ms == 0
    assert end_ms == 1000
