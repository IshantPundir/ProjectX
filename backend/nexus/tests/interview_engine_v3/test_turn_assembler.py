"""Tests for the TurnAssembler (livekit-free fragment assembly)."""
from __future__ import annotations

import asyncio

import pytest

from app.modules.interview_engine.turn_assembler import (
    AsyncioTimerScheduler,
    FakeTimerScheduler,
    TurnAssembler,
)
from app.modules.interview_engine.turn_source import CommittedTurnSource


async def test_asyncio_scheduler_fires_after_delay() -> None:
    sched = AsyncioTimerScheduler()
    fired = []
    sched.schedule(0.01, lambda: fired.append(True))
    await asyncio.sleep(0.03)
    assert fired == [True]


async def test_asyncio_scheduler_cancel() -> None:
    sched = AsyncioTimerScheduler()
    fired = []
    handle = sched.schedule(0.02, lambda: fired.append(True))
    handle.cancel()
    await asyncio.sleep(0.04)
    assert fired == []


def test_fake_scheduler_fires_on_command() -> None:
    sched = FakeTimerScheduler()
    fired = []
    sched.schedule(0.5, lambda: fired.append("a"))
    assert fired == []
    sched.fire_all()
    assert fired == ["a"]


def test_fake_scheduler_cancel_prevents_fire() -> None:
    sched = FakeTimerScheduler()
    fired = []
    h = sched.schedule(0.5, lambda: fired.append("a"))
    h.cancel()
    sched.fire_all()
    assert fired == []


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _make(grace_s: float = 0.5, max_s: float = 45.0, enabled: bool = True):
    sink = CommittedTurnSource()
    clock = _Clock()
    timer = FakeTimerScheduler()
    asm = TurnAssembler(
        sink=sink, clock=clock, timer=timer,
        grace_s=grace_s, max_duration_s=max_s, enabled=enabled,
    )
    return asm, sink, clock, timer


async def test_single_fragment_flushes_after_grace() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("I led the migration.")
    assert sink._queue.qsize() == 0
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "I led the migration."
    assert turn.suppress_bridge is False


async def test_disabled_passthrough_flushes_immediately() -> None:
    asm, sink, clock, timer = _make(enabled=False)
    asm.submit_fragment("hello")
    turn = await sink.get()
    assert turn.text == "hello"


async def test_two_fragments_within_grace_merge() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("First, I assessed the tenant health,")
    asm.submit_fragment("then I built a pilot ring.")
    assert sink._queue.qsize() == 0
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "First, I assessed the tenant health, then I built a pilot ring."


async def test_vad_speaking_holds_then_resume_merges() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("Compliance policy,")
    asm.note_user_speaking()
    timer.fire_all()
    assert sink._queue.qsize() == 0
    asm.note_user_stopped()
    asm.submit_fragment("configuration profiles.")
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "Compliance policy, configuration profiles."


async def test_max_duration_force_flush() -> None:
    asm, sink, clock, timer = _make(max_s=10.0)
    asm.note_user_stopped()
    clock.t = 100.0
    asm.submit_fragment("start")
    clock.t = 111.0
    asm.submit_fragment("continued")
    turn = await sink.get()
    assert turn.text == "start continued"


async def test_in_flight_not_superseded_confirm_commits() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("done answer")
    timer.fire_all()
    await sink.get()
    assert asm.is_superseded() is False
    asm.confirm_committed()
    asm.submit_fragment("next answer")
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "next answer"
    assert turn.is_reflush is False


async def test_merge_back_when_user_resumes_in_flight() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("part one")
    timer.fire_all()
    first = await sink.get()
    assert first.text == "part one"
    asm.note_user_speaking()
    assert asm.is_superseded() is True
    asm.note_user_stopped()
    asm.submit_fragment("part two")
    timer.fire_all()
    merged = await sink.get()
    assert merged.text == "part one part two"
    assert merged.suppress_bridge is True
    assert merged.is_reflush is True


async def test_false_resume_reflushes_same_text() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("only answer")
    timer.fire_all()
    await sink.get()
    asm.note_user_speaking()
    asm.note_user_stopped()
    timer.fire_all()
    reflush = await sink.get()
    assert reflush.text == "only answer"


async def test_resume_after_confirm_is_new_turn() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("answer one")
    timer.fire_all()
    await sink.get()
    asm.confirm_committed()             # loop committed it (atomic, at the checkpoint)
    asm.note_user_speaking()            # candidate resumes AFTER commit
    assert asm.is_superseded() is False  # NOT a merge-back — it's a new turn
    asm.note_user_stopped()
    asm.submit_fragment("answer two")
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "answer two"
    assert turn.is_reflush is False
