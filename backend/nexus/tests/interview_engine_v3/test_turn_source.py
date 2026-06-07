"""Tests for CommittedTurnSource (Path A+ native turn detection).

The drive loop's turn feed under LiveKit native turn detection. The
`on_user_turn_completed` hook submits the full final transcript here; the
SessionDriver drive loop consumes it. Replaces the gen-3 manual Ear poll loop.

Load-bearing behaviors:
  1. Non-empty transcripts are delivered FIFO.
  2. Empty / whitespace-only transcripts are DROPPED (prevents spurious no-op
     turns from empty STT finals — the eot_delay=0/conf=0 commits seen in F3).
  3. close() unblocks a pending get() with a None sentinel so the loop exits.
  4. submit() after close() is a no-op (returns False).
"""
from __future__ import annotations

import asyncio

import pytest

from app.modules.interview_engine.turn_source import CommittedTurnSource


async def test_submit_then_get_returns_transcript() -> None:
    src = CommittedTurnSource()
    assert src.submit("I have five years of experience.") is True
    assert await src.get() == "I have five years of experience."


@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", None])
async def test_empty_or_whitespace_dropped(bad) -> None:
    src = CommittedTurnSource()
    assert src.submit(bad) is False
    # A real turn submitted afterwards is what get() returns — the empty one
    # never occupied the queue.
    src.submit("real answer")
    assert await src.get() == "real answer"


async def test_fifo_order() -> None:
    src = CommittedTurnSource()
    src.submit("first")
    src.submit("second")
    src.submit("third")
    assert await src.get() == "first"
    assert await src.get() == "second"
    assert await src.get() == "third"


async def test_close_unblocks_pending_get_with_none() -> None:
    src = CommittedTurnSource()

    async def _close_soon() -> None:
        await asyncio.sleep(0.01)
        src.close()

    asyncio.create_task(_close_soon())
    # get() is blocked (queue empty) until close() injects the None sentinel.
    assert await src.get() is None


async def test_submit_after_close_dropped() -> None:
    src = CommittedTurnSource()
    src.close()
    assert src.submit("too late") is False


async def test_close_drains_pending_then_none() -> None:
    src = CommittedTurnSource()
    src.submit("pending")
    src.close()
    # Pending real turns are delivered before the close sentinel.
    assert await src.get() == "pending"
    assert await src.get() is None
