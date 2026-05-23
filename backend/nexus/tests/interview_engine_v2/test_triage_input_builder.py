from app.modules.interview_engine_v2.triage.input_builder import (
    build_triage_messages,
    render_triage_prefix,
)

SYSTEM = "TRIAGE SYSTEM PROMPT (stable)."


def test_prefix_holds_system_and_persona():
    prefix = render_triage_prefix(system_prompt=SYSTEM, persona_name="Arjun",
                                  job_title="Backend Eng")
    assert SYSTEM in prefix and "Arjun" in prefix and "Backend Eng" in prefix


def test_messages_carry_question_accumulated_answer_and_last_question():
    prefix = render_triage_prefix(system_prompt=SYSTEM, persona_name="Arjun", job_title="X")
    msgs = build_triage_messages(
        triage_prefix=prefix, active_question="How long with Workato?",
        accumulated_answer="So, like, around one and a half",
        last_spoken_question="How long with Workato?")
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == prefix
    suffix = msgs[1]["content"]
    assert "ACTIVE QUESTION" in suffix and "How long with Workato?" in suffix
    assert "CANDIDATE SO FAR: «So, like, around one and a half»" in suffix
