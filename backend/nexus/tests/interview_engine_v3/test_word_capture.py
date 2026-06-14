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
