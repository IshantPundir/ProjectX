"""Tests for the session-anchored turn-assembler clock (RC-4 fix).

The gen-3 engine used to stamp CANDIDATE turn spans on a RAW ``time.monotonic()``
clock, so spans came out ~1.231e9 ms (~14 days) instead of session-relative ms.
``make_session_clock`` anchors the assembler clock to session start so the spans
the ``TurnAssembler`` stamps are session-relative.

RC-4 also requires that the CANDIDATE clock (assembler, monotonic-anchored) and
the AGENT clock (SessionDriver, wall-anchored) share ONE session origin — both
speakers must live on a single timeline in ``SessionEvidence`` for
``pre_turn_gap_ms`` and the cross-speaker evidence timeline to be meaningful.
``run()`` co-captures ``started_at_wall = datetime.now(UTC)`` and
``t0_monotonic = time.monotonic()`` at a single instant and feeds the wall
anchor to the driver and the monotonic anchor to the assembler. The co-origin
test below asserts that helper-level invariant: a clock pair captured together
agrees on elapsed time. (True end-to-end co-origin across the live
``AgentSession`` is the acceptance gate — it needs the full LiveKit harness.)
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

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


def test_assembler_and_driver_clocks_share_one_origin() -> None:
    """Co-origin invariant (RC-4): a wall + monotonic anchor pair captured at the
    SAME instant (exactly as ``run()`` does at the assembler-build site) agree on
    elapsed time.

    The assembler stamps CANDIDATE spans as ``time.monotonic() - t0_monotonic``
    (``make_session_clock``); the SessionDriver stamps AGENT spans as
    ``(datetime.now(UTC) - started_at_wall)`` (driver.py:335/456). Both measure
    elapsed time from their own anchor, so the two timelines coincide iff the
    anchors name the same instant. Here we co-capture the pair, sleep a known
    interval, and assert the assembler-relative elapsed equals the driver-relative
    elapsed within a small tolerance — i.e. one shared origin.
    """
    # Co-capture the anchor pair together — mirrors run()'s assembler-build site.
    started_at_wall = datetime.now(UTC)
    t0_monotonic = time.monotonic()

    session_clock = make_session_clock(t0_monotonic)

    time.sleep(0.05)

    # CANDIDATE timeline: assembler's session-relative seconds.
    candidate_elapsed_s = session_clock()
    # AGENT timeline: driver's wall-relative seconds (driver.py computes this delta).
    agent_elapsed_s = (datetime.now(UTC) - started_at_wall).total_seconds()

    # Same origin → the two elapsed measurements agree (tolerance covers the few
    # lines of skew between the two reads + monotonic-vs-wall granularity).
    assert abs(candidate_elapsed_s - agent_elapsed_s) < 0.02
