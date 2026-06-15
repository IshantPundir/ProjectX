from __future__ import annotations

from app.modules.interview_runtime import (
    asked_at_ms_by_question_evidence,
    relative_words,
)
from app.modules.interview_runtime.models import WordTiming


# --- asked_at_ms_by_question_evidence (gen-3: over session_evidence_json["transcript"]) ---
#
# Gen-3 turn dict shape (SessionEvidence.transcript dumped to JSON):
#   {"speaker": "agent"|"candidate", "question_id": str|None,
#    "span": {"start_ms": int, "end_ms": int}, "words": [...]}

def test_picks_earliest_agent_span_start_per_question():
    transcript = [
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 1000, "end_ms": 1500}},
        {"speaker": "candidate", "question_id": "q1", "span": {"start_ms": 1600, "end_ms": 2000}},
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 2000, "end_ms": 2400}},
        # earlier start appearing LATER in the list must still win (out-of-order guard):
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 500, "end_ms": 900}},
        {"speaker": "agent", "question_id": "q2", "span": {"start_ms": 3000, "end_ms": 3400}},
    ]
    assert asked_at_ms_by_question_evidence(transcript) == {"q1": 500, "q2": 3000}


def test_ignores_candidate_and_untagged_agent_turns():
    transcript = [
        {"speaker": "agent", "question_id": None, "span": {"start_ms": 100, "end_ms": 150}},
        {"speaker": "candidate", "question_id": "q1", "span": {"start_ms": 200, "end_ms": 300}},
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 300, "end_ms": 700}},
    ]
    assert asked_at_ms_by_question_evidence(transcript) == {"q1": 300}


def test_skips_turns_missing_span_or_start_ms():
    transcript = [
        {"speaker": "agent", "question_id": "q1"},  # no span
        {"speaker": "agent", "question_id": "q1", "span": {}},  # span, no start_ms
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 800, "end_ms": 900}},
    ]
    assert asked_at_ms_by_question_evidence(transcript) == {"q1": 800}


def test_empty_transcript_returns_empty():
    assert asked_at_ms_by_question_evidence([]) == {}


# --- relative_words (Phase 1 candidate-reel helper) ---

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
