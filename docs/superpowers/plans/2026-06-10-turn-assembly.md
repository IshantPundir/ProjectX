# Turn Assembly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge consecutive fragmented candidate turns into one logical turn before the brain runs, so a paused answer is graded as one complete answer instead of several thin fragments.

**Architecture:** A new livekit-free `TurnAssembler` sits between the LiveKit `on_user_turn_completed` hook and the existing `CommittedTurnSource`. It buffers fragments, uses VAD `user_state_changed` as the "candidate resumed" signal with a short grace timer, and flushes one `AssembledTurn` per logical answer. A continuation that arrives just after a flush is merged back through a single checkpoint at the note-commit point-of-no-return in `loop.py` (no preemption, no supersession of committed evidence).

**Tech Stack:** Python 3.13, asyncio, pydantic v2, pytest (async), structlog. livekit-agents 1.5.7 (only `agent.py` touches it). Tests run in the nexus container.

**Spec:** `docs/superpowers/specs/2026-06-10-turn-assembly-design.md` — read it first.

**Test runner (all tasks):**
```bash
docker compose exec -T nexus python -m pytest <path> -q
```
(Plain pytest, not `--cov`: these tests are livekit-free and avoid the pytest-cov/PyO3 segfault.)

**Branch:** `feat/turn-assembly` (already created; the config-consolidation + spec are already committed on it).

---

## Task 1: Config knobs (single source of truth)

**Files:**
- Modify: `app/config.py` (turn-handling block, after `engine_endpointing_max_delay_s`)
- Modify: `app/ai/config.py` (after the `engine_endpointing_*` properties)
- Modify: `.env.example` (turn-handling block)
- Test: `tests/test_engine_settings.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine_settings.py`:

```python
def test_turn_assembly_settings_defaults():
    """Turn-assembly knobs exist with the design-spec defaults."""
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["engine_assembly_enabled"].default is True
    assert fields["engine_assembly_grace_s"].default == 0.5
    assert fields["engine_assembly_max_duration_s"].default == 45.0


def test_aiconfig_exposes_turn_assembly(monkeypatch):
    from app.ai.config import ai_config

    assert ai_config.engine_assembly_enabled is True
    assert ai_config.engine_assembly_grace_s == 0.5
    assert ai_config.engine_assembly_max_duration_s == 45.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/test_engine_settings.py::test_turn_assembly_settings_defaults -q`
Expected: FAIL (`KeyError: 'engine_assembly_enabled'`).

- [ ] **Step 3: Add the settings**

In `app/config.py`, immediately after the `engine_endpointing_max_delay_s` field, add:

```python
    # ── Turn assembly — merge fragmented answers before the brain ──
    # See docs/superpowers/specs/2026-06-10-turn-assembly-design.md. The
    # assembler buffers consecutive committed fragments of one spoken answer and
    # flushes one merged turn, using VAD user_state_changed as the "resumed"
    # signal so it adds near-zero latency on clean turns.
    engine_assembly_enabled: bool = True          # kill switch (pass-through when False)
    engine_assembly_grace_s: float = 0.5          # wait after a fragment (no VAD resume) before flushing
    engine_assembly_max_duration_s: float = 45.0  # safety force-flush ceiling for one assembled turn
```

In `app/ai/config.py`, immediately after the `engine_endpointing_max_delay_s` property, add:

```python
    @property
    def engine_assembly_enabled(self) -> bool:
        return self._settings.engine_assembly_enabled

    @property
    def engine_assembly_grace_s(self) -> float:
        return self._settings.engine_assembly_grace_s

    @property
    def engine_assembly_max_duration_s(self) -> float:
        return self._settings.engine_assembly_max_duration_s
```

In `.env.example`, append to the "Interview engine — turn handling" block:

```bash
# Turn assembly — merge fragmented answers before the brain (code defaults shown).
# ENGINE_ASSEMBLY_ENABLED=true
# ENGINE_ASSEMBLY_GRACE_S=0.5
# ENGINE_ASSEMBLY_MAX_DURATION_S=45.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/test_engine_settings.py -q`
Expected: the two new tests PASS (the pre-existing `test_settings_have_sarvam_fields` failure is unrelated — `interview_tts_pace` default mismatch; ignore it).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/.env.example backend/nexus/tests/test_engine_settings.py
git commit -m "feat(engine): add turn-assembly config knobs"
```

---

## Task 2: `AssembledTurn` value object + migrate `CommittedTurnSource`

**Files:**
- Modify: `app/modules/interview_engine/turn_source.py`
- Test: `tests/interview_engine_v3/test_turn_source.py`

The source's queued type changes from `str` to `AssembledTurn` so the merged span + `suppress_bridge` hint travel with the text.

- [ ] **Step 1: Update the failing tests**

Replace the body of `tests/interview_engine_v3/test_turn_source.py` with (note the new `_turn()` helper and the `AssembledTurn` import):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_source.py -q`
Expected: FAIL (`ImportError: cannot import name 'AssembledTurn'`).

