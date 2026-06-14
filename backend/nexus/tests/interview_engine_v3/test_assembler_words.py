"""Tests for word-timing plumbing through the TurnAssembler.

The candidate's STT per-word timings (RawWord tuples on a continuous STT stream
clock) are buffered per-fragment alongside the text buffer and merged on flush via
``relative_words`` (rebased on the first word so first word start = 0). These tests
drive the assembler directly (FakeTimerScheduler + CommittedTurnSource) and assert
the flushed ``AssembledTurn.words`` is correctly merged + turn-relative.
"""
from __future__ import annotations

from app.modules.interview_engine.turn_assembler import (
    FakeTimerScheduler,
    TurnAssembler,
)
from app.modules.interview_engine.turn_source import CommittedTurnSource
from app.modules.interview_runtime.models import WordTiming


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


def _monotonic(words: list[WordTiming]) -> bool:
    return all(
        words[i].start_ms <= words[i + 1].start_ms for i in range(len(words) - 1)
    )


async def test_single_fragment_words_relative_from_zero() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    # Two words at stream-clock 5.0s / 5.4s (not zero) — must rebase to 0.
    raw = [("I", 5.0, 5.2, 0.99), ("led", 5.4, 5.7, 0.98)]
    asm.submit_fragment("I led", words=raw)
    timer.fire_all()
    turn = await sink.get()

    assert len(turn.words) == 2
    assert all(isinstance(w, WordTiming) for w in turn.words)
    assert turn.words[0].start_ms == 0
    assert turn.words[0].text == "I"
    assert _monotonic(turn.words)


async def test_two_fragments_merge_words_turn_relative() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    # Continuous stream clock across two committed fragments of ONE logical turn.
    frag1 = [("I", 5.0, 5.2, 0.99), ("led", 5.3, 5.6, 0.98)]
    frag2 = [("the", 6.0, 6.1, 0.97), ("migration", 6.2, 6.8, 0.96)]
    asm.submit_fragment("I led", words=frag1)
    asm.submit_fragment("the migration", words=frag2)
    timer.fire_all()
    turn = await sink.get()

    assert turn.text == "I led the migration"
    assert len(turn.words) == 4
    # Rebased on the FIRST word of the whole turn (5.0s -> 0).
    assert turn.words[0].start_ms == 0
    assert turn.words[0].text == "I"
    assert turn.words[-1].text == "migration"
    # The boundary word ("the" at 6.0s) is 1000ms after the turn start.
    assert turn.words[2].start_ms == 1000
    assert _monotonic(turn.words)


async def test_merge_back_re_flush_carries_merged_words() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    frag1 = [("I", 5.0, 5.2, 0.99), ("led", 5.3, 5.6, 0.98)]
    asm.submit_fragment("I led", words=frag1)
    timer.fire_all()
    first = await sink.get()
    assert len(first.words) == 2

    # A continuation arrives during the IN_FLIGHT window → merge-back re-buffers
    # the retained words; the re-flush must carry the merged words of BOTH frags.
    frag2 = [("the", 6.0, 6.1, 0.97), ("migration", 6.2, 6.8, 0.96)]
    asm.submit_fragment("the migration", words=frag2)
    timer.fire_all()
    reflush = await sink.get()

    assert reflush.is_reflush is True
    assert reflush.text == "I led the migration"
    assert len(reflush.words) == 4
    assert reflush.words[0].start_ms == 0
    assert reflush.words[0].text == "I"
    assert reflush.words[-1].text == "migration"
    assert _monotonic(reflush.words)


async def test_empty_or_none_words_no_crash() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    asm.submit_fragment("I led", words=None)
    timer.fire_all()
    turn = await sink.get()
    assert turn.text == "I led"
    assert turn.words == []


async def test_disabled_passthrough_relative_words() -> None:
    asm, sink, clock, timer = _make(enabled=False)
    raw = [("hello", 3.0, 3.3, 0.99), ("there", 3.4, 3.7, 0.98)]
    asm.submit_fragment("hello there", words=raw)
    turn = await sink.get()
    assert turn.text == "hello there"
    assert len(turn.words) == 2
    assert turn.words[0].start_ms == 0
    assert _monotonic(turn.words)


# --- span-from-words (reel clip sync) ---------------------------------------
# The assembler CLOCK fires at COMMIT time (after the answer ends). The reel
# anchors clips on ``turn.span.start_ms`` — so the span MUST come from the real
# absolute STT word times, not the clock. _Clock starts at 100.0s while the words
# below start at 10.0s, so a word-derived span (10000) is provably NOT the clock.


async def test_single_fragment_span_from_absolute_word_times() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    # Clock is at 100.0s; words are at absolute stream-clock 10.0s..10.9s.
    raw = [("I", 10.0, 10.2, 0.99), ("led", 10.4, 10.9, 0.98)]
    asm.submit_fragment("I led", words=raw)
    timer.fire_all()
    turn = await sink.get()

    # span derives from the WORDS (first word start -> last word end), NOT the
    # clock (which would have been 100000).
    assert turn.span.start_ms == 10000
    assert turn.span.end_ms == 10900
    assert turn.span.start_ms != int(clock.t * 1000)
    # words stay turn-relative for captions (first word start == 0).
    assert turn.words[0].start_ms == 0
    # span.start + word.rel == absolute word ms (the reel's math invariant).
    assert turn.span.start_ms + turn.words[0].start_ms == 10000


async def test_two_fragment_span_spans_first_to_last_word() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    # Continuous stream clock across two committed fragments of ONE logical turn.
    frag1 = [("I", 10.0, 10.2, 0.99), ("led", 10.3, 10.6, 0.98)]
    frag2 = [("the", 11.0, 11.1, 0.97), ("migration", 11.2, 11.8, 0.96)]
    asm.submit_fragment("I led", words=frag1)
    asm.submit_fragment("the migration", words=frag2)
    timer.fire_all()
    turn = await sink.get()

    # first fragment's first word start (10.0s) -> last fragment's last word end (11.8s).
    assert turn.span.start_ms == 10000
    assert turn.span.end_ms == 11800
    assert turn.span.start_ms != int(clock.t * 1000)


async def test_no_words_turn_span_falls_back_to_clock() -> None:
    asm, sink, clock, timer = _make()
    asm.note_user_stopped()
    # No STT words → the span MUST fall back to the assembler clock (commit time).
    clock.t = 42.0
    asm.submit_fragment("I led", words=None)
    timer.fire_all()
    turn = await sink.get()

    assert turn.words == []
    assert turn.span.start_ms == 42000
    assert turn.span.end_ms == 42000


async def test_disabled_path_span_from_absolute_word_times() -> None:
    asm, sink, clock, timer = _make(enabled=False)
    # Clock at 100.0s; words at absolute 3.0s..3.7s — span must come from words.
    raw = [("hello", 3.0, 3.3, 0.99), ("there", 3.4, 3.7, 0.98)]
    asm.submit_fragment("hello there", words=raw)
    turn = await sink.get()

    assert turn.span.start_ms == 3000
    assert turn.span.end_ms == 3700
    assert turn.span.start_ms != int(clock.t * 1000)


async def test_disabled_path_no_words_span_from_clock() -> None:
    asm, sink, clock, timer = _make(enabled=False)
    clock.t = 7.5
    asm.submit_fragment("hello there", words=None)
    turn = await sink.get()

    assert turn.words == []
    assert turn.span.start_ms == 7500
    assert turn.span.end_ms == 7500
