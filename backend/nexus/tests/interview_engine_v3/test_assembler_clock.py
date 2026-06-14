"""Tests for the session-anchored turn-assembler clock (RC-4 fix).

The gen-3 engine used to stamp CANDIDATE turn spans on a RAW ``time.monotonic()``
clock, so spans came out ~1.231e9 ms (~14 days) instead of session-relative ms.
``make_session_clock`` anchors the assembler clock to session start so the spans
the ``TurnAssembler`` stamps are session-relative.
"""
from __future__ import annotations

import time

from app.modules.interview_engine.agent import make_session_clock
from app.modules.interview_engine.turn_assembler import (
    FakeTimerScheduler,
    TurnAssembler,
)
from app.modules.interview_engine.turn_source import CommittedTurnSource


def test_session_clock_is_session_relative_near_zero() -> None:
    """A clock anchored at t0 reads near zero just after t0 — NOT ~1e9 seconds."""
    t0 = time.monotonic()
    clock = make_session_clock(t0)

    now = clock()

    # Session-relative: a tiny positive elapsed, never the raw monotonic (~1e6 s).
    assert 0.0 <= now < 1.0


async def test_assembler_stamps_session_relative_span() -> None:
    """Feeding the anchored clock into a real TurnAssembler yields small spans."""
    t0 = time.monotonic()
    clock = make_session_clock(t0)

    sink = CommittedTurnSource()
    timer = FakeTimerScheduler()
    assembler = TurnAssembler(
        sink=sink,
        clock=clock,
        timer=timer,
        grace_s=0.5,
        max_duration_s=60.0,
        enabled=True,
    )

    assembler.submit_fragment("I built a payments service.")
    timer.fire_all()  # grace elapses → flush

    turn = await sink.get()
    assert turn is not None
    # Session-relative ms: well under a minute, NOT ~1.231e9 ms (~14 days).
    assert 0 <= turn.span.start_ms < 60_000
    assert 0 <= turn.span.end_ms < 60_000
