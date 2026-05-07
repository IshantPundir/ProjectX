"""SessionLifecycle FSM + KnockoutFailures + TimeBudget."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.modules.interview_runtime import KnockoutFailure


class LifecycleState(StrEnum):
    pre_start = "pre_start"
    active = "active"
    closing = "closing"
    closed = "closed"


class SessionOutcome(StrEnum):
    completed = "completed"
    knockout_closed = "knockout_closed"
    time_expired = "time_expired"
    candidate_ended = "candidate_ended"
    candidate_disconnected = "candidate_disconnected"
    candidate_unresponsive = "candidate_unresponsive"
    error = "error"


class LifecycleSnapshot(BaseModel):
    state: LifecycleState
    knockout_failures: list[KnockoutFailure] = Field(default_factory=list)
    time_budget_total_seconds: float = Field(ge=0)
    time_elapsed_seconds: float = Field(ge=0, default=0.0)
    last_outcome: SessionOutcome | None = None

    def has_knockout(self) -> bool:
        return len(self.knockout_failures) > 0

    def time_remaining_seconds(self) -> float:
        return max(0.0, self.time_budget_total_seconds - self.time_elapsed_seconds)

    def time_exhausted(self) -> bool:
        return self.time_elapsed_seconds >= self.time_budget_total_seconds


class SessionLifecycle:
    def __init__(self, *, time_budget_total_seconds: float) -> None:
        if time_budget_total_seconds < 0:
            raise ValueError("time_budget_total_seconds must be >= 0")
        self._state: LifecycleState = LifecycleState.pre_start
        self._knockouts: list[KnockoutFailure] = []
        self._time_budget_total = time_budget_total_seconds
        self._time_elapsed = 0.0
        self._last_outcome: SessionOutcome | None = None

    def transition_to_active(self) -> None:
        if self._state != LifecycleState.pre_start:
            raise ValueError(
                f"Cannot transition pre_start→active from {self._state.value}"
            )
        self._state = LifecycleState.active

    def transition_to_closing(self) -> None:
        if self._state not in (LifecycleState.active, LifecycleState.pre_start):
            raise ValueError(f"Cannot transition to closing from {self._state.value}")
        self._state = LifecycleState.closing

    def transition_to_closed(self) -> None:
        if self._state not in (LifecycleState.closing, LifecycleState.active):
            raise ValueError(f"Cannot transition to closed from {self._state.value}")
        self._state = LifecycleState.closed

    def record_knockout(self, failure: KnockoutFailure) -> None:
        self._knockouts.append(failure)

    def set_time_elapsed(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        self._time_elapsed = seconds

    def set_last_outcome(self, outcome: SessionOutcome) -> None:
        self._last_outcome = outcome

    def snapshot(self) -> LifecycleSnapshot:
        return LifecycleSnapshot(
            state=self._state,
            knockout_failures=[k.model_copy() for k in self._knockouts],
            time_budget_total_seconds=self._time_budget_total,
            time_elapsed_seconds=self._time_elapsed,
            last_outcome=self._last_outcome,
        )

    @classmethod
    def from_snapshot(cls, snap: LifecycleSnapshot) -> "SessionLifecycle":
        lc = cls(time_budget_total_seconds=snap.time_budget_total_seconds)
        lc._state = snap.state
        lc._knockouts = [k.model_copy() for k in snap.knockout_failures]
        lc._time_elapsed = snap.time_elapsed_seconds
        lc._last_outcome = snap.last_outcome
        return lc
