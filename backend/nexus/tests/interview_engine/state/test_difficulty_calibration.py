"""Difficulty calibrates the advance quality-gate and the push-back cap.

  easy   : gate OFF (engaged thin answer advances), push_back cap 1
  medium : gate >=1 concrete (today), push_back cap 2
  hard   : gate >=1 strong OR >=2 concrete, push_back cap 3
"""
from app.modules.interview_engine.models.judge import (
    AdvancePayload, CoverageQuality, CoverageTransition, JudgeOutput, NextAction,
    Observation, PushBackPayload, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import StateEngine
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
    SessionConfig, SignalMetadata, StageConfig,
)


def _q(qid, sig, pos, difficulty):
    return QuestionConfig(
        id=qid, position=pos, text="A question about the topic, walk me through it.",
        signal_values=[sig], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="Look for specifics.", question_kind="technical_depth",
        difficulty=difficulty)


def _cfg(difficulty):
    return SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Eng", role_summary="r",
        seniority_level="mid", company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="S",
                          duration_minutes=15, difficulty=difficulty,
                          questions=[_q("q1", "sig_a", 0, difficulty), _q("q2", "sig_b", 1, difficulty)]),
        signals=["sig_a", "sig_b"],
        signal_metadata=[
            SignalMetadata(value="sig_a", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_b", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
        ])


def _start(eng):
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)


def _advance_with_thin():
    return JudgeOutput(
        reasoning="Candidate engaged but the answer is generic with no specifics yet.",
        observations=[Observation(signal_value="sig_a", anchor_id=0, evidence_quote="I would log it.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.thin)],
        candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata())


def test_easy_gate_off_thin_answer_advances():
    eng = StateEngine(session_config=_cfg("easy"))
    _start(eng)
    d = eng.process_judge_output(turn_id="t1", judge_output=_advance_with_thin(),
                                 candidate_utterance_text="I would log it.", elapsed_ms=1000)
    assert d.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert eng.queue_snapshot().active_index == 1  # advanced despite thin


def test_medium_gate_thin_answer_downgraded_to_push_back():
    eng = StateEngine(session_config=_cfg("medium"))
    _start(eng)
    d = eng.process_judge_output(turn_id="t1", judge_output=_advance_with_thin(),
                                 candidate_utterance_text="I would log it.", elapsed_ms=1000)
    assert d.speaker_input.instruction_kind == InstructionKind.push_back
    assert eng.queue_snapshot().active_index == 0  # held


def test_hard_gate_single_concrete_downgraded_to_push_back():
    eng = StateEngine(session_config=_cfg("hard"))
    _start(eng)
    adv = JudgeOutput(
        reasoning="Candidate named one concrete tool but no tradeoffs or scale yet.",
        observations=[Observation(signal_value="sig_a", anchor_id=0,
                                  evidence_quote="I used Splunk for the logs.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.concrete)],
        candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata())
    d = eng.process_judge_output(turn_id="t1", judge_output=adv,
                                 candidate_utterance_text="I used Splunk.", elapsed_ms=1000)
    # hard needs >=1 strong OR >=2 concrete; one concrete is not enough.
    assert d.speaker_input.instruction_kind == InstructionKind.push_back
    assert eng.queue_snapshot().active_index == 0


def test_push_back_cap_easy_is_one():
    eng = StateEngine(session_config=_cfg("easy"))
    _start(eng)
    pb = JudgeOutput(
        reasoning="Candidate engaged but the answer is thin; pushing for one specific.",
        observations=[Observation(signal_value="sig_a", anchor_id=0, evidence_quote="logs.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.thin)],
        candidate_claims=[], next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata())
    # 1st push_back honored (count 0 -> 1).
    d1 = eng.process_judge_output(turn_id="t1", judge_output=pb,
                                  candidate_utterance_text="logs.", elapsed_ms=1000)
    assert d1.speaker_input.instruction_kind == InstructionKind.push_back
    # 2nd push_back at easy hits the cap (1) -> downgrades to advance.
    d2 = eng.process_judge_output(turn_id="t2", judge_output=pb,
                                  candidate_utterance_text="logs again.", elapsed_ms=2000)
    assert d2.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert d2.speaker_input.is_post_cap_advance is True


def test_hard_gate_two_concrete_advances():
    eng = StateEngine(session_config=_cfg("hard"))
    _start(eng)
    adv = JudgeOutput(
        reasoning="Candidate named two concrete tools with distinct roles in the pipeline.",
        observations=[
            Observation(signal_value="sig_a", anchor_id=0,
                        evidence_quote="I used Splunk for logs.",
                        coverage_transition=CoverageTransition.none_to_partial,
                        quality=CoverageQuality.concrete),
            Observation(signal_value="sig_a", anchor_id=1,
                        evidence_quote="and Grafana for dashboards.",
                        coverage_transition=CoverageTransition.partial_to_sufficient,
                        quality=CoverageQuality.concrete),
        ],
        candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata())
    d = eng.process_judge_output(turn_id="t1", judge_output=adv,
                                 candidate_utterance_text="Splunk and Grafana.", elapsed_ms=1000)
    assert d.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert eng.queue_snapshot().active_index == 1  # 2 concrete clears the hard gate


def test_easy_voluntary_advance_at_cap_sets_post_cap_flag():
    """On easy (cap 1), after one push_back the Judge VOLUNTARILY advances;
    is_post_cap_advance must fire (it would NOT under the old hardcoded >=2)."""
    eng = StateEngine(session_config=_cfg("easy"))
    _start(eng)
    pb = JudgeOutput(
        reasoning="Candidate engaged but thin; one push for a specific.",
        observations=[Observation(signal_value="sig_a", anchor_id=0, evidence_quote="logs.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.thin)],
        candidate_claims=[], next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata())
    # count -> 1 (cap on easy)
    eng.process_judge_output(turn_id="t1", judge_output=pb,
                             candidate_utterance_text="logs.", elapsed_ms=1000)
    # Judge now VOLUNTARILY advances (not a forced downgrade).
    adv = JudgeOutput(
        reasoning="Candidate cannot give more; advancing rather than looping on an easy question.",
        observations=[], candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata())
    d = eng.process_judge_output(turn_id="t2", judge_output=adv,
                                 candidate_utterance_text="that's all I've got.", elapsed_ms=2000)
    assert d.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert d.speaker_input.is_post_acknowledge is False
    assert d.speaker_input.is_post_cap_advance is True
    assert eng.queue_snapshot().active_index == 1
