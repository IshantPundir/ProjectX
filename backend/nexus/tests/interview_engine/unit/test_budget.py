"""Unit tests for SessionBudget — pure-logic time math, no LiveKit."""

from __future__ import annotations

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_runtime.schemas import (
    QuestionConfig,
    QuestionRubric,
)


def make_question(*, estimated_minutes: float, qid: str = "q-1") -> QuestionConfig:
    """Build a minimally-valid QuestionConfig fixture."""
    return QuestionConfig(
        id=qid,
        position=0,
        text="A long enough placeholder question text body.",
        signal_values=["python"],
        estimated_minutes=estimated_minutes,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="e", meets_bar="m", below_bar="b"),
        evaluation_hint="some hint that is at least 10 chars",
    )


class TestSessionBudgetElapsed:
    def test_elapsed_zero_at_start(self) -> None:
        b = SessionBudget(started_at_monotonic=100.0, duration_limit_seconds=900.0)
        assert b.elapsed(now=100.0) == 0.0

    def test_elapsed_increases_with_now(self) -> None:
        b = SessionBudget(started_at_monotonic=100.0, duration_limit_seconds=900.0)
        assert b.elapsed(now=160.0) == 60.0


class TestSessionBudgetRemaining:
    def test_remaining_full_at_start(self) -> None:
        b = SessionBudget(started_at_monotonic=0.0, duration_limit_seconds=900.0)
        assert b.remaining(now=0.0) == 900.0

    def test_remaining_negative_when_overrun(self) -> None:
        b = SessionBudget(started_at_monotonic=0.0, duration_limit_seconds=900.0)
        assert b.remaining(now=1000.0) == -100.0


class TestSessionBudgetIsExpired:
    def test_not_expired_at_start(self) -> None:
        b = SessionBudget(started_at_monotonic=0.0, duration_limit_seconds=900.0)
        assert b.is_expired(now=0.0) is False

    def test_expired_at_boundary(self) -> None:
        b = SessionBudget(started_at_monotonic=0.0, duration_limit_seconds=900.0)
        assert b.is_expired(now=900.0) is True

    def test_expired_past_boundary(self) -> None:
        b = SessionBudget(started_at_monotonic=0.0, duration_limit_seconds=900.0)
        assert b.is_expired(now=900.5) is True


class TestSessionBudgetHasRemainingFor:
    def test_true_when_lots_of_time_left(self) -> None:
        b = SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=5.0,
        )
        q = make_question(estimated_minutes=3.0)  # 180s + 5s overhead = 185s needed
        assert b.has_remaining_for(q, now=0.0) is True

    def test_false_when_estimate_exceeds_remaining(self) -> None:
        b = SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=5.0,
        )
        q = make_question(estimated_minutes=3.0)  # needs 185s
        # 800s elapsed -> only 100s left; question needs 185s -> false
        assert b.has_remaining_for(q, now=800.0) is False

    def test_true_at_exact_boundary(self) -> None:
        b = SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=5.0,
        )
        q = make_question(estimated_minutes=3.0)  # 185s needed
        # 715s elapsed -> 185s remaining -> true (>=)
        assert b.has_remaining_for(q, now=715.0) is True


class TestSessionBudgetTrimToRemaining:
    def test_returns_estimated_when_plenty_of_time(self) -> None:
        b = SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=5.0,
        )
        q = make_question(estimated_minutes=3.0)
        # Plenty of time: returns the question's full estimate (180s).
        assert b.trim_to_remaining(q, now=0.0) == 180.0

    def test_returns_remaining_minus_overhead_when_tight(self) -> None:
        b = SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=5.0,
        )
        q = make_question(estimated_minutes=3.0)
        # 800s elapsed -> 100s remaining -> 95s after overhead
        assert b.trim_to_remaining(q, now=800.0) == 95.0

    def test_returns_zero_when_overrun(self) -> None:
        b = SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=5.0,
        )
        q = make_question(estimated_minutes=3.0)
        assert b.trim_to_remaining(q, now=1000.0) == 0.0
