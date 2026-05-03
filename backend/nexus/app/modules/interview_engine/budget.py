"""Per-task and per-session time math.

Pure-logic dataclass — no LiveKit, no LLM, no IO. Deterministic for unit
tests by accepting `now` as a parameter rather than calling time.monotonic
internally. Production callers pass `time.monotonic()` at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_runtime import QuestionConfig


@dataclass
class SessionBudget:
    """Per-session time budget.

    Args:
        started_at_monotonic: time.monotonic() at session start.
        duration_limit_seconds: stage.duration_minutes * 60.
        overhead_seconds: padding subtracted from remaining time when
            checking whether a question fits. Phase 2 default = 5.0
            (controlled via ENGINE_TASK_BUDGET_OVERHEAD_SECONDS).
    """

    started_at_monotonic: float
    duration_limit_seconds: float
    overhead_seconds: float = 5.0

    def elapsed(self, *, now: float) -> float:
        """Seconds since the session started."""
        return now - self.started_at_monotonic

    def remaining(self, *, now: float) -> float:
        """Seconds left in the session. May be negative if overrun."""
        return self.duration_limit_seconds - self.elapsed(now=now)

    def is_expired(self, *, now: float) -> bool:
        """True iff elapsed has reached or exceeded the duration limit."""
        return self.elapsed(now=now) >= self.duration_limit_seconds

    def has_remaining_for(self, q: QuestionConfig, *, now: float) -> bool:
        """True iff the question's estimated time + overhead fits in
        remaining session budget."""
        needed = q.estimated_minutes * 60.0 + self.overhead_seconds
        return self.remaining(now=now) >= needed

    def trim_to_remaining(self, q: QuestionConfig, *, now: float) -> float:
        """Watchdog seconds for a tight-budget mandatory question.

        Returns min(question.estimated_minutes*60, remaining - overhead),
        floored at 0. The caller should treat 0 as "out of time" and
        skip the dispatch (controller does this — see spec §1.1).
        """
        full = q.estimated_minutes * 60.0
        cap = self.remaining(now=now) - self.overhead_seconds
        return max(0.0, min(full, cap))
