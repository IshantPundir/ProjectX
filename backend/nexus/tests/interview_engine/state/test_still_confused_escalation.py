"""Stuck-candidate escalation: after 2 still-confused clarifies, the 3rd
escalates to acknowledge-and-advance (Option A). No regex involved."""
from app.modules.interview_engine.models.judge import (
    ClarifyKind, ClarifyPayload, JudgeOutput, NextAction, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
    SessionConfig, SignalMetadata, StageConfig,
)


def _q(qid, sig, pos):
    return QuestionConfig(
        id=qid, position=pos, text="A question about the active topic here, please.",
        signal_values=[sig], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="Look for specifics here.", question_kind="technical_depth")


def _cfg():
    return SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Eng", role_summary="r",
        seniority_level="mid", company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="S",
                          duration_minutes=15, difficulty="medium",
                          questions=[_q("q1", "sig_a", 0), _q("q2", "sig_b", 1)]),
        signals=["sig_a", "sig_b"],
        signal_metadata=[
            SignalMetadata(value="sig_a", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_b", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
        ])


def _confused_clarify():
    return JudgeOutput(
        reasoning="Candidate expresses generic confusion and cannot engage with the question.",
        observations=[], candidate_claims=[], next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.broad_rephrase),
        turn_metadata=TurnMetadata(candidate_still_confused=True))


def test_two_clarifies_then_escalate_to_ack_advance():
    eng = StateEngine(session_config=_cfg(), config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)
    # Confusion #1 -> clarify, count -> 1
    d1 = eng.process_judge_output(turn_id="t1", judge_output=_confused_clarify(),
                                  candidate_utterance_text="I didn't quite understand.", elapsed_ms=1000)
    assert d1.speaker_input.instruction_kind == InstructionKind.clarify
    assert eng.queue_snapshot().questions[0].still_confused_count == 1
    # Confusion #2 -> clarify, count -> 2
    d2 = eng.process_judge_output(turn_id="t2", judge_output=_confused_clarify(),
                                  candidate_utterance_text="Still not following, sorry.", elapsed_ms=2000)
    assert d2.speaker_input.instruction_kind == InstructionKind.clarify
    assert eng.queue_snapshot().questions[0].still_confused_count == 2
    # Confusion #3 -> escalate to ack+advance
    d3 = eng.process_judge_output(turn_id="t3", judge_output=_confused_clarify(),
                                  candidate_utterance_text="I really do not get it.", elapsed_ms=3000)
    assert d3.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert d3.speaker_input.is_post_acknowledge is True
    assert eng.queue_snapshot().active_index == 1


def test_still_confused_count_resets_on_non_confused_turn():
    eng = StateEngine(session_config=_cfg(), config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)
    eng.process_judge_output(turn_id="t1", judge_output=_confused_clarify(),
                             candidate_utterance_text="huh?", elapsed_ms=1000)
    assert eng.queue_snapshot().questions[0].still_confused_count == 1
    # A clarify WITHOUT the flag (engaged term_definition) resets the streak.
    engaged = JudgeOutput(
        reasoning="Candidate asks a specific, engaged question about a term in the prompt.",
        observations=[], candidate_claims=[], next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.term_definition),
        turn_metadata=TurnMetadata())
    eng.process_judge_output(turn_id="t2", judge_output=engaged,
                             candidate_utterance_text="What is an upsert?", elapsed_ms=2000)
    assert eng.queue_snapshot().questions[0].still_confused_count == 0
