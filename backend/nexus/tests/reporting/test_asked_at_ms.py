"""Unit tests for the pure `asked_at_ms_by_question` helper in reporting/service.py.

It maps each bank question_id → the EARLIEST agent transcript turn's span.start_ms
(session-relative). Candidate turns and agent turns without a question_id are ignored.
"""
from __future__ import annotations

from app.modules.interview_runtime.evidence import Speaker, TimeSpan, TranscriptTurn
from app.modules.reporting.service import asked_at_ms_by_question


def _turn(
    *, turn_ref: str, speaker: Speaker, start_ms: int, question_id: str | None
) -> TranscriptTurn:
    return TranscriptTurn(
        turn_ref=turn_ref,
        speaker=speaker,
        text="...",
        span=TimeSpan(start_ms=start_ms, end_ms=start_ms + 1000),
        pre_turn_gap_ms=0,
        question_id=question_id,
    )


def test_maps_agent_turn_start_ms_per_question() -> None:
    transcript = [
        _turn(turn_ref="t1", speaker=Speaker.agent, start_ms=4200, question_id="q1"),
        _turn(turn_ref="t2", speaker=Speaker.agent, start_ms=30100, question_id="q2"),
    ]
    assert asked_at_ms_by_question(transcript) == {"q1": 4200, "q2": 30100}


def test_ignores_candidate_turns() -> None:
    transcript = [
        _turn(turn_ref="c1", speaker=Speaker.candidate, start_ms=1000, question_id="q1"),
        _turn(turn_ref="a1", speaker=Speaker.agent, start_ms=5000, question_id="q1"),
    ]
    # The candidate turn (even though it carries q1) must NOT set asked_at_ms.
    assert asked_at_ms_by_question(transcript) == {"q1": 5000}


def test_ignores_agent_turns_without_question_id() -> None:
    transcript = [
        _turn(turn_ref="a0", speaker=Speaker.agent, start_ms=0, question_id=None),
        _turn(turn_ref="a1", speaker=Speaker.agent, start_ms=4200, question_id="q1"),
    ]
    assert asked_at_ms_by_question(transcript) == {"q1": 4200}


def test_earliest_agent_turn_wins_for_repeated_question() -> None:
    transcript = [
        _turn(turn_ref="a1", speaker=Speaker.agent, start_ms=9000, question_id="q1"),
        _turn(turn_ref="a2", speaker=Speaker.agent, start_ms=4200, question_id="q1"),
        _turn(turn_ref="a3", speaker=Speaker.agent, start_ms=12000, question_id="q1"),
    ]
    assert asked_at_ms_by_question(transcript) == {"q1": 4200}


def test_empty_transcript_yields_empty_map() -> None:
    assert asked_at_ms_by_question([]) == {}
