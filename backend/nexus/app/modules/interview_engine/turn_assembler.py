"""Gen-3 TurnAssembler — merges fragmented candidate turns before the brain.

Livekit-free + unit-testable: the LiveKit wiring (on_user_turn_completed,
user_state_changed) lives in agent.py and calls this module's plain methods.
A pluggable TimerScheduler keeps the grace-timer logic deterministic in tests.

The candidate's spoken answer can arrive as several committed "fragments" when
the turn detector ends the turn during a mid-answer pause. This assembler holds
each fragment briefly, merges consecutive ones into one AssembledTurn, and
flushes once the candidate has clearly settled (VAD user_state_changed is the
"resumed" signal). A continuation that arrives just after a flush is merged back:
note_user_speaking / a late commit during the IN_FLIGHT window re-buffers the
retained text and flags `is_superseded()` so the drive loop aborts at its
note-commit checkpoint and re-runs on the merged text.

See docs/superpowers/specs/2026-06-10-turn-assembly-design.md.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Protocol

import structlog

from app.modules.interview_engine.turn_source import AssembledTurn, CommittedTurnSource
from app.modules.interview_runtime.evidence import TimeSpan
from app.modules.interview_runtime.transcript_timing import RawWord, relative_words

_log = structlog.get_logger("interview_engine.assembler")

_IDLE, _BUFFERING, _IN_FLIGHT = "idle", "buffering", "in_flight"


def _span_from_words(
    raw: list[RawWord], *, fallback_start: float, fallback_end: float
) -> TimeSpan:
    """Build the turn's TimeSpan from the absolute STT word times when present.

    ``raw`` are RawWord tuples ``(text, start_s, end_s, conf)`` on the absolute STT
    stream clock. The span is first word start -> last word end (the TRUE speech
    window), so the reel anchors clips on real speech, not the commit-lagged
    assembler clock. With no words, fall back to the clock anchors (in seconds).
    Both bounds are clamped non-negative and ``end_ms >= start_ms``.
    """
    if raw:
        start_ms = max(0, int(raw[0][1] * 1000))
        end_ms = max(0, int(raw[-1][2] * 1000))
    else:
        start_ms = max(0, int(fallback_start * 1000))
        end_ms = max(0, int(fallback_end * 1000))
    return TimeSpan(start_ms=start_ms, end_ms=max(start_ms, end_ms))


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


class TimerScheduler(Protocol):
    def schedule(self, delay_s: float, callback: Callable[[], None]) -> TimerHandle: ...


class AsyncioTimerScheduler:
    """Production scheduler — wraps the running loop's call_later (single loop,
    so callbacks run on the same loop as the assembler's mutations: no locks)."""

    def schedule(self, delay_s: float, callback: Callable[[], None]) -> TimerHandle:
        return asyncio.get_running_loop().call_later(delay_s, callback)


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


