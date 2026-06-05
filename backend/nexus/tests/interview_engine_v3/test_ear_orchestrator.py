"""Tests for the Ear orchestrator (B4) — `ear/orchestrator.py`.

Tests the `Ear` class in isolation using:
  - A duck-typed mock session (records commit_user_turn / say / interrupt calls)
  - A fake Smart Turn detector injected into TurnAudioBuffer
  - An explicit EarLadderConfig with known thresholds
  - A fake clock (integer ms passed directly)

Five tests:
  1. DONE fixture → EarDecision.commit; act → NO-OP (commit is owned by the
     poll loop in agent.py::setup_ear, not by Ear.act). commit_user_turn must
     NOT be called from act; say must NOT be called.
  2. THINKING fixture → EarDecision.hold_cue; act → say called once, commit_user_turn NOT called.
  3. Cue fires at most once per pause (idempotent across multiple act calls in same pause).
  4. min-silence floor → EarDecision.wait; act → neither commit nor say called.
  5. buffer reset on speaking-start: on_user_state("speaking") clears the buffer (len 0).

No I/O, no LiveKit, no database — fully unit-testable.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.modules.interview_engine.ear.ladder import EarDecision, EarLadderConfig
from app.modules.interview_engine.ear.orchestrator import DEFAULT_HOLD_CUE, Ear
from app.modules.interview_engine.ear.smart_turn import TurnAudioBuffer
from app.modules.interview_engine.ear.vad_gate import SpeechActivity


# ---------------------------------------------------------------------------
# Shared config with KNOWN thresholds — tests are designed around these.
# ---------------------------------------------------------------------------

CFG = EarLadderConfig(
    smart_turn_commit_thr=0.5,   # voice_complete when smart_turn_prob >= 0.5
    text_commit_thr=0.02,        # text_complete when text_eou_prob >= 0.02
    min_silence_ms=300,          # below this → WAIT unconditionally
    hold_cue_ms=2000,            # at or above this (& incomplete) → HOLD_CUE
)


# ---------------------------------------------------------------------------
# Helpers — fake detector and mock session
# ---------------------------------------------------------------------------


class _FakeDetector:
    """Fake Smart Turn detector — always returns the configured prediction."""

    def __init__(self, probability: float) -> None:
        self._probability = probability
        self._called = 0

    def predict(self, audio: np.ndarray, *, sample_rate: int) -> dict:
        self._called += 1
        prediction = 1 if self._probability >= 0.5 else 0
        return {"prediction": prediction, "probability": self._probability}


class _MockSession:
    """Duck-typed mock of livekit.AgentSession — records method calls."""

    def __init__(self) -> None:
        self.commit_calls: int = 0
        self.say_calls: list[str] = []
        self.interrupt_calls: int = 0

    def commit_user_turn(self) -> None:
        self.commit_calls += 1

    async def say(self, text: str, **kwargs) -> None:
        self.say_calls.append(text)

    def interrupt(self) -> None:
        self.interrupt_calls += 1


def _make_ear(detector_prob: float) -> tuple[Ear, _FakeDetector]:
    """Construct an Ear with an injected fake detector, no EOU model."""
    detector = _FakeDetector(detector_prob)
    buffer = TurnAudioBuffer(sample_rate=16000, max_seconds=8.0, detector=detector)
    activity = SpeechActivity()
    ear = Ear(
        cfg=CFG,
        buffer=buffer,
        activity=activity,
        eou_model=None,
    )
    return ear, detector


def _dummy_audio(samples: int = 1600) -> np.ndarray:
    """Return a short dummy float32 audio frame (100ms at 16kHz)."""
    return np.zeros(samples, dtype=np.float32)


# ---------------------------------------------------------------------------
# Test 1: DONE fixture → commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_done_fixture_produces_commit_and_act_is_noop():
    """speaking(t=0) → listening(t=2000) + high smart_turn_prob → commit.

    Ear.act(commit) is a NO-OP: commit is owned by the poll loop in
    agent.py::setup_ear, which calls session.commit_user_turn() and captures
    the returned transcript. Ear.act must NOT call commit_user_turn() — that
    would fire a second commit and discard the transcript.

    After act: commit_user_turn NOT called (no-op); say NOT called.
    """
    ear, _ = _make_ear(detector_prob=0.9)   # well above 0.5 threshold
    session = _MockSession()

    # Simulate a turn: speaking then stopped
    ear.on_user_state("speaking", now_ms=0)
    ear.append_audio(_dummy_audio())          # some audio buffered
    ear.on_user_state("listening", now_ms=2000)

    # Evaluate at t=2400 → 400ms silence (above min_silence_ms=300)
    decision, telemetry = await ear.evaluate(now_ms=2400, chat_ctx=None)

    assert decision == EarDecision.commit, (
        f"Expected commit with high prob + 400ms silence, got {decision!r}"
    )
    assert "smart_turn_prob" in telemetry
    assert "vad_silence_ms" in telemetry

    await ear.act(session, decision)

    # act(commit) is a no-op: the poll loop owns commit_user_turn
    assert session.commit_calls == 0, (
        "commit_user_turn must NOT be called from Ear.act — "
        "commit is owned by agent.py::setup_ear's poll loop"
    )
    assert session.say_calls == [], "say must NOT be called on commit"


# ---------------------------------------------------------------------------
# Test 2: THINKING fixture → hold_cue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_fixture_produces_hold_cue_and_calls_say():
    """speaking → listening + low prob + long silence → hold_cue.

    After act: say called once with the hold cue; commit_user_turn NOT called.
    """
    ear, _ = _make_ear(detector_prob=0.1)   # well below 0.5 threshold
    session = _MockSession()

    ear.on_user_state("speaking", now_ms=0)
    ear.append_audio(_dummy_audio())
    ear.on_user_state("listening", now_ms=1000)

    # Evaluate at t=3000 → 2000ms silence (== hold_cue_ms=2000 → hold_cue)
    decision, telemetry = await ear.evaluate(now_ms=3000, chat_ctx=None)

    assert decision == EarDecision.hold_cue, (
        f"Expected hold_cue with low prob + 2000ms silence, got {decision!r}"
    )

    await ear.act(session, decision)

    assert session.commit_calls == 0, "commit_user_turn must NOT be called on hold_cue"
    assert len(session.say_calls) == 1, "say must be called exactly once"
    assert session.say_calls[0] == DEFAULT_HOLD_CUE


# ---------------------------------------------------------------------------
# Test 3: Cue fires at most once per pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_cue_fires_at_most_once_per_pause_then_resets():
    """Multiple act(hold_cue) calls in the same pause → say fires only once.

    After a new speaking-start, a subsequent hold_cue can fire again.
    """
    ear, _ = _make_ear(detector_prob=0.1)
    session = _MockSession()

    # First pause
    ear.on_user_state("speaking", now_ms=0)
    ear.append_audio(_dummy_audio())
    ear.on_user_state("listening", now_ms=500)

    # Call act(hold_cue) twice in the same pause
    await ear.act(session, EarDecision.hold_cue)
    await ear.act(session, EarDecision.hold_cue)

    assert len(session.say_calls) == 1, (
        "say must fire exactly once per pause, even if act is called multiple times"
    )

    # New speaking-start resets the cue guard
    ear.on_user_state("speaking", now_ms=3000)

    # Second pause — cue should fire again
    ear.append_audio(_dummy_audio())
    ear.on_user_state("listening", now_ms=4000)
    await ear.act(session, EarDecision.hold_cue)

    assert len(session.say_calls) == 2, (
        "After a new speaking-start, hold_cue should fire again"
    )


# ---------------------------------------------------------------------------
# Test 4: min-silence floor → wait
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_min_silence_floor_produces_wait():
    """High prob but silence < min_silence_ms → EarDecision.wait.

    act: neither commit_user_turn nor say is called.
    """
    ear, _ = _make_ear(detector_prob=0.95)  # voice "complete" by signal
    session = _MockSession()

    ear.on_user_state("speaking", now_ms=0)
    ear.append_audio(_dummy_audio())
    ear.on_user_state("listening", now_ms=1000)

    # Evaluate at t=1150 → only 150ms silence (< min_silence_ms=300) → wait
    decision, _ = await ear.evaluate(now_ms=1150, chat_ctx=None)

    assert decision == EarDecision.wait, (
        f"Expected wait under silence floor, got {decision!r}"
    )

    await ear.act(session, decision)

    assert session.commit_calls == 0, "commit_user_turn must NOT be called on wait"
    assert session.say_calls == [], "say must NOT be called on wait"


# ---------------------------------------------------------------------------
# Test 5: buffer reset on speaking-start
# ---------------------------------------------------------------------------


def test_buffer_reset_on_speaking_start():
    """on_user_state('speaking', ...) must clear the buffer.

    Each new turn starts with len(buffer)==0 so predict() only sees
    the current turn's audio.
    """
    ear, _ = _make_ear(detector_prob=0.5)

    # First turn — accumulate audio
    ear.on_user_state("speaking", now_ms=0)
    ear.append_audio(_dummy_audio(samples=16000))  # 1 second of audio
    ear.on_user_state("listening", now_ms=1000)

    # Verify there is buffered audio (sanity)
    assert len(ear._buffer) > 0, "Buffer should have audio after the first turn"

    # New speaking-start should reset the buffer
    ear.on_user_state("speaking", now_ms=3000)

    assert len(ear._buffer) == 0, (
        "Buffer must be empty (reset) at the start of a new speaking turn"
    )
