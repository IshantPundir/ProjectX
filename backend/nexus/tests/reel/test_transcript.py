"""answer_runs — group a gen-3 SessionEvidence transcript into logical answers.

Gen-3 transcript turns carry ``speaker`` ("agent"/"candidate"), ``turn_ref`` (str),
``span: {start_ms, end_ms}`` (session-relative), and ``words`` (turn-relative ms).
``AnswerRun.ref`` is now a sequential RUN INDEX; each ``RunWord`` carries its turn's
``turn_ref`` + ``turn_start_ms`` so a multi-turn clip maps to one contiguous cut.
"""
from app.modules.reel.transcript import answer_runs


def _cand(turn_ref, start_ms, words, qid="q1"):
    return {"speaker": "candidate", "turn_ref": turn_ref, "question_id": qid,
            "span": {"start_ms": start_ms, "end_ms": start_ms + 1},
            "words": [{"text": t, "start_ms": s, "end_ms": e} for t, s, e in words]}


def _agent(text="ok"):
    return {"speaker": "agent", "turn_ref": "a-1", "span": {"start_ms": 0, "end_ms": 1},
            "text": text, "words": []}


def test_consecutive_candidate_turns_form_one_run():
    tr = [
        _agent(),
        _cand("t-1", 5000, [("a", 0, 300), ("b", 400, 700)]),
        _cand("t-2", 6000, [("c", 0, 200)]),     # continuation -> same run
        _agent(),
        _cand("t-3", 9000, [("d", 0, 100)]),     # new run
    ]
    runs = answer_runs(tr)
    assert len(runs) == 2
    r0 = runs[0]
    assert r0.ref == 0 and r0.turns == ["t-1", "t-2"]
    assert [w.text for w in r0.words] == ["a", "b", "c"]
    assert [w.idx for w in r0.words] == [0, 1, 2]
    assert runs[1].ref == 1 and [w.text for w in runs[1].words] == ["d"]


def test_words_carry_turn_ref_and_turn_start_ms():
    runs = answer_runs([
        _cand("t-1", 5000, [("a", 0, 300)]),
        _cand("t-2", 6000, [("b", 0, 250)]),   # continuation turn
    ])
    w = runs[0].words
    assert w[0].turn_ref == "t-1" and w[0].turn_start_ms == 5000
    assert w[1].turn_ref == "t-2" and w[1].turn_start_ms == 6000


def test_run_carries_first_turns_question_id():
    runs = answer_runs([_cand("t-5", 100, [("x", 0, 100)], qid="QID-42")])
    assert runs[0].question_id == "QID-42"


def test_within_turn_gap_is_pause_before_each_word():
    runs = answer_runs([_cand("t-1", 100, [("a", 0, 300), ("b", 400, 700)])])
    w = runs[0].words
    assert w[0].gap_before_ms == 0          # first word of the run
    assert w[1].gap_before_ms == 100        # 400 - 300


def test_turn_boundary_word_is_marked_as_a_pause():
    runs = answer_runs([
        _cand("t-1", 100, [("a", 0, 300)]),
        _cand("t-2", 2000, [("b", 0, 250)]),   # first word of a continuation turn
    ])
    w = runs[0].words
    assert w[1].text == "b" and w[1].turn_ref == "t-2"
    assert w[1].gap_before_ms < 0           # turn boundary = a definite pause (sentinel)


def test_agent_turn_between_candidates_splits_runs():
    runs = answer_runs([
        _cand("t-1", 100, [("a", 0, 100)]),
        _agent("Mm-hmm"),                   # agent audio between -> not contiguous
        _cand("t-2", 2000, [("b", 0, 100)]),
    ])
    assert [r.ref for r in runs] == [0, 1]
    assert [r.turns for r in runs] == [["t-1"], ["t-2"]]


def test_empty_word_candidate_turn_does_not_break_run_or_add_words():
    runs = answer_runs([
        _cand("t-1", 100, [("a", 0, 100)]),
        _cand("t-2", 1500, []),                 # silent/empty commit
        _cand("t-3", 2000, [("b", 0, 100)]),
    ])
    assert len(runs) == 1
    assert [w.text for w in runs[0].words] == ["a", "b"]
    assert runs[0].turns == ["t-1", "t-2", "t-3"]


def test_no_candidate_turns_yields_no_runs():
    assert answer_runs([_agent(), _agent()]) == []