class TurnAssembler:
    """Buffers committed fragments and flushes one AssembledTurn per logical
    answer. Single-event-loop: all public methods + timer callbacks run on the
    same asyncio loop, so there are no locks and no data races."""

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
        # Per-fragment RawWord lists, mirroring _buffer 1:1 (one list per buffered
        # fragment). Merged turn-relative on flush via relative_words.
        self._word_buffer: list[list[RawWord]] = []
        self._first_at = 0.0
        self._last_at = 0.0
        self._user_speaking = False
        self._superseded = False
        self._is_reflush = False
        self._grace_handle: TimerHandle | None = None
        self._retained: list[str] = []
        # Mirrors _retained for merge-back re-buffering of word timings.
        self._retained_words: list[list[RawWord]] = []
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
        # Word times across the fragments of ONE turn are on a continuous STT
        # stream clock — concatenate in fragment order and rebase once.
        all_raw = [w for frag in self._word_buffer for w in frag]
        merged_words = relative_words(all_raw)
        # Anchor the span on the REAL absolute STT word times (first word start ->
        # last word end), not the assembler clock — the clock fires at commit time
        # (after the answer ends), so a clock-anchored span lands the reel's clips
        # ~1.5-2.7s late. With no STT words, fall back to the clock (commit) span.
        span = _span_from_words(all_raw, fallback_start=self._first_at, fallback_end=self._last_at)
        turn = AssembledTurn(
            text=text,
            span=span,
            suppress_bridge=self._is_reflush,
            is_reflush=self._is_reflush,
            words=merged_words,
        )
        _log.info("engine.assembly.flushed", fragment_count=len(self._buffer),
                  merged_len=len(text), word_count=len(merged_words),
                  reason=reason, is_reflush=self._is_reflush)
        self._sink.submit(turn)
        # retain for a possible merge-back; move to IN_FLIGHT
        self._retained = list(self._buffer)
        self._retained_words = [list(frag) for frag in self._word_buffer]
        self._retained_first_at = self._first_at
        self._buffer = []
        self._word_buffer = []
        self._superseded = False
        self._state = _IN_FLIGHT

    def _begin_merge_back(self) -> None:
        """A continuation appeared for the in-flight turn → re-buffer the retained
        text (so a re-flush is the merged answer) and flag superseded for the loop."""
        self._superseded = True
        self._buffer = list(self._retained)
        self._word_buffer = [list(frag) for frag in self._retained_words]
        self._first_at = self._retained_first_at
        self._is_reflush = True
        self._state = _BUFFERING
        self._arm_grace()

    # --- public API (called by agent.py wiring / the drive loop) ---
    def submit_fragment(self, text: str, words: list[RawWord] | None = None) -> None:
        frag_words = list(words) if words else []
        if not self._enabled:
            now = self._clock()
            # Same span-from-words rule as the buffered path: a single direct-submit
            # fragment anchors on its words' absolute STT times; with no words it
            # falls back to the clock (now).
            span = _span_from_words(frag_words, fallback_start=now, fallback_end=now)
            self._sink.submit(AssembledTurn(
                text=text,
                span=span,
                suppress_bridge=False, is_reflush=False,
                words=relative_words(frag_words)))
            return
        now = self._clock()
        if self._state == _IN_FLIGHT:
            # continuation commit for the in-flight turn → merge back
            if not self._superseded:
                self._begin_merge_back()
            self._buffer.append(text)
            self._word_buffer.append(frag_words)
            self._last_at = now
            if (now - self._first_at) >= self._max_s:
                self._flush(reason="max_duration")
                return
            self._arm_grace()
            return
        if self._state == _IDLE:
            self._buffer = [text]
            self._word_buffer = [frag_words]
            self._first_at = now
            self._is_reflush = False
        else:  # _BUFFERING
            self._buffer.append(text)
            self._word_buffer.append(frag_words)
            self._last_at = now
            if (now - self._first_at) >= self._max_s:
                self._state = _BUFFERING
                self._flush(reason="max_duration")
                return
        self._last_at = now
        self._state = _BUFFERING
        _log.info("engine.assembly.fragment_buffered", buffer_count=len(self._buffer))
        self._arm_grace()

    def note_user_speaking(self) -> None:
        self._user_speaking = True
        if self._state == _BUFFERING:
            self._cancel_grace()
        elif self._state == _IN_FLIGHT:
            self._begin_merge_back()

    def note_user_stopped(self) -> None:
        self._user_speaking = False
        if self._state == _BUFFERING:
            self._arm_grace()

    def is_superseded(self) -> bool:
        """Loop checkpoint: has a continuation arrived for the turn the loop is
        processing? Set True by _begin_merge_back (which also moves state to
        BUFFERING), reset to False on the next flush — so it is NOT gated on the
        _IN_FLIGHT state."""
        return self._superseded

    def confirm_committed(self) -> None:
        """Loop passed the point-of-no-return for the in-flight turn (not
        superseded): discard the retained buffer and accept the next turn fresh."""
        if self._state == _IN_FLIGHT:
            self._retained = []
            self._is_reflush = False
            self._superseded = False
            self._state = _IDLE

    def close(self) -> None:
        if self._state == _BUFFERING and self._buffer:
            self._flush(reason="close")
        self._sink.close()