- [ ] **Step 3: Implement the migration**

In `app/modules/interview_engine/turn_source.py`, add the dataclass at the top (after the module docstring + `import asyncio`) and change the queue type + `submit` signature + drop guard:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.modules.interview_runtime.evidence import TimeSpan


@dataclass(frozen=True)
class AssembledTurn:
    """One logical candidate turn after fragment assembly — the unit the drive
    loop consumes. `text` is the merged answer; `span` covers all merged
    fragments; `suppress_bridge` is set on a merge-back re-flush (an ack already
    played); `is_reflush` is audit-only."""
    text: str
    span: TimeSpan
    suppress_bridge: bool = False
    is_reflush: bool = False


class CommittedTurnSource:
    # ... docstring unchanged ...

    def __init__(self) -> None:
        # The None sentinel (pushed by close()) is the only non-AssembledTurn item.
        self._queue: asyncio.Queue[AssembledTurn | None] = asyncio.Queue()
        self._closed: bool = False

    def submit(self, turn: AssembledTurn | None) -> bool:
        """Offer an assembled turn to the drive loop. Returns False if the source
        is closed, or the turn is None / has empty-or-whitespace text."""
        if self._closed:
            return False
        if turn is None or not turn.text.strip():
            return False
        self._queue.put_nowait(turn)
        return True

    async def get(self) -> AssembledTurn | None:
        return await self._queue.get()

    # close() unchanged
```

Keep the existing `close()` method as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_source.py -q`
Expected: PASS (all).

> Note: `agent.py` and `driver.py` still pass/consume `str` at this point — they are rewired in Tasks 8–9. No test exercises that path, so the suite stays green; full-engine boot is restored at Task 9.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/turn_source.py backend/nexus/tests/interview_engine_v3/test_turn_source.py
git commit -m "feat(engine): carry AssembledTurn on the committed-turn queue"
```

---

## Task 3: `TimerScheduler` protocol + asyncio implementation

**Files:**
- Create: `app/modules/interview_engine/turn_assembler.py`
- Test: `tests/interview_engine_v3/test_turn_assembler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/interview_engine_v3/test_turn_assembler.py`:

```python
"""Tests for the TurnAssembler (livekit-free fragment assembly)."""
from __future__ import annotations

import asyncio

import pytest

from app.modules.interview_engine.turn_assembler import (
    AsyncioTimerScheduler,
    FakeTimerScheduler,
)


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
    assert fired == []          # not auto-fired
    sched.fire_all()
    assert fired == ["a"]


def test_fake_scheduler_cancel_prevents_fire() -> None:
    sched = FakeTimerScheduler()
    fired = []
    h = sched.schedule(0.5, lambda: fired.append("a"))
    h.cancel()
    sched.fire_all()
    assert fired == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -q`
Expected: FAIL (`ModuleNotFoundError: turn_assembler`).

- [ ] **Step 3: Implement the schedulers**

Create `app/modules/interview_engine/turn_assembler.py`:

```python
"""Gen-3 TurnAssembler — merges fragmented candidate turns before the brain.

Livekit-free + unit-testable: the LiveKit wiring (on_user_turn_completed,
user_state_changed) lives in agent.py and calls this module's plain methods.
A pluggable TimerScheduler keeps the grace-timer logic deterministic in tests.

See docs/superpowers/specs/2026-06-10-turn-assembly-design.md.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Protocol


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


class TimerScheduler(Protocol):
    def schedule(self, delay_s: float, callback: Callable[[], None]) -> TimerHandle: ...


class AsyncioTimerScheduler:
    """Production scheduler — wraps the running loop's call_later (single loop,
    so callbacks run on the same loop as the assembler's mutations: no locks)."""

    def schedule(self, delay_s: float, callback: Callable[[], None]) -> TimerHandle:
        return asyncio.get_event_loop().call_later(delay_s, callback)


class _FakeHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeTimerScheduler:
    """Deterministic test scheduler — timers fire only on fire_all()."""

    def __init__(self) -> None:
        self._pending: list[tuple[_FakeHandle, Callable[[], None]]] = []

    def schedule(self, delay_s: float, callback: Callable[[], None]) -> _FakeHandle:
        handle = _FakeHandle()
        self._pending.append((handle, callback))
        return handle

    def fire_all(self) -> None:
        pending, self._pending = self._pending, []
        for handle, cb in pending:
            if not handle.cancelled:
                cb()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/turn_assembler.py backend/nexus/tests/interview_engine_v3/test_turn_assembler.py
