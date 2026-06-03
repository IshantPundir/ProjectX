"""answer_runs — group a session transcript into logical answers (lean nexus)."""
from app.modules.reel.transcript import answer_runs


def _cand(commit, words, qid="q1"):
    return {"role": "candidate", "timestamp_ms": commit, "question_id": qid,
            "words": [{"text": t, "start_ms": s, "end_ms": e} for t, s, e in words]}


def _agent(text="ok"):
    return {"role": "agent", "text": text}


def test_consecutive_candidate_turns_form_one_run():
    tr = [
        _agent(),
        _cand(100, [("a", 0, 300), ("b", 400, 700)]),
        _cand(200, [("c", 0, 200)]),     # continuation -> same run
        _agent(),
        _cand(300, [("d", 0, 100)]),     # new run
    ]
    runs = answer_runs(tr)
    assert len(runs) == 2
    r0 = runs[0]
    assert r0.ref == 100 and r0.turns == [100, 200]
    assert [w.text for w in r0.words] == ["a", "b", "c"]
    assert [w.idx for w in r0.words] == [0, 1, 2]
    assert runs[1].ref == 300 and [w.text for w in runs[1].words] == ["d"]


def test_run_carries_first_turns_question_id():
    runs = answer_runs([_cand(5, [("x", 0, 100)], qid="QID-42")])
    assert runs[0].question_id == "QID-42"


def test_within_turn_gap_is_pause_before_each_word():
    runs = answer_runs([_cand(100, [("a", 0, 300), ("b", 400, 700)])])
    w = runs[0].words
    assert w[0].gap_before_ms == 0          # first word of the run
    assert w[1].gap_before_ms == 100        # 400 - 300


def test_turn_boundary_word_is_marked_as_a_pause():
    runs = answer_runs([
        _cand(100, [("a", 0, 300)]),
        _cand(200, [("b", 0, 250)]),        # first word of a continuation turn
    ])
    w = runs[0].words
    assert w[1].text == "b" and w[1].turn_commit == 200
    assert w[1].gap_before_ms < 0           # turn boundary = a definite pause (sentinel)


def test_agent_turn_between_candidates_splits_runs():
    runs = answer_runs([
        _cand(100, [("a", 0, 100)]),
        _agent("Mm-hmm"),                   # agent audio between -> not contiguous
        _cand(200, [("b", 0, 100)]),
    ])
    assert [r.ref for r in runs] == [100, 200]


def test_empty_word_candidate_turn_does_not_break_run_or_add_words():
    runs = answer_runs([
        _cand(100, [("a", 0, 100)]),
        _cand(150, []),                     # silent/empty commit
        _cand(200, [("b", 0, 100)]),
    ])
    assert len(runs) == 1
    assert [w.text for w in runs[0].words] == ["a", "b"]
    assert runs[0].turns == [100, 150, 200]


def test_no_candidate_turns_yields_no_runs():
    assert answer_runs([_agent(), _agent()]) == []
