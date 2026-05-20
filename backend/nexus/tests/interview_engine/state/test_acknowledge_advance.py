"""ack_no_experience / meta_confession / stuck now advance the queue in one turn (Option A)."""
from app.modules.interview_engine.models.judge import (
    AcknowledgeNoExperiencePayload, JudgeOutput, NextAction, Observation, TurnMetadata,
)
from app.modules.interview_engine.models.judge import CoverageTransition, CoverageQuality
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
    SessionConfig, SignalMetadata, StageConfig,
)


def _q(qid: str, signal: str, *, mandatory: bool, position: int) -> QuestionConfig:
    return QuestionConfig(
        id=qid, position=position, text="A sufficiently long question about the topic.",
        signal_values=[signal], estimated_minutes=2.0, is_mandatory=mandatory,
        follow_ups=[], positive_evidence=["ev-a", "ev-b", "ev-c"],
        red_flags=["rf-a", "rf-b"],
        rubric=QuestionRubric(
            excellent="A strong answer names concrete tools and tradeoffs here.",
            meets_bar="An acceptable answer names at least one concrete tool here.",
            below_bar="A weak answer stays generic with no specifics at all here.",
        ),
        evaluation_hint="Look for one concrete, specific example.",
        question_kind="technical_depth",
    )


def _session_config() -> SessionConfig:
    return SessionConfig(
        session_id="s1", job_id="j1", candidate_id="c1", job_title="Engineer",
        role_summary="Build integrations.", seniority_level="mid",
        company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(
            stage_id="st1", stage_type="ai_screening", name="Screen",
            duration_minutes=15, difficulty="medium",
            questions=[
                _q("q1", "sig_python", mandatory=True, position=0),
                _q("q2", "sig_rest", mandatory=True, position=1),
            ],
        ),
        signals=["sig_python", "sig_rest"],
        signal_metadata=[
            SignalMetadata(value="sig_python", type="competency", priority="required",
                           weight=2, knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_rest", type="competency", priority="required",
                           weight=3, knockout=False, stage="screen", evaluation_method="verbal_response"),
        ],
    )


def _ack_output(failed_signal: str) -> JudgeOutput:
    return JudgeOutput(
        reasoning="Candidate explicitly disclosed no experience with the active signal.",
        observations=[Observation(
            signal_value=failed_signal, anchor_id=-1,
            evidence_quote="I have never used that.",
            coverage_transition=CoverageTransition.none_to_failed,
            quality=CoverageQuality.concrete,
        )],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value=failed_signal),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )


def test_ack_no_experience_advances_to_next_question_in_one_turn():
    eng = StateEngine(session_config=_session_config(),
                      config=StateEngineConfig(knockout_policy="close_polite"))
    # Start: advance to q1 (synthetic session start).
    eng.process_judge_output(
        turn_id="t0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Candidate discloses no-experience on q1's (non-knockout) signal.
    decision = eng.process_judge_output(
        turn_id="t1", judge_output=_ack_output("sig_python"),
        candidate_utterance_text="I have never used Python.", elapsed_ms=1000,
    )
    # Option A: same turn delivers the NEXT question with the post-ack flag.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_acknowledge is True
    assert eng.queue_snapshot().active_index == 1  # advanced to q2
    assert eng.lifecycle_snapshot().state.value == "active"


def test_ack_on_last_question_politely_closes_with_disclosure_signal():
    cfg = _session_config()
    eng = StateEngine(session_config=cfg, config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(
        turn_id="t0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Advance to q2 (last mandatory) first via a clean advance.
    from app.modules.interview_engine.models.judge import AdvancePayload
    adv = JudgeOutput(
        reasoning="Candidate gave a concrete answer on q1; advancing to q2.",
        observations=[Observation(
            signal_value="sig_python", anchor_id=0, evidence_quote="I use Python daily.",
            coverage_transition=CoverageTransition.none_to_sufficient, quality=CoverageQuality.concrete,
        )],
        candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata(),
    )
    eng.process_judge_output(turn_id="t1", judge_output=adv,
                             candidate_utterance_text="I use Python daily.", elapsed_ms=1000)
    # Now ack no-experience on q2 (the last question) → polite_close.
    decision = eng.process_judge_output(
        turn_id="t2", judge_output=_ack_output("sig_rest"),
        candidate_utterance_text="I have never touched REST.", elapsed_ms=2000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert eng.lifecycle_snapshot().state.value == "closing"