git commit -m "feat(engine): turn-assembler timer scheduler (asyncio + fake)"
```

---

## Task 4: `TurnAssembler` — basic flush (single fragment, grace timer, enabled flag)

**Files:**
- Modify: `app/modules/interview_engine/turn_assembler.py`
- Test: `tests/interview_engine_v3/test_turn_assembler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine_v3/test_turn_assembler.py`:

```python
from app.modules.interview_engine.turn_assembler import TurnAssembler
from app.modules.interview_engine.turn_source import CommittedTurnSource


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
    asm.note_user_stopped()           # candidate not speaking
    asm.submit_fragment("I led the migration.")
    # nothing flushed yet — grace timer pending
    assert sink._queue.qsize() == 0
    timer.fire_all()                  # grace elapses, no resume
    turn = await sink.get()
    assert turn.text == "I led the migration."
    assert turn.suppress_bridge is False


async def test_disabled_passthrough_flushes_immediately() -> None:
    asm, sink, clock, timer = _make(enabled=False)
    asm.submit_fragment("hello")
    turn = await sink.get()           # straight through, no timer
    assert turn.text == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -q`
Expected: FAIL (`TypeError: TurnAssembler(...)` / no such class).

- [ ] **Step 3: Implement the basic state machine**

Append to `app/modules/interview_engine/turn_assembler.py` (imports `AssembledTurn`, `CommittedTurnSource`, `TimeSpan`, `structlog` at top of file):

```python
import structlog
from app.modules.interview_engine.turn_source import AssembledTurn, CommittedTurnSource
from app.modules.interview_runtime.evidence import TimeSpan

_log = structlog.get_logger("interview_engine.assembler")

_IDLE, _BUFFERING, _IN_FLIGHT = "idle", "buffering", "in_flight"


