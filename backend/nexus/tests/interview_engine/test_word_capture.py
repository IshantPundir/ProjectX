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


def test_build_candidate_entry_attaches_words_and_clears_buffer():
    a = _bare_agent()
    a._collect_words_from_event(_final_event([("six", 12.40, 12.72, 0.99),
                                              ("years", 12.80, 13.30, 0.97)]))
    entry = a._build_candidate_entry(text="six years", timestamp_ms=42000, question_id="q1")

    assert entry.role == "candidate"
    assert entry.question_id == "q1"
    assert entry.timestamp_ms == 42000
    assert entry.end_ms == 42000
    assert entry.start_ms == 42000 - 900           # back off spoken duration
    assert [w.text for w in entry.words] == ["six", "years"]
    assert entry.words[0].start_ms == 0            # relative to first word
    assert a._pending_words == []                  # buffer drained


def test_build_candidate_entry_without_words_is_backward_compatible():
    a = _bare_agent()
    entry = a._build_candidate_entry(text="ok", timestamp_ms=1000, question_id=None)
    assert entry.words is None
    assert entry.start_ms is None and entry.end_ms is None
    assert entry.timestamp_ms == 1000
