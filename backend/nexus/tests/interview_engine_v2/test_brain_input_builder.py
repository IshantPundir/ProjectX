from app.modules.interview_engine_v2.brain.input_builder import (
    build_brain_messages,
    render_stable_prefix,
)
from app.modules.interview_runtime import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)


def _question(qid="q1", primary="python"):
    return QuestionConfig(
        id=qid, position=0, text="Tell me about a service you built in Python.",
        signal_values=["python"], estimated_minutes=3.0, is_mandatory=True,
        follow_ups=["What did you own versus the team?", "How did you test it?"],
        positive_evidence=["names a real service", "describes a tradeoff", "owns a decision"],
        red_flags=["only 'we'", "hypothetical 'I would'"],
        rubric=QuestionRubric(
            excellent="deep ownership + tradeoffs", meets_bar="one concrete example",
            below_bar="generic, no specifics",
        ),
        evaluation_hint="listen for individual contribution", question_kind="behavioral",
        primary_signal=primary, difficulty="medium",
    )


def _config():
    return SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Backend Engineer",
        hiring_company_name="Workato", role_summary="Build integration backends.",
        jd_text="We need Python + Kafka.", seniority_level="mid",
        company=CompanyContext(about="iPaaS", industry="SaaS", hiring_bar="senior-leaning"),
        candidate=CandidateContext(name="Asha"),
        stage=StageConfig(stage_id="st1", stage_type="ai_screening", name="Screen",
                          duration_minutes=30, difficulty="medium", questions=[_question()]),
        signals=["python", "kafka"],
    )


SYSTEM = "BRAIN SYSTEM PROMPT (stable)."


def test_stable_prefix_is_compact_index_with_role_context():
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    assert SYSTEM in prefix
    assert "Backend Engineer" in prefix and "Workato" in prefix
    # COMPACT INDEX: id / signals / kind / difficulty / mandatory / text / follow-ups are present
    assert "q1" in prefix  # question id present so the brain can select by reference
    assert "primary_signal=python" in prefix and "kind=behavioral" in prefix
    assert "Tell me about a service you built in Python." in prefix
    assert "What did you own versus the team?" in prefix  # follow-up text indexed
    # the grading detail (rubric/positive_evidence/red_flags/eval hint) MOVED to the dynamic
    # suffix — it must NOT bloat the cache prefix (the ~36KB -> few-KB latency win)
    assert "deep ownership" not in prefix
    assert "only 'we'" not in prefix
    assert "listen for individual contribution" not in prefix


def test_stable_prefix_is_byte_stable_across_turns():
    """R6: the prefix is rendered once and is byte-identical regardless of turn (cache hit)."""
    cfg = _config()
    p1 = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    p2 = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    assert p1 == p2


def test_build_messages_prefix_then_dynamic_suffix():
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    msgs = build_brain_messages(
        stable_prefix=prefix,
        transcript_window=[("agent", "Tell me about a service you built."),
                           ("candidate", "I built a billing service.")],
        coverage_summary="python=partial, kafka=none",
        active_question=_question(),
        candidate_utterance="I built a billing service in Python with a teammate.",
    )
    # cached prefix is message[0]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == prefix
    assert msgs[1]["role"] == "user"
    suffix = msgs[1]["content"]
    # candidate speech fenced as DATA (spotlighting, doc 05)
    assert "CANDIDATE SAID: «I built a billing service in Python with a teammate.»" in suffix
    assert "python=partial" in suffix
    # the ACTIVE question's FULL rubric travels in the dynamic suffix (grade this turn against it)
    assert "ACTIVE QUESTION" in suffix
    assert "id=q1" in suffix
    assert "deep ownership" in suffix and "one concrete example" in suffix     # rubric
    assert "only 'we'" in suffix                                               # red flags
    assert "listen for individual contribution" in suffix                      # evaluation_hint


def test_build_messages_no_active_question_renders_none():
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    msgs = build_brain_messages(
        stable_prefix=prefix, transcript_window=[], coverage_summary="python=none",
        active_question=None, candidate_utterance="hi")
    suffix = msgs[1]["content"]
    assert "# ACTIVE QUESTION (grade this turn's answer against this rubric)\n(none)" in suffix


def test_message_prefix_identical_across_two_different_turns():
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    m1 = build_brain_messages(stable_prefix=prefix, transcript_window=[("candidate", "a")],
                              coverage_summary="python=none", active_question=_question(),
                              candidate_utterance="a")
    m2 = build_brain_messages(stable_prefix=prefix, transcript_window=[("candidate", "b")],
                              coverage_summary="python=partial", active_question=_question(),
                              candidate_utterance="b")
    assert m1[0]["content"] == m2[0]["content"]   # only the suffix changes turn-to-turn


def test_build_messages_lists_already_asked_questions():
    """Repeat-guard (brain side): the dynamic suffix tells the brain which questions were already
    physically asked so it won't re-pick one (the deterministic guard in service.py is the safety
    net; this reduces how often it has to override the brain). d9828b7b talk-test."""
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    msgs = build_brain_messages(
        stable_prefix=prefix, transcript_window=[], coverage_summary="python=none",
        active_question=_question(), candidate_utterance="hi",
        asked_question_ids=["q1", "q7"])
    suffix = msgs[1]["content"]
    assert "ALREADY ASKED" in suffix
    assert "q7" in suffix                       # q7 appears ONLY via the already-asked list


def test_build_messages_omits_already_asked_section_when_empty():
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    msgs = build_brain_messages(
        stable_prefix=prefix, transcript_window=[], coverage_summary="python=none",
        active_question=_question(), candidate_utterance="hi")
    assert "ALREADY ASKED" not in msgs[1]["content"]


def test_transcript_window_is_bounded():
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    window = [("candidate", f"turn {i}") for i in range(50)]
    msgs = build_brain_messages(stable_prefix=prefix, transcript_window=window,
                                coverage_summary="python=none", active_question=_question(),
                                candidate_utterance="latest", max_transcript_turns=6)
    suffix = msgs[1]["content"]
    assert "turn 49" in suffix and "turn 10" not in suffix   # only the last 6 turns kept


def test_build_messages_shows_active_probe_count():
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    msgs = build_brain_messages(
        stable_prefix=prefix, transcript_window=[], coverage_summary="python=partial",
        active_question=_question(), candidate_utterance="hi", active_probe_count=2)
    assert "PROBES SO FAR ON THIS QUESTION: 2" in msgs[1]["content"]


def test_knockout_pending_block_appears_only_for_failed_mandatory():
    """b33f4ed5: a failed MANDATORY signal must be surfaced as a KNOCKOUT PENDING block so the brain
    completes the confirm->knockout_close instead of advancing past it. Absent when none failed."""
    cfg = _config()
    prefix = render_stable_prefix(system_prompt=SYSTEM, config=cfg)
    base = dict(
        stable_prefix=prefix,
        transcript_window=[("candidate", "I don't have Workato experience.")],
        coverage_summary="python=failed, kafka=none",
        active_question=_question(),
        candidate_utterance="I already said I don't have it.",
    )
    # no failed_mandatory -> no block
    assert "KNOCKOUT PENDING" not in build_brain_messages(**base)[1]["content"]
    assert "KNOCKOUT PENDING" not in build_brain_messages(**base, failed_mandatory=[])[1]["content"]
    # failed_mandatory present -> a prominent block naming the signal + the confirm/knockout rule
    suffix = build_brain_messages(**base, failed_mandatory=["python"])[1]["content"]
    assert "KNOCKOUT PENDING" in suffix
    assert "python" in suffix
    assert "knockout_close" in suffix
