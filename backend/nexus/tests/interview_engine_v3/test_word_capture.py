"""Task 2 — STT word-timing capture (pure helper).

Validates ``words_from_final_transcript`` — the pure extractor that reads a
LiveKit final-transcript ``SpeechEvent`` and returns the ``RawWord`` tuples
(text, start_s, end_s, confidence) consumed by
``interview_runtime.transcript_timing.relative_words``.

Fakes mirror the real livekit-agents 1.5.17 shapes:
  - ``SpeechEvent.alternatives: list[SpeechData]``
  - ``SpeechData.words: list[TimedString] | None`` + ``SpeechData.confidence``
  - ``TimedString`` is a ``str`` subclass carrying ``.start_time`` / ``.end_time``
    (stream-clock seconds; may be a NotGiven sentinel when unavailable).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.interview_engine import agent as agent_mod
from app.modules.interview_engine.agent import words_from_final_transcript


class _FakeWord(str):
    """A str carrying word-level timing, mirroring livekit's ``TimedString``."""

    def __new__(cls, text: str, start_time, end_time):
        obj = super().__new__(cls, text)
        obj.start_time = start_time
        obj.end_time = end_time
        return obj


def _event(alternatives):
    return SimpleNamespace(alternatives=alternatives)


def _alt(words, confidence):
    return SimpleNamespace(words=words, confidence=confidence)


def test_extracts_word_tuples_from_first_alternative() -> None:
    ev = _event(
        [
            _alt(
                words=[
                    _FakeWord("hello", 1.0, 1.4),
                    _FakeWord("world", 1.5, 2.0),
                ],
                confidence=0.9,
            )
        ]
    )

    assert words_from_final_transcript(ev) == [
        ("hello", 1.0, 1.4, 0.9),
        ("world", 1.5, 2.0, 0.9),
    ]


def test_empty_words_yields_empty_list() -> None:
    ev = _event([_alt(words=[], confidence=0.9)])
    assert words_from_final_transcript(ev) == []


def test_none_words_yields_empty_list() -> None:
    ev = _event([_alt(words=None, confidence=0.9)])
    assert words_from_final_transcript(ev) == []


def test_no_alternatives_yields_empty_list() -> None:
    assert words_from_final_transcript(_event([])) == []


def test_missing_confidence_defaults_to_zero() -> None:
    ev = _event([_alt(words=[_FakeWord("hi", 0.0, 0.3)], confidence=None)])
    assert words_from_final_transcript(ev) == [("hi", 0.0, 0.3, 0.0)]


def test_not_given_timing_coerced_to_zero() -> None:
    """When the provider omits per-word timing, start/end aren't floats; the
    helper must still return float tuples (0.0) rather than leaking sentinels."""

    class _NotGiven:
        pass

    sentinel = _NotGiven()
    ev = _event(
        [_alt(words=[_FakeWord("um", sentinel, sentinel)], confidence=0.5)]
    )
    assert words_from_final_transcript(ev) == [("um", 0.0, 0.0, 0.5)]


# ---------------------------------------------------------------------------
# stt_node accumulation across a turn's FINAL_TRANSCRIPT events
# ---------------------------------------------------------------------------
#
# Deepgram emits MULTIPLE FINAL_TRANSCRIPT events within one user turn (one per
# utterance segment). ``stt_node`` must ACCUMULATE every segment's words onto
# ``self._pending_words`` so a long answer carries ALL its words to the turn —
# not just the last segment's few. The stash is reset (consumed + cleared) by
# ``on_user_turn_completed`` so accumulation is scoped to exactly one turn.


class _FakeAssembler:
    """Captures the words handed to ``submit_fragment`` (the real TurnAssembler
    interface ``on_user_turn_completed`` calls)."""

    def __init__(self) -> None:
        self.submitted: list[list] = []

    def submit_fragment(self, text: str, *, words) -> None:  # noqa: ANN001
        self.submitted.append(list(words))


def _final_event(words):
    """A FINAL_TRANSCRIPT SpeechEvent carrying ``words`` in its first alternative."""
    return SimpleNamespace(
        type=agent_mod._lk_stt.SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[_alt(words=words, confidence=0.9)],
    )


def _make_agent():
    from app.modules.interview_engine.agent import _EngineAgent

    return _EngineAgent(assembler=_FakeAssembler(), instructions="test")


async def _drain(agen):
    out = []
    async for ev in agen:
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_stt_node_accumulates_words_across_multiple_finals(monkeypatch) -> None:
    """Two FINAL_TRANSCRIPT events in ONE turn → ``_pending_words`` holds BOTH
    segments' words, in order — not just the last segment's."""
    seg1 = [_FakeWord("the", 1.0, 1.2), _FakeWord("system", 1.3, 1.8)]
    seg2 = [_FakeWord("scales", 2.0, 2.4), _FakeWord("horizontally", 2.5, 3.2)]

    ev1 = _final_event(seg1)
    ev2 = _final_event(seg2)

    async def _fake_default_stt_node(self, audio, model_settings):  # noqa: ANN001
        # The default node yields the two segment finals for this single turn.
        yield ev1
        yield ev2

    monkeypatch.setattr(agent_mod.Agent.default, "stt_node", _fake_default_stt_node)

    agent = _make_agent()
    yielded = await _drain(agent.stt_node(audio=object(), model_settings=object()))

    # Pass-through invariant: EVERY event is yielded unchanged.
    assert yielded == [ev1, ev2]

    # Accumulation: both segments' words present, in order (NOT just seg2).
    assert agent._pending_words == [
        ("the", 1.0, 1.2, 0.9),
        ("system", 1.3, 1.8, 0.9),
        ("scales", 2.0, 2.4, 0.9),
        ("horizontally", 2.5, 3.2, 0.9),
    ]


@pytest.mark.asyncio
async def test_on_user_turn_completed_consumes_and_resets_word_stash(monkeypatch) -> None:
    """After ``on_user_turn_completed`` consumes + clears the stash, the next
    turn's final starts a FRESH accumulation — no bleed across turns."""
    from livekit.agents import StopResponse

    turn1 = [_FakeWord("first", 0.0, 0.5), _FakeWord("answer", 0.6, 1.0)]
    turn2 = [_FakeWord("second", 5.0, 5.5)]

    async def _stt_turn1(self, audio, model_settings):  # noqa: ANN001
        yield _final_event(turn1)

    async def _stt_turn2(self, audio, model_settings):  # noqa: ANN001
        yield _final_event(turn2)

    agent = _make_agent()

    # --- Turn 1: accumulate then consume ---
    monkeypatch.setattr(agent_mod.Agent.default, "stt_node", _stt_turn1)
    await _drain(agent.stt_node(audio=object(), model_settings=object()))
    assert len(agent._pending_words) == 2

    msg = SimpleNamespace(text_content="first answer")
    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(turn_ctx=object(), new_message=msg)

    # Stash cleared; the assembler received turn 1's words.
    assert agent._pending_words == []
    assert agent._assembler.submitted == [
        [("first", 0.0, 0.5, 0.9), ("answer", 0.6, 1.0, 0.9)]
    ]

    # --- Turn 2: fresh accumulation, no bleed from turn 1 ---
    monkeypatch.setattr(agent_mod.Agent.default, "stt_node", _stt_turn2)
    await _drain(agent.stt_node(audio=object(), model_settings=object()))
    assert agent._pending_words == [("second", 5.0, 5.5, 0.9)]
