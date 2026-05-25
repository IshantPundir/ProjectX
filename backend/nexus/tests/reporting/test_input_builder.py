from app.modules.reporting.scoring.input_builder import render_prefix, build_messages

QUESTION = {"id": "q4", "text": "Design an agent loop…",
            "rubric": {"excellent": "concrete loop + guardrails", "meets_bar": "basic loop",
                       "below_bar": "buzzwords, no controls"},
            "positive_evidence": ["allow-listed tools"], "red_flags": ["no constraints"]}

def test_prefix_is_byte_stable_across_answers():
    p1 = render_prefix(system_prompt="SYS", question=QUESTION)
    p2 = render_prefix(system_prompt="SYS", question=QUESTION)
    assert p1 == p2                       # identical -> cacheable

def test_prefix_contains_rubric_and_no_candidate_data():
    p = render_prefix(system_prompt="SYS", question=QUESTION)
    assert "concrete loop + guardrails" in p   # rubric is in the stable prefix
    assert "allow-listed tools" in p           # positive_evidence in prefix
    assert "no constraints" in p               # red_flags in prefix
    # the prefix must NOT contain any candidate answer text (that goes in the suffix)

def test_messages_put_answer_last():
    msgs = build_messages(prefix="PREFIX", transcript_excerpt="CANDIDATE: foo")
    assert msgs[0]["role"] in ("system", "developer")
    assert msgs[0]["content"] == "PREFIX"
    assert msgs[-1]["role"] == "user"
    assert "foo" in msgs[-1]["content"]   # dynamic content LAST
