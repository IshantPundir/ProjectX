"""Idle-nudge state machine.

Pure logic — no LiveKit dependency. Driven by two inputs:
  * on_user_state(new_state) — the controller calls this from the
    UserStateChangedEvent handler in agent.py's _wire_session_observability
  * on_tick(now_seconds) — the controller calls this from a 1Hz timer
    task started in on_enter and cancelled in _terminate

Outputs one of IdleNudgeOutput per tick. The controller reacts:
  * NUDGE_ONE / NUDGE_TWO -> session.generate_reply(instructions=...)
  * END_UNRESPONSIVE      -> set self._end_outcome and cancel current task
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IdleNudgeState(StrEnum):
    LISTENING = "listening"
    NUDGED_1 = "nudged_1"
    NUDGED_2 = "nudged_2"
    TERMINAL = "terminal"


class IdleNudgeOutput(StrEnum):
    NO_OP = "no_op"
    NUDGE_ONE = "nudge_one"
    NUDGE_TWO = "nudge_two"
    END_UNRESPONSIVE = "end_unresponsive"


@dataclass(frozen=True)
class IdleNudgeConfig:
    first_nudge_seconds: float
    second_nudge_seconds: float
    give_up_seconds: float


class IdleNudgeStateMachine:
    """Drives nudge cadence based on candidate silence.

    Threshold semantics:
      * `first_nudge_seconds` after the candidate goes 'away' from
        LISTENING -> fire NUDGE_ONE, transition to NUDGED_1.
      * `second_nudge_seconds` after entering NUDGED_1 (no resume) ->
        fire NUDGE_TWO, transition to NUDGED_2.
      * `give_up_seconds` after entering NUDGED_2 (no resume) ->
        fire END_UNRESPONSIVE, transition to TERMINAL.
      * Any 'speaking' state (VAD-only — see spec P2-8) at any non-terminal
        state resets to LISTENING and clears the silence-start timer.
    """

    def __init__(self, config: IdleNudgeConfig) -> None:
        self._config = config
        self._state: IdleNudgeState = IdleNudgeState.LISTENING
        # Time at which the current silence window started (reset on speech).
        # None means "we are not in a silence window".
        self._silence_started_at: float | None = None

    @property
    def state(self) -> IdleNudgeState:
        return self._state

    def on_user_state(self, new_state: str, *, now_seconds: float = 0.0) -> None:
        """React to a UserStateChangedEvent from the AgentSession.

        Args:
            new_state: 'listening' | 'speaking' | 'away' (LiveKit's enum
                values, passed as strings).
            now_seconds: time.monotonic() at the event. Optional for tests
                that only care about the resume side effects.
        """
        if self._state is IdleNudgeState.TERMINAL:
            return  # Sticky.
        if new_state == "away":
            # Start a silence window if we aren't already in one.
            if self._silence_started_at is None:
                self._silence_started_at = now_seconds
        elif new_state == "speaking":
            # VAD detected speech -> reset to listening.
            self._state = IdleNudgeState.LISTENING
            self._silence_started_at = None

    def on_tick(self, *, now_seconds: float) -> IdleNudgeOutput:
        """1Hz tick driver.

        Returns the action the controller should take (NO_OP if nothing
        to do). Stateful — transitions internal state on the firing tick.
        """
        if self._state is IdleNudgeState.TERMINAL:
            return IdleNudgeOutput.NO_OP
        if self._silence_started_at is None:
            return IdleNudgeOutput.NO_OP

        elapsed = now_seconds - self._silence_started_at

        if self._state is IdleNudgeState.LISTENING:
            if elapsed >= self._config.first_nudge_seconds:
                self._state = IdleNudgeState.NUDGED_1
                self._silence_started_at = now_seconds
                return IdleNudgeOutput.NUDGE_ONE
            return IdleNudgeOutput.NO_OP

        if self._state is IdleNudgeState.NUDGED_1:
            if elapsed >= self._config.second_nudge_seconds:
                self._state = IdleNudgeState.NUDGED_2
                self._silence_started_at = now_seconds
                return IdleNudgeOutput.NUDGE_TWO
            return IdleNudgeOutput.NO_OP

        if self._state is IdleNudgeState.NUDGED_2:
            if elapsed >= self._config.give_up_seconds:
                self._state = IdleNudgeState.TERMINAL
                self._silence_started_at = None
                return IdleNudgeOutput.END_UNRESPONSIVE
            return IdleNudgeOutput.NO_OP

        return IdleNudgeOutput.NO_OP