class TurnAssembler:
    def __init__(
        self,
        *,
        sink: CommittedTurnSource,
        clock: Callable[[], float],
        timer: TimerScheduler,
        grace_s: float,
        max_duration_s: float,
        enabled: bool = True,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._timer = timer
        self._grace_s = grace_s
        self._max_s = max_duration_s
        self._enabled = enabled

        self._state = _IDLE
        self._buffer: list[str] = []
        self._first_at = 0.0
        self._last_at = 0.0
        self._user_speaking = False
        self._superseded = False
        self._is_reflush = False
        self._grace_handle: TimerHandle | None = None
        # retained copy of the last-flushed turn (for merge-back; Task 6)
        self._retained: list[str] = []
        self._retained_first_at = 0.0

    # --- internal helpers ---
    def _cancel_grace(self) -> None:
        if self._grace_handle is not None:
            self._grace_handle.cancel()
            self._grace_handle = None

    def _arm_grace(self) -> None:
        self._cancel_grace()
        if not self._user_speaking:
            self._grace_handle = self._timer.schedule(self._grace_s, self._on_grace_elapsed)

    def _on_grace_elapsed(self) -> None:
        self._grace_handle = None
        if self._state == _BUFFERING:
            self._flush(reason="grace")

    def _flush(self, *, reason: str) -> None:
        self._cancel_grace()
        text = " ".join(self._buffer).strip()
        turn = AssembledTurn(
            text=text,
            span=TimeSpan(start_ms=int(self._first_at * 1000), end_ms=int(self._last_at * 1000)),
            suppress_bridge=self._is_reflush,
            is_reflush=self._is_reflush,
        )
        _log.info("engine.assembly.flushed", fragment_count=len(self._buffer),
                  merged_len=len(text), reason=reason, is_reflush=self._is_reflush)
        self._sink.submit(turn)
        # retain for possible merge-back; move to IN_FLIGHT
        self._retained = list(self._buffer)
        self._retained_first_at = self._first_at
        self._buffer = []
        self._superseded = False
        self._state = _IN_FLIGHT

    # --- public API ---
    def submit_fragment(self, text: str) -> None:
        if not self._enabled:
            now = self._clock()
            self._sink.submit(AssembledTurn(
                text=text, span=TimeSpan(start_ms=int(now * 1000), end_ms=int(now * 1000)),
                suppress_bridge=False, is_reflush=False))
            return
        now = self._clock()
        if self._state == _IDLE:
            self._buffer = [text]
            self._first_at = now
            self._is_reflush = False
        else:  # _BUFFERING (or _IN_FLIGHT handled in Task 6)
            self._buffer.append(text)
        self._last_at = now
        self._state = _BUFFERING
        _log.info("engine.assembly.fragment_buffered", buffer_count=len(self._buffer))
        self._arm_grace()

    def note_user_speaking(self) -> None:
        self._user_speaking = True
        if self._state == _BUFFERING:
            self._cancel_grace()

    def note_user_stopped(self) -> None:
        self._user_speaking = False
        if self._state == _BUFFERING:
            self._arm_grace()

    def close(self) -> None:
        if self._state == _BUFFERING and self._buffer:
            self._flush(reason="close")
        self._sink.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -q`
Expected: PASS (all so far).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/turn_assembler.py backend/nexus/tests/interview_engine_v3/test_turn_assembler.py
git commit -m "feat(engine): turn-assembler basic flush + grace + disabled passthrough"
```

---

## Task 5: `TurnAssembler` — multi-fragment merge, VAD cancel/restart, max-duration

**Files:**
- Modify: `app/modules/interview_engine/turn_assembler.py`
- Test: `tests/interview_engine_v3/test_turn_assembler.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
async def test_two_fragments_within_grace_merge() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("First, I assessed the tenant health,")
    asm.submit_fragment("then I built a pilot ring.")
    assert sink._queue.qsize() == 0          # still buffering (2nd commit reset grace)
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "First, I assessed the tenant health, then I built a pilot ring."


async def test_vad_speaking_holds_then_resume_merges() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("Compliance policy,")     # grace armed
    asm.note_user_speaking()                       # candidate resumed → grace cancelled
    timer.fire_all()                               # any stale timer must NOT flush
    assert sink._queue.qsize() == 0
    asm.note_user_stopped()                        # they paused again → re-arm
    asm.submit_fragment("configuration profiles.")
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "Compliance policy, configuration profiles."


async def test_max_duration_force_flush() -> None:
    asm, sink, clock, timer = _make(max_s=10.0)
    asm.note_user_stopped()
    clock.t = 100.0
    asm.submit_fragment("start")
    clock.t = 111.0                                # 11s elapsed > max 10s
    asm.submit_fragment("continued")               # crossing the cap force-flushes
    turn = await sink.get()
    assert turn.text == "start continued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -k "merge or vad or max_duration" -q`
Expected: FAIL (`test_max_duration_force_flush` — no cap yet; `test_vad_speaking` may flush a stale timer).

- [ ] **Step 3: Implement max-duration + harden the grace guard**

In `turn_assembler.py`, update `submit_fragment` so that appending a fragment force-flushes when the buffer has spanned `max_s`, and ensure `_on_grace_elapsed` only flushes when still buffering (already guarded). Replace the `else:  # _BUFFERING` branch and the trailing lines of `submit_fragment` with:

```python
        else:  # _BUFFERING
            self._buffer.append(text)
            self._last_at = now
            if (now - self._first_at) >= self._max_s:
                self._state = _BUFFERING
                self._flush(reason="max_duration")
                return
        self._last_at = now
        self._state = _BUFFERING
        _log.info("engine.assembly.fragment_buffered", buffer_count=len(self._buffer))
        self._arm_grace()
```

(The `_IDLE` branch above it is unchanged: it sets `self._buffer = [text]; self._first_at = now`.)

The VAD guard already holds: `_on_grace_elapsed` flushes only `if self._state == _BUFFERING`, and `note_user_speaking` cancels the grace handle, so a fired stale timer is a no-op.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/turn_assembler.py backend/nexus/tests/interview_engine_v3/test_turn_assembler.py
git commit -m "feat(engine): turn-assembler merge + VAD-aware grace + max-duration cap"
```

---

## Task 6: `TurnAssembler` — in-flight supersession & merge-back

**Files:**
- Modify: `app/modules/interview_engine/turn_assembler.py`
- Test: `tests/interview_engine_v3/test_turn_assembler.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
async def test_in_flight_not_superseded_confirm_commits() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("done answer")
    timer.fire_all()
    await sink.get()                       # flushed → IN_FLIGHT
    assert asm.is_superseded() is False
    asm.confirm_committed()                # loop passed the checkpoint
    # back to IDLE — a new fragment starts a fresh turn
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
    first = await sink.get()               # flushed part one → IN_FLIGHT
    assert first.text == "part one"

    # candidate resumes while the brain is mid-flight, then a continuation commits
    asm.note_user_speaking()
    assert asm.is_superseded() is True     # loop will abort at the checkpoint
    asm.note_user_stopped()
    asm.submit_fragment("part two")        # continuation
    timer.fire_all()
    merged = await sink.get()
    assert merged.text == "part one part two"
    assert merged.suppress_bridge is True  # ack already played on the first flush
    assert merged.is_reflush is True


async def test_false_resume_reflushes_same_text() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("only answer")
    timer.fire_all()
    await sink.get()                       # IN_FLIGHT
    asm.note_user_speaking()               # VAD blip, no words follow
    asm.note_user_stopped()                # ... and they stop with no new commit
    timer.fire_all()
    reflush = await sink.get()
    assert reflush.text == "only answer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -k "in_flight or merge_back or false_resume" -q`
Expected: FAIL (`AttributeError: 'TurnAssembler' object has no attribute 'is_superseded'`).

- [ ] **Step 3: Implement supersession + merge-back**

In `turn_assembler.py`:

(a) Add the two loop-facing methods:

```python
    def is_superseded(self) -> bool:
        """Loop checkpoint: has a continuation arrived for the turn the loop is
        processing? Set True by `_begin_merge_back` (which also moves the state to
        BUFFERING), reset to False on the next flush — so it is NOT gated on the
        _IN_FLIGHT state (the merge-back has already left it)."""
        return self._superseded

    def confirm_committed(self) -> None:
        """Loop passed the point-of-no-return for the in-flight turn (not
        superseded): discard the retained buffer and accept the next turn fresh."""
        if self._state == _IN_FLIGHT:
            self._retained = []
            self._is_reflush = False
            self._superseded = False
            self._state = _IDLE
```

(b) Make `note_user_speaking` set supersession while in flight:

```python
    def note_user_speaking(self) -> None:
        self._user_speaking = True
        if self._state == _BUFFERING:
            self._cancel_grace()
        elif self._state == _IN_FLIGHT:
            self._begin_merge_back()
```

(c) Handle a continuation commit while in flight, and add `_begin_merge_back`:

```python
    def _begin_merge_back(self) -> None:
        """A continuation appeared for the in-flight turn → re-buffer the retained
        text (so a re-flush is the merged answer) and flag superseded for the loop."""
        self._superseded = True
        self._buffer = list(self._retained)
        self._first_at = self._retained_first_at
        self._is_reflush = True
        self._state = _BUFFERING
        self._arm_grace()
```

(d) In `submit_fragment`, handle the `_IN_FLIGHT` state at the top (before the IDLE/BUFFERING branches):

```python
        if self._state == _IN_FLIGHT:
            # continuation commit for the in-flight turn → merge back
            if not self._superseded:
                self._begin_merge_back()
            self._buffer.append(text)
            self._last_at = now
            if (now - self._first_at) >= self._max_s:
                self._flush(reason="max_duration")
                return
            self._arm_grace()
            return
```

> Note on the false-resume path: `_begin_merge_back` re-buffers the retained text and arms the grace timer; if no continuation commits, the grace timer re-flushes the same text once (`is_reflush=True`). `confirm_committed` is only called by the loop on the NON-superseded path, so a superseded in-flight turn that the loop aborts leaves us correctly in BUFFERING.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_turn_assembler.py -q`
Expected: PASS (all assembler tests).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/turn_assembler.py backend/nexus/tests/interview_engine_v3/test_turn_assembler.py
git commit -m "feat(engine): turn-assembler in-flight supersession + merge-back"
```

---

## Task 7: `loop.py` — supersession checkpoint + suppress_bridge + ABORTED

**Files:**
- Modify: `app/modules/interview_engine/loop.py`
- Test: `tests/interview_engine_v3/test_loop.py`

- [ ] **Step 1: Write the failing tests**

`test_loop.py` already constructs a `TurnContext` and calls
`run_turn(ctx, brain=, mouth=, voice=, notelog=NoteLog())` in its existing tests
(see the calls around the file's `run_turn(...)` lines). For these new tests:
copy that existing `TurnContext` construction verbatim, then (a) add the two new
fields `supersession_check=...` and `suppress_bridge=...`, (b) swap `NoteLog()`
for the `_RecordingNoteLog` below so you can assert `append_count`, and (c) make
the fake mouth count its `bridge`/`real_line` calls. Append:

```python
from app.modules.interview_engine.loop import ABORTED, run_turn, TurnContext


async def test_checkpoint_aborts_before_notes_when_superseded() -> None:
    """When supersession_check returns True at the checkpoint, no notes are
    appended and no real line is spoken; run_turn returns ABORTED."""
    # Build a context whose supersession_check is True. Reuse the file's fakes:
    brain = _make_fake_brain_with_observation()   # returns a decision with 1 observation
    mouth = _make_fake_mouth()
    voice = FakeVoice()
    notelog = _RecordingNoteLog()                 # counts append() calls
    ctx = _make_turn_context(supersession_check=lambda: True, suppress_bridge=False)

    result = await run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)

    assert result is ABORTED
    assert notelog.append_count == 0              # no durable evidence
    assert mouth.real_line_calls == 0             # no real line rendered
    # the bridge MAY have played (content-free) — that is acceptable


async def test_checkpoint_proceeds_when_not_superseded() -> None:
    brain = _make_fake_brain_with_observation()
    mouth = _make_fake_mouth()
    voice = FakeVoice()
    notelog = _RecordingNoteLog()
    ctx = _make_turn_context(supersession_check=lambda: False, suppress_bridge=False)

    result = await run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)

    assert result is not ABORTED
    assert notelog.append_count == 1
    assert mouth.real_line_calls == 1


async def test_suppress_bridge_skips_bridge_call() -> None:
    brain = _make_fake_brain_with_observation()
    mouth = _make_fake_mouth()
    voice = FakeVoice()
    notelog = _RecordingNoteLog()
    ctx = _make_turn_context(supersession_check=lambda: False, suppress_bridge=True)

    await run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)

    assert mouth.bridge_calls == 0                 # bridge skipped on a re-flush
    assert mouth.real_line_calls == 1
```

Add small helpers near the top of `test_loop.py` if not already present:

```python
class _RecordingNoteLog:
    def __init__(self) -> None:
        self.append_count = 0

    def append(self, *args, **kwargs) -> None:
        self.append_count += 1
```

Extend the file's fake mouth to count `bridge_calls` / `real_line_calls`, and add a `_make_turn_context(*, supersession_check, suppress_bridge)` helper that fills the existing `TurnContext` required fields (turn_ref, utterance, utterance_span, from_question_id, via_probe, brain_input, bridge_request, recent_openers) plus the two new fields.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_loop.py -k "checkpoint or suppress_bridge" -q`
Expected: FAIL (`ImportError: cannot import name 'ABORTED'`).

- [ ] **Step 3: Implement the checkpoint + suppress_bridge + ABORTED**

In `app/modules/interview_engine/loop.py`:

(a) Add the sentinel near the top (after `CANNED_BRIDGE_FALLBACK`):

```python
#: Returned by run_turn when a continuation superseded this turn at the
#: pre-commit checkpoint — the driver discards the turn (no notes, no real line).
ABORTED: object = object()
```

(b) Add two fields to `TurnContext`:

```python
    supersession_check: Callable[[], bool] | None = None
    suppress_bridge: bool = False
```

(import `Callable` from `typing` at the top if not already imported).

(c) In `run_turn`, gate the bridge and add the checkpoint. Update the body so:
- the bridge task is only created/awaited when `not ctx.suppress_bridge` (otherwise `bridge_text = None` and no `voice.say(bridge)`);
- immediately AFTER `decision = await brain_task` and BEFORE the `for obs in decision.observations: notelog.append(...)` loop, insert:

```python
        # §7 merge-back checkpoint — the point-of-no-return is the note commit.
        if ctx.supersession_check is not None and ctx.supersession_check():
            return ABORTED
```

Adjust the return type annotation to `-> BrainDecision | object` and ensure the bridge-skip path passes `just_said=None` into `MouthTurnInput`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_loop.py -q`
Expected: PASS (existing loop tests + the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/loop.py backend/nexus/tests/interview_engine_v3/test_loop.py
git commit -m "feat(engine): loop merge-back checkpoint + suppress_bridge + ABORTED"
```

---

## Task 8: `driver.py` — consume `AssembledTurn`, handle ABORTED, confirm_committed

**Files:**
- Modify: `app/modules/interview_engine/driver.py`
- Test: `tests/interview_engine_v3/test_driver.py`

The driver's `handle_turn` currently takes `utterance: str` + `span`. Change it to take an `AssembledTurn` and an injected `confirm_committed`/`is_superseded` pair, wire the checkpoint into the `TurnContext`, and unwind on ABORTED.

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine_v3/test_driver.py` (reuse `_make_session_config`, `_FakeMouth`, `_FakeVoice`):

```python
from app.modules.interview_engine.turn_source import AssembledTurn
from app.modules.interview_runtime.evidence import TimeSpan


def _aturn(text: str, *, suppress_bridge: bool = False) -> AssembledTurn:
    return AssembledTurn(text=text, span=TimeSpan(start_ms=0, end_ms=10),
                         suppress_bridge=suppress_bridge, is_reflush=False)


async def test_handle_turn_aborted_unwinds_transcript_and_no_advance() -> None:
    # Brain returns a normal decision, but the supersession check is True →
    # run_turn returns ABORTED → driver pops the candidate transcript turn and
    # does NOT advance / record notes.
    config = _make_session_config()
    driver = build_session_driver(
        config, voice=_FakeVoice(),
        persist=_noop_persist, started_at=_NOW,
    )
    await driver.opener()
    transcript_len_before = len(driver._transcript)   # includes intro+opener agent turns
    driver._set_superseded(True)                      # test hook (see impl)

    is_terminal = await driver.handle_turn(turn=_aturn("partial answer"), turn_ref="t-1")

    assert is_terminal is False
    # the candidate transcript turn appended at entry was popped on abort:
    assert len(driver._transcript) == transcript_len_before
    assert len(driver._notelog) == 0


async def test_handle_turn_confirms_committed_on_success() -> None:
    config = _make_session_config()
    confirmed = []
    driver = build_session_driver(
        config, voice=_FakeVoice(),
        persist=_noop_persist, started_at=_NOW,
        on_committed=lambda: confirmed.append(True),   # injected confirm hook
    )
    await driver.opener()
    await driver.handle_turn(turn=_aturn("a complete answer"), turn_ref="t-1")
    assert confirmed == [True]
```

(Define `_NOW = datetime(2026, 6, 10, tzinfo=UTC)` and `async def _noop_persist(ev): pass` near the top of the test file if not present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_driver.py -k "aborted or confirms_committed" -q`
Expected: FAIL (`handle_turn() got an unexpected keyword 'turn'`).

- [ ] **Step 3: Implement the driver changes**

In `app/modules/interview_engine/driver.py`:

(a) Add constructor params + state for the supersession hooks. In `SessionDriver.__init__`, add `is_superseded: Callable[[], bool] | None = None` and `on_committed: Callable[[], None] | None = None`, store them, and a test hook:

```python
        self._is_superseded_cb = is_superseded
        self._on_committed_cb = on_committed
        self._forced_superseded = False  # test hook

    def _set_superseded(self, value: bool) -> None:  # test hook only
        self._forced_superseded = value

    def _superseded(self) -> bool:
        if self._forced_superseded:
            return True
        return bool(self._is_superseded_cb and self._is_superseded_cb())
```

(b) Change `handle_turn` signature to `async def handle_turn(self, *, turn: AssembledTurn, turn_ref: str, pre_turn_gap_ms: int = 0, words: list | None = None) -> bool:` and derive `utterance = turn.text` and `span = turn.span` at the top (replacing the old `utterance`/`span` params). Pass `supersession_check=self._superseded` and `suppress_bridge=turn.suppress_bridge` into the `TurnContext`.

(c) Record the index of the candidate transcript turn appended at entry; after `run_turn`, branch on ABORTED:

```python
        from app.modules.interview_engine.loop import ABORTED
        decision = await run_turn(ctx, brain=self._brain_adapter,
                                  mouth=self._mouth_combined, voice=capturing,
                                  notelog=self._notelog)
        if decision is ABORTED:
            # Unwind: pop the candidate transcript turn appended at entry; the
            # assembler will re-flush the merged turn. No notes were committed.
            self._transcript.pop()                  # the candidate turn from step 1
            _log.info("engine.driver.turn_aborted_merge_back", turn_ref=turn_ref)
            return False
        if self._on_committed_cb is not None:
            self._on_committed_cb()
```

Place the `_on_committed_cb()` call right after the ABORTED branch (the point-of-no-return passed). Keep the rest of `handle_turn` (agent-turn recording, state advance) unchanged.

(d) Update `build_session_driver` to accept + forward `is_superseded` and `on_committed`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_driver.py -q`
Expected: PASS. Fix any existing driver tests that call `handle_turn(utterance=..., span=...)` to use `turn=_aturn(...)`.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/driver.py backend/nexus/tests/interview_engine_v3/test_driver.py
git commit -m "feat(engine): driver consumes AssembledTurn + merge-back unwind"
```

---

## Task 9: `agent.py` — construct assembler, wire LiveKit events

**Files:**
- Modify: `app/modules/interview_engine/agent.py`
- Test: manual (livekit) + `tests/interview_engine_v3/test_engine_imports.py` (import smoke)

- [ ] **Step 1: Verify the import smoke test still covers agent.py**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_engine_imports.py -q`
Expected: PASS now (baseline before changes).

- [ ] **Step 2: Construct the assembler and wire it**

In `app/modules/interview_engine/agent.py`:

(a) Import at top:
```python
from app.modules.interview_engine.turn_assembler import AsyncioTimerScheduler, TurnAssembler
```

(b) In `run()`, after `turn_source = CommittedTurnSource()`, build the assembler:
```python
    import time as _time
    assembler = TurnAssembler(
        sink=turn_source,
        clock=_time.monotonic,
        timer=AsyncioTimerScheduler(),
        grace_s=ai_config.engine_assembly_grace_s,
        max_duration_s=ai_config.engine_assembly_max_duration_s,
        enabled=ai_config.engine_assembly_enabled,
    )
```

(c) Change `_EngineAgent` to submit fragments to the assembler instead of the source. Give `_EngineAgent` an `assembler` instead of `turn_source`; in `on_user_turn_completed`:
```python
        text = (getattr(new_message, "text_content", "") or "").strip()
        if text:
            self._assembler.submit_fragment(text)
        log.info("engine.turn.fragment", transcript_len=len(text))
        raise StopResponse()
```
Construct it as `_EngineAgent(assembler=assembler, instructions="")`.

(d) Register the VAD state listener on the session (after `session.start(...)`):
```python
    from livekit.agents.voice.events import UserStateChangedEvent  # local import

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:  # noqa: ANN001
        if ev.new_state == "speaking":
            assembler.note_user_speaking()
        elif ev.new_state == "listening":
            assembler.note_user_stopped()
```

(e) Pass the assembler's hooks into `_drive` so the driver gets the checkpoint. In `build_session_driver(...)` call inside `_drive`, forward:
```python
        is_superseded=assembler.is_superseded,
        on_committed=assembler.confirm_committed,
```
and thread `assembler` into `_drive(...)`'s parameters. Update `on_commit` / `_consume_turns` to call `driver.handle_turn(turn=utterance, turn_ref=turn_ref)` (now `utterance` is an `AssembledTurn` from the source). Remove the manual `span = TimeSpan(...)` synthesis (the span now rides on the AssembledTurn). On candidate disconnect / inactivity / close, call `assembler.close()` (which flushes + closes the source) instead of `turn_source.close()`.

(f) Reset the inactivity activity timestamp in `on_commit` as today (each assembled turn is activity); fragments mid-buffer also count — optionally call a lightweight activity bump from `submit_fragment` wiring (acceptable to leave as assembled-turn-only for v1; note it).

- [ ] **Step 3: Run the import + boot smoke tests**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3/test_engine_imports.py tests/interview_engine_v3/test_engine_boot.py -q`
Expected: PASS.

- [ ] **Step 4: Manual talk-test (the standing validation rule)**

```bash
docker compose up -d --force-recreate nexus-engine
```
Start a candidate session; give an answer with a deliberate ~2s mid-sentence pause. Confirm in the engine logs (`docker compose logs -f nexus-engine`) `engine.assembly.flushed fragment_count=2 ...` (one merged turn, not two), and that a clean answer still flushes promptly (`fragment_count=1`).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "feat(engine): wire TurnAssembler into the live agent (VAD + hook)"
```

---

## Task 10: Docs — correct AGENT_ARCHITECTURE §11 + CLAUDE.md

**Files:**
- Modify: `app/modules/interview_engine/AGENT_ARCHITECTURE.md`
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 1: Fix AGENT_ARCHITECTURE.md**

In §11 ("The full per-turn timeline"), replace the inaccurate barge-in claim ("VAD-mode interruption cancels the in-flight `run_turn`...") with the truth: turns are processed sequentially; `run_turn` is not cancelled mid-flight; barge-in only sets `last_interrupted`. Add a short subsection documenting the `TurnAssembler` (LiveKit → assembler → `CommittedTurnSource` → drive loop), the VAD-driven grace, and the merge-back checkpoint at the note-commit point-of-no-return.

- [ ] **Step 2: Fix CLAUDE.md file map**

In `backend/nexus/CLAUDE.md`, update the `interview_engine/` module-tree line to include `turn_assembler.py (TurnAssembler — fragment assembly before the brain)` and `turn_source.py (CommittedTurnSource + AssembledTurn)`, and note in the 3D.engine bullet that committed turns flow LiveKit hook → `TurnAssembler` → `CommittedTurnSource` → drive loop, with VAD-driven assembly + merge-back checkpoint.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/AGENT_ARCHITECTURE.md backend/nexus/CLAUDE.md
git commit -m "docs(engine): document TurnAssembler + correct barge-in claim"
```

---

## Final verification

- [ ] Run the full gen-3 engine suite:

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3 tests/test_engine_settings.py -m "not prompt_quality" -q`
Expected: all PASS (the pre-existing `test_settings_have_sarvam_fields` failure in `tests/test_engine_settings.py` is unrelated — leave it).

- [ ] Confirm the assembler is livekit-free:

Run: `docker compose exec -T nexus python -c "import ast,sys; src=open('app/modules/interview_engine/turn_assembler.py').read(); assert 'livekit' not in src; print('OK: livekit-free')"`
Expected: `OK: livekit-free`.

- [ ] Manual talk-test passed (Task 9 Step 4): a mid-answer pause assembles into one turn; clean turns stay responsive.
