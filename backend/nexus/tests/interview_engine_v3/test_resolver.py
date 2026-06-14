"""
Tests for brain/resolver.py — deterministic positional question resolver.

Selection is purely positional now (+ optional preferred_next_signal hint); the
time-budget + tier scheduler was deleted 2026-06-14.

Covers:
 1. lowest-position unasked is returned
 2. no-repeat: asked questions are skipped
 3. full coverage → close (all asked → None)
 4. preferred_next_signal honored when it matches an unasked question
 5. preferred_next_signal miss → falls back to position
 6. build_question_records: asked/not_reached + closure mapping
"""
from __future__ import annotations

from app.modules.interview_engine.brain.resolver import (
    ResolverQuestion,
    build_question_records,
    resolve_next,
)
from app.modules.interview_runtime.evidence import (
    QuestionOutcome,
    ThreadClosure,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

Q1 = ResolverQuestion(question_id="q1", primary_signal="python", position=1)
Q2 = ResolverQuestion(question_id="q2", primary_signal="system_design", position=2)
Q3 = ResolverQuestion(question_id="q3", primary_signal="kubernetes", position=3)


# ---------------------------------------------------------------------------
# 1. lowest-position unasked
# ---------------------------------------------------------------------------

def test_resolve_next_returns_lowest_position_unasked():
    qs = [
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
    ]
    assert resolve_next(questions=qs, asked_ids=set()).question_id == "a"


# ---------------------------------------------------------------------------
# 2. no-repeat: asked questions skipped
# ---------------------------------------------------------------------------

def test_resolve_next_skips_asked():
    qs = [
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
    ]
    assert resolve_next(questions=qs, asked_ids={"a"}).question_id == "b"


# ---------------------------------------------------------------------------
# 3. full coverage → close
# ---------------------------------------------------------------------------

def test_resolve_next_none_when_all_asked():
    qs = [ResolverQuestion(question_id="a", primary_signal="s1", position=0)]
    assert resolve_next(questions=qs, asked_ids={"a"}) is None


def test_empty_bank_returns_none():
    assert resolve_next(questions=[], asked_ids=set()) is None


# ---------------------------------------------------------------------------
# 4. preferred_next_signal honored
# ---------------------------------------------------------------------------

def test_resolve_next_honors_preferred_signal():
    qs = [
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
    ]
    assert resolve_next(questions=qs, asked_ids=set(), preferred_next_signal="s2").question_id == "b"


# ---------------------------------------------------------------------------
# 5. preferred_next_signal miss → position fallback
# ---------------------------------------------------------------------------

def test_resolve_next_preferred_miss_falls_back_to_position():
    qs = [
        ResolverQuestion(question_id="a", primary_signal="s1", position=0),
        ResolverQuestion(question_id="b", primary_signal="s2", position=1),
    ]
    assert resolve_next(questions=qs, asked_ids=set(), preferred_next_signal="nope").question_id == "a"


# ---------------------------------------------------------------------------
# 6. build_question_records: asked→outcome=asked+closure; not_asked→not_reached+closure None
# ---------------------------------------------------------------------------

def test_build_question_records_full():
    """
    q1 asked with closure=satisfied, q2 asked with closure=tapped_out,
    q3 not asked → not_reached + closure None.
    """
    questions = [Q1, Q2, Q3]
    asked_ids = {"q1", "q2"}
    closures = {
        "q1": ThreadClosure.satisfied,
        "q2": ThreadClosure.tapped_out,
    }

    records = build_question_records(
        questions=questions,
        asked_ids=asked_ids,
        closures=closures,
        asked_at_turn={"q1": "turn-001", "q2": "turn-003"},
        probes_used={"q1": [0], "q2": []},
        probes_available={"q1": 3, "q2": 2, "q3": 1},
        time_spent_s={"q1": 120.0, "q2": 90.0},
    )

    by_id = {r.question_id: r for r in records}

    # q1: asked, satisfied
    assert by_id["q1"].outcome == QuestionOutcome.asked
    assert by_id["q1"].closure == ThreadClosure.satisfied
    assert by_id["q1"].asked_at_turn == "turn-001"
    assert by_id["q1"].probes_used == [0]
    assert by_id["q1"].probes_available == 3
    assert by_id["q1"].time_spent_s == 120.0

    # q2: asked, tapped_out
    assert by_id["q2"].outcome == QuestionOutcome.asked
    assert by_id["q2"].closure == ThreadClosure.tapped_out
    assert by_id["q2"].probes_used == []

    # q3: not reached → closure must be None
    assert by_id["q3"].outcome == QuestionOutcome.not_reached
    assert by_id["q3"].closure is None


def test_build_question_records_not_reached_closure_none():
    """Un-asked question gets outcome=not_reached and closure=None, regardless of closures map."""
    questions = [Q1]
    records = build_question_records(
        questions=questions,
        asked_ids=set(),
        closures={"q1": ThreadClosure.satisfied},  # would be ignored since not asked
    )
    assert len(records) == 1
    assert records[0].outcome == QuestionOutcome.not_reached
    assert records[0].closure is None


def test_build_question_records_defaults():
    """build_question_records handles absent optional maps with correct defaults."""
    records = build_question_records(
        questions=[Q1],
        asked_ids={"q1"},
        closures={"q1": ThreadClosure.absent},
    )
    assert len(records) == 1
    rec = records[0]
    assert rec.outcome == QuestionOutcome.asked
    assert rec.closure == ThreadClosure.absent
    assert rec.asked_at_turn is None
    assert rec.probes_used == []
    assert rec.probes_available == 0
    assert rec.time_spent_s == 0.0
