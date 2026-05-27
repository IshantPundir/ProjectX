from app.modules.reporting.scoring.input_builder import build_messages


def test_messages_put_answer_last():
    msgs = build_messages(prefix="PREFIX", transcript_excerpt="CANDIDATE: foo")
    assert msgs[0]["role"] in ("system", "developer")
    assert msgs[0]["content"] == "PREFIX"
    assert msgs[-1]["role"] == "user"
    assert "foo" in msgs[-1]["content"]   # dynamic content LAST


def test_messages_wraps_transcript_in_xml():
    msgs = build_messages(prefix="SYS", transcript_excerpt="Hello world")
    user_content = msgs[-1]["content"]
    assert "<transcript>" in user_content
    assert "Hello world" in user_content
    assert "</transcript>" in user_content
