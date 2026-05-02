"""Unit tests for IdleNudgeStateMachine — pure logic, no LiveKit."""

from __future__ import annotations

import pytest

from app.modules.interview_engine.idle_nudge import (
    IdleNudgeConfig,
    IdleNudgeOutput,
    IdleNudgeState,
    IdleNudgeStateMachine,
)


def make_sm(
    *,
    first: float = 30.0,
    second: float = 30.0,
    give_up: float = 30.0,
) -> IdleNudgeStateMachine:
    return IdleNudgeStateMachine(
        IdleNudgeConfig(
            first_nudge_seconds=first,
            second_nudge_seconds=second,
            give_up_seconds=give_up,
        )
    )


class TestStartingState:
    def test_initial_state_is_listening(self) -> None:
        sm = make_sm()
        assert sm.state is IdleNudgeState.LISTENING

    def test_tick_before_any_silence_is_noop(self) -> None:
        sm = make_sm()
        assert sm.on_tick(now_seconds=10.0) is IdleNudgeOutput.NO_OP


class TestFirstNudge:
    def test_no_op_before_threshold(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        assert sm.on_tick(now_seconds=29.999) is IdleNudgeOutput.NO_OP

    def test_fires_at_threshold(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        assert sm.on_tick(now_seconds=30.0) is IdleNudgeOutput.NUDGE_ONE
        assert sm.state is IdleNudgeState.NUDGED_1

    def test_fires_only_once_then_no_op(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        assert sm.on_tick(now_seconds=30.0) is IdleNudgeOutput.NUDGE_ONE
        assert sm.on_tick(now_seconds=31.0) is IdleNudgeOutput.NO_OP


class TestResumeOnSpeech:
    def test_speech_during_listening_does_nothing(self) -> None:
        sm = make_sm()
        sm.on_user_state("speaking", now_seconds=5.0)
        assert sm.state is IdleNudgeState.LISTENING

    def test_speech_resets_first_nudge_timer(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        sm.on_tick(now_seconds=20.0)  # not yet
        sm.on_user_state("speaking", now_seconds=20.0)
        assert sm.state is IdleNudgeState.LISTENING
        # New silence at 25s; should not fire until 25 + 30 = 55s
        sm.on_user_state("away", now_seconds=25.0)
        assert sm.on_tick(now_seconds=54.999) is IdleNudgeOutput.NO_OP
        assert sm.on_tick(now_seconds=55.0) is IdleNudgeOutput.NUDGE_ONE

    def test_speech_after_first_nudge_returns_to_listening(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        sm.on_tick(now_seconds=30.0)  # NUDGE_ONE
        sm.on_user_state("speaking", now_seconds=35.0)
        assert sm.state is IdleNudgeState.LISTENING


class TestSecondNudge:
    def test_fires_after_second_silence(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        sm.on_tick(now_seconds=30.0)  # NUDGE_ONE -> NUDGED_1
        # Second-nudge silence starts at the nudge-one fire time. So 30 + 30 = 60s.
        assert sm.on_tick(now_seconds=59.999) is IdleNudgeOutput.NO_OP
        assert sm.on_tick(now_seconds=60.0) is IdleNudgeOutput.NUDGE_TWO
        assert sm.state is IdleNudgeState.NUDGED_2


class TestEndUnresponsive:
    def test_fires_after_give_up(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        sm.on_tick(now_seconds=30.0)  # NUDGE_ONE
        sm.on_tick(now_seconds=60.0)  # NUDGE_TWO
        # Give-up fires at 60 + 30 = 90s.
        assert sm.on_tick(now_seconds=89.999) is IdleNudgeOutput.NO_OP
        assert sm.on_tick(now_seconds=90.0) is IdleNudgeOutput.END_UNRESPONSIVE
        assert sm.state is IdleNudgeState.TERMINAL

    def test_terminal_is_sticky(self) -> None:
        sm = make_sm()
        sm.on_user_state("away", now_seconds=0.0)
        sm.on_tick(now_seconds=30.0)
        sm.on_tick(now_seconds=60.0)
        sm.on_tick(now_seconds=90.0)
        # Subsequent ticks and inputs are no-ops in TERMINAL.
        assert sm.on_tick(now_seconds=200.0) is IdleNudgeOutput.NO_OP
        sm.on_user_state("speaking", now_seconds=200.0)
        assert sm.state is IdleNudgeState.TERMINAL
