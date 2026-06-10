"""Tests for CommittedTurnSource — now carries AssembledTurn, not bare str."""
from __future__ import annotations

import asyncio

import pytest

from app.modules.interview_engine.turn_source import AssembledTurn, CommittedTurnSource
from app.modules.interview_runtime.evidence import TimeSpan


def _turn(text: str) -> AssembledTurn:
    return AssembledTurn(
        text=text,
        span=TimeSpan(start_ms=0, end_ms=0),
        suppress_bridge=False,
        is_reflush=False,
    )


async def test_submit_then_get_returns_turn() -> None:
    src = CommittedTurnSource()
    t = _turn("I have five years of experience.")
    assert src.submit(t) is True
    got = await src.get()
    assert got is t


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
async def test_empty_or_whitespace_dropped(bad) -> None:
    src = CommittedTurnSource()
    assert src.submit(_turn(bad)) is False
    real = _turn("real answer")
    src.submit(real)
    assert await src.get() is real


async def test_none_dropped() -> None:
    src = CommittedTurnSource()
    assert src.submit(None) is False


async def test_fifo_order() -> None:
    src = CommittedTurnSource()
    a, b, c = _turn("first"), _turn("second"), _turn("third")
    src.submit(a)
    src.submit(b)
    src.submit(c)
    assert (await src.get()) is a
    assert (await src.get()) is b
    assert (await src.get()) is c


async def test_close_unblocks_pending_get_with_none() -> None:
    src = CommittedTurnSource()

    async def _close_soon() -> None:
        await asyncio.sleep(0.01)
        src.close()

    asyncio.create_task(_close_soon())
    assert await src.get() is None


async def test_submit_after_close_dropped() -> None:
    src = CommittedTurnSource()
    src.close()
    assert src.submit(_turn("too late")) is False


async def test_close_drains_pending_then_none() -> None:
    src = CommittedTurnSource()
    pending = _turn("pending")
    src.submit(pending)
    src.close()
    assert (await src.get()) is pending
    assert (await src.get()) is None
