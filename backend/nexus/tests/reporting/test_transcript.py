"""Tests for transcript ↔ envelope segmentation (Task 8)."""
from __future__ import annotations

import json
import pathlib

from app.modules.reporting.scoring.transcript import segment

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_segments_real_session() -> None:
    envelope = json.loads((FIX / "e4072361_envelope.json").read_text())
    transcript = json.loads((FIX / "e4072361_transcript.json").read_text())
    units = segment(transcript=transcript, envelope=envelope)
    assert len(units) >= 5
    prog = next(
        u for u in units if "Java" in u.question_text or "JSON" in u.question_text
    )
    assert prog.candidate_engaged is True
    assert prog.word_count > 0
    assert prog.probes_fired >= 0


def test_handles_missing_envelope_gracefully() -> None:
    transcript = [
        {"role": "agent", "text": "Q1?", "timestamp_ms": 0, "question_id": "q1"},
        {
            "role": "candidate",
            "text": "yes I have five years",
            "timestamp_ms": 1000,
            "question_id": None,
        },
    ]
    units = segment(transcript=transcript, envelope={"events": []})
    assert len(units) == 1 and units[0].question_id == "q1"


def test_deduplicates_repeated_question_id() -> None:
    """A question re-asked (same qid) should produce only one ScoredUnit."""
    transcript = [
        {
            "role": "agent",
            "text": "Tell me about X?",
            "timestamp_ms": 0,
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "text": "I worked on X",
            "timestamp_ms": 1000,
            "question_id": "q1",
        },
        # agent re-asks the same question (e.g. candidate didn't understand first time)
        {
            "role": "agent",
            "text": "Tell me about X again?",
            "timestamp_ms": 2000,
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "text": "OK so X was about",
            "timestamp_ms": 3000,
            "question_id": "q1",
        },
    ]
    units = segment(transcript=transcript, envelope={"events": []})
    assert len(units) == 1
    assert units[0].question_id == "q1"
    # both candidate answers should be concatenated
    assert "I worked on X" in units[0].candidate_answer
    assert "OK so X was about" in units[0].candidate_answer


def test_word_count_sums_all_candidate_turns() -> None:
    """word_count should sum across all candidate turns for the question."""
    transcript = [
        {"role": "agent", "text": "Q?", "timestamp_ms": 0, "question_id": "q1"},
        {
            "role": "candidate",
            "text": "one two three",
            "timestamp_ms": 100,
            "question_id": "q1",
        },
        {"role": "agent", "text": "Go on?", "timestamp_ms": 200, "question_id": "q1"},
        {
            "role": "candidate",
            "text": "four five",
            "timestamp_ms": 300,
            "question_id": "q1",
        },
    ]
    units = segment(transcript=transcript, envelope={"events": []})
    assert units[0].word_count == 5


def test_candidate_engaged_false_for_no_experience() -> None:
    """A single candidate turn classified as no_experience → engaged=False."""
    transcript = [
        {
            "role": "agent",
            "text": "Do you know Kubernetes?",
            "timestamp_ms": 0,
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "text": "I don't know",
            "timestamp_ms": 500,
            "question_id": "q1",
        },
    ]
    # triage decision that fires just before the candidate turn
    envelope = {
        "events": [
            {
                "kind": "engine.v2.triage.decision",
                "t_ms": 498,
                "payload": {
                    "turn_ref": "t-1",
                    "kind": "no_experience",
                    "route": "to_brain",
                },
            }
        ]
    }
    units = segment(transcript=transcript, envelope=envelope)
    assert units[0].candidate_engaged is False


def test_answer_start_ms_is_first_candidate_turn() -> None:
    """answer_start_ms should be the timestamp of the first candidate turn."""
    transcript = [
        {"role": "agent", "text": "Q?", "timestamp_ms": 0, "question_id": "q1"},
        {
            "role": "candidate",
            "text": "Answer here",
            "timestamp_ms": 1500,
            "question_id": "q1",
        },
    ]
    units = segment(transcript=transcript, envelope={"events": []})
    assert units[0].answer_start_ms == 1500


def test_no_question_turns_returns_empty() -> None:
    """No agent turns with question_id → empty list."""
    transcript = [
        {"role": "agent", "text": "Hello!", "timestamp_ms": 0, "question_id": None},
        {"role": "candidate", "text": "Hi", "timestamp_ms": 500, "question_id": None},
    ]
    units = segment(transcript=transcript, envelope={"events": []})
    assert units == []


def test_real_session_question_order_preserved() -> None:
    """Units are returned in question order (as they appear in the transcript)."""
    envelope = json.loads((FIX / "e4072361_envelope.json").read_text())
    transcript = json.loads((FIX / "e4072361_transcript.json").read_text())
    units = segment(transcript=transcript, envelope=envelope)
    # First question is about years of experience
    assert "years" in units[0].question_text.lower()
    # Java question appears after year-of-experience question
    java_idx = next(
        i
        for i, u in enumerate(units)
        if "Java" in u.question_text or "JSON" in u.question_text
    )
    assert java_idx > 0


def test_real_session_probes_nonzero_for_heavy_question() -> None:
    """c99c92ca-a2fe-4814-8932-edaba1e61a5a (RAG/triage design) had many probes — should be > 0."""
    envelope = json.loads((FIX / "e4072361_envelope.json").read_text())
    transcript = json.loads((FIX / "e4072361_transcript.json").read_text())
    units = segment(transcript=transcript, envelope=envelope)
    # Anchor to the known heavy question by its full UUID (4 probes + 3 clarifies in the fixture).
    heavy = next(
        (u for u in units if u.question_id == "c99c92ca-a2fe-4814-8932-edaba1e61a5a"),
        None,
    )
    assert heavy is not None, "c99c92ca question not found in segmented units"
    assert heavy.probes_fired > 0
    assert heavy.clarifies > 0
