import types

from app.modules.interview_engine.agent import _MouthAgent


def _bare_agent() -> _MouthAgent:
    # Bypass __init__ (it needs the full engine wiring); exercise only the
    # word-buffer logic on an otherwise-empty instance.
    a = _MouthAgent.__new__(_MouthAgent)
    a._pending_words = []
    return a


def _final_event(words):
    # Mimic stt.SpeechEvent: type==FINAL_TRANSCRIPT, alternatives[0].words[*]
    # carries .text/.start_time/.end_time/.confidence.
    word_objs = [
        types.SimpleNamespace(text=t, start_time=s, end_time=e, confidence=c)
        for (t, s, e, c) in words
    ]
    alt = types.SimpleNamespace(words=word_objs)
    return types.SimpleNamespace(type="final_transcript", alternatives=[alt])


def test_collect_appends_words_from_final_transcript():
    a = _bare_agent()
    a._collect_words_from_event(_final_event([("hi", 1.0, 1.2, 0.9)]))
    assert a._pending_words == [("hi", 1.0, 1.2, 0.9)]


def test_collect_ignores_non_final_and_empty():
    a = _bare_agent()
    interim = types.SimpleNamespace(type="interim_transcript", alternatives=[])
    a._collect_words_from_event(interim)
    a._collect_words_from_event(_final_event([]))  # final, but no words
    assert a._pending_words == []


def test_collect_accumulates_across_multiple_finals():
    a = _bare_agent()
    a._collect_words_from_event(_final_event([("a", 1.0, 1.1, 0.9)]))
    a._collect_words_from_event(_final_event([("b", 2.0, 2.1, 0.9)]))
    assert [w[0] for w in a._pending_words] == ["a", "b"]
