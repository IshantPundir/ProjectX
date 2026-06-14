"""
Gen-3 Interview Engine — deterministic question resolver.

This module owns EXCLUSIVELY:
  • Which question is asked next (no-repeat, no LLM leapfrogging)
  • Whether the session should close (all questions asked)
  • Session-end `QuestionRecord` finalization for the engine→report contract

The brain NEVER hard-selects the next main question. It may emit an OPTIONAL
`preferred_next_signal` hint that this resolver honors when a matching unasked
question exists; otherwise positional ordering is used.

Selection is purely positional now — the time-budget + tier scheduler was
deleted 2026-06-14 (the question-bank already sizes question COUNT to the time
budget, so at runtime the engine just asks its questions in order).

Design: PURE logic — no livekit, no I/O, no LLM, no async.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_runtime.evidence import (
    QuestionOutcome,
    QuestionRecord,
    ThreadClosure,
)


# ============================================================================
# ResolverQuestion — compact bank view (no rubric, no text)
# ============================================================================

@dataclass(frozen=True)
class ResolverQuestion:
    """The resolver's compact view of one bank question.

    Tiny by design — everything the resolver needs to pick the next question and
    nothing more. Selection is purely positional now (the time-budget + tier
    scheduler was deleted 2026-06-14).
    """
    question_id: str
    primary_signal: str
    position: int          # absolute ordering within the bank (ascending = earlier)


# ============================================================================
# Main resolver
# ============================================================================

def resolve_next(
    *,
    questions: list[ResolverQuestion],
    asked_ids: set[str],
    preferred_next_signal: str | None = None,
) -> ResolverQuestion | None:
    """Return the next question to ask, or None to CLOSE the session.

    1. Filter to unasked; if none remain → None (full coverage, close).
    2. If the brain emitted a preferred_next_signal that matches an unasked
       question, honor it (naturalness hint).
    3. Otherwise return the lowest-position unasked question.
    """
    unasked = [q for q in questions if q.question_id not in asked_ids]
    if not unasked:
        return None
    if preferred_next_signal is not None:
        pref = next(
            (q for q in unasked if q.primary_signal == preferred_next_signal),
            None,
        )
        if pref is not None:
            return pref
    return min(unasked, key=lambda q: q.position)


# ============================================================================
# Session-end record builder
# ============================================================================

def build_question_records(
    *,
    questions: list[ResolverQuestion],
    asked_ids: set[str],
    closures: dict[str, ThreadClosure],
    asked_at_turn: dict[str, str] | None = None,
    probes_used: dict[str, list[int]] | None = None,
    probes_available: dict[str, int] | None = None,
    time_spent_s: dict[str, float] | None = None,
) -> list[QuestionRecord]:
    """Build one `QuestionRecord` per bank question for the engine→report contract.

    Called once at session end. The loop supplies the per-thread `closures` it
    inferred from the brain's final coverage_after + stance (and `truncated` when
    the session ended with the thread still open).

    Rules:
      - outcome = `asked`       when question_id in asked_ids
      - outcome = `not_reached` otherwise
      - closure = closures.get(question_id) ONLY for asked questions;
                  not_reached questions always have closure=None (no thread ran)
    """
    _asked_at_turn = asked_at_turn or {}
    _probes_used = probes_used or {}
    _probes_available = probes_available or {}
    _time_spent_s = time_spent_s or {}

    records: list[QuestionRecord] = []
    for q in questions:
        qid = q.question_id
        if qid in asked_ids:
            # Thread was opened — record what happened.
            records.append(QuestionRecord(
                question_id=qid,
                primary_signal=q.primary_signal,
                outcome=QuestionOutcome.asked,
                closure=closures.get(qid),  # None only if loop forgot to set it
                asked_at_turn=_asked_at_turn.get(qid),
                probes_used=_probes_used.get(qid, []),
                probes_available=_probes_available.get(qid, 0),
                time_spent_s=_time_spent_s.get(qid, 0.0),
            ))
        else:
            # Never reached — no thread, no closure.
            records.append(QuestionRecord(
                question_id=qid,
                primary_signal=q.primary_signal,
                outcome=QuestionOutcome.not_reached,
                closure=None,  # no thread ran → closure is meaningless
                asked_at_turn=None,
                probes_used=[],
                probes_available=_probes_available.get(qid, 0),
                time_spent_s=0.0,
            ))
    return records
