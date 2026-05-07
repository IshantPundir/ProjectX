import pytest

from app.modules.interview_engine.models.judge import (
    AdvancePayload, ClarifyPayload, CoverageTransition,
    JudgeOutput, NextAction, Observation, ProbePayload, RepeatPayload,
    ClaimEntry as JudgeClaimEntry, TurnMetadata,
    AcknowledgeNoExperiencePayload,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import (
    StateEngine, StateEngineDecision, StateEngineConfig,
)
from app.modules.interview_runtime.schemas import (
    SessionConfig, QuestionConfig, QuestionRubric, SignalMetadata,
    StageConfig, CompanyContext, CandidateContext,
)


def _question(qid: str, position: int, mandatory: bool, follow_ups: list[str], signal_values: list[str]):
    return QuestionConfig(
        id=qid, position=position, text=f"Tell me about {qid} please.",
        signal_values=signal_values, estimated_minutes=2.0,
        is_mandatory=mandatory, follow_ups=follow_ups,
        positive_evidence=["evidence-0", "evidence-1", "evidence-2"],
        red_flags=["flag-0", "flag-1"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="hint hint hint hint",
        question_kind="technical_depth",
    )


def _config():
    questions = [
        _question("q1", 0, True, ["fu0", "fu1"], ["S1"]),
        _question("q2", 1, True, ["fu0"], ["S2"]),
    ]
    return SessionConfig(
        session_id="sess-1", job_id="job-1", candidate_id="cand-1",
        job_title="SRE",
        role_summary="rrrrrr",
        seniority_level="Senior",
        company=CompanyContext(
            about="Acme is an enterprise software company building tools for hiring teams.",
            industry="software",
            company_stage="growth",
            hiring_bar="High bar — only senior engineers.",
        ),
        candidate=CandidateContext(name="Alice"),
        stage=StageConfig(
            stage_id="stg-1",
            stage_type="ai_screening",
            name="AI Screening",
            duration_minutes=10,
            difficulty="medium",
            questions=questions,
        ),
        signals=["S1", "S2"],
        signal_metadata=[
            SignalMetadata(value="S1", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="S2", type="competency", priority="required", weight=3,
                           knockout=True, stage="screen", evaluation_method="verbal_response"),
        ],
    )


def _judge_advance(target: str) -> JudgeOutput:
    return JudgeOutput(
        thought="advancing",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id=target),
        turn_metadata=TurnMetadata(),
    )


def _engine() -> StateEngine:
    return StateEngine(
        session_config=_config(),
        config=StateEngineConfig(claims_pool_max=50),
    )


def test_initialize_for_session_start_returns_advance_to_position_zero():
    eng = _engine()
    j = eng.initialize_for_session_start()
    assert j.next_action == NextAction.advance
    assert j.next_action_payload.target_question_id == "q1"


def test_process_advance_resolves_first_question_speaker_input():
    eng = _engine()
    j = eng.initialize_for_session_start()
    decision = eng.process_judge_output(
        turn_id="t-0", judge_output=j, candidate_utterance_text=None,
        elapsed_ms=0,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_first_question
    assert "Tell me about q1" in (decision.speaker_input.bank_text or "")


def test_process_probe_consumes_remaining():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        thought="probing",
        observations=[
            Observation(signal_value="S1", anchor_id=0, evidence_quote="ev",
                        coverage_transition=CoverageTransition.none_to_partial),
        ],
        candidate_claims=[],
        next_action=NextAction.probe,
        next_action_payload=ProbePayload(probe_id="0", probe_rationale="r"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="my answer",
        elapsed_ms=4000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_probe
    assert decision.speaker_input.bank_text == "fu0"


def test_no_experience_disclosure_marks_signal_failed():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        thought="no experience",
        observations=[
            Observation(signal_value="S1", anchor_id=-1, evidence_quote="never used it",
                        coverage_transition=CoverageTransition.none_to_failed),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S1"),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="never used", elapsed_ms=2000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.acknowledge_no_experience
    assert decision.speaker_input.failed_signal_value == "S1"


def test_repeat_action_uses_cached_utterance():
    """When Judge emits repeat, decision carries cached_utterance + bypasses Speaker."""
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    eng.register_agent_utterance(turn_id="t-0", text="Tell me about your work with q1.")
    j = JudgeOutput(
        thought="candidate asked to repeat",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.repeat,
        next_action_payload=RepeatPayload(),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="can you repeat that?",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.repeat
    assert decision.cached_utterance == "Tell me about your work with q1."
    assert decision.cached_source_turn_id == "t-0"


def test_repeat_without_prior_utterance_degrades_to_clarify():
    """If repeat is requested before any agent utterance exists, degrade to clarify with a warning."""
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # NO register_agent_utterance call.
    j = JudgeOutput(
        thought="candidate asked to repeat",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.repeat,
        next_action_payload=RepeatPayload(),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="repeat",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.clarify
    assert any(
        w.code == "repeat_without_prior_utterance" for w in decision.validation_warnings
    )


def test_invalid_probe_id_falls_back_to_first_unused_followup():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        thought="probing",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.probe,
        next_action_payload=ProbePayload(probe_id="99", probe_rationale="r"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="answer",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_probe
    assert decision.speaker_input.bank_text == "fu0"  # first unused
    assert any(
        w.code == "invalid_probe_id" for w in decision.validation_warnings
    )


def test_advance_to_unknown_target_picks_next_pending_mandatory():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = _judge_advance("q-DOES-NOT-EXIST")
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="answer", elapsed_ms=1000,
    )
    assert decision.speaker_input.bank_text and "q2" in decision.speaker_input.bank_text
    assert any(w.code == "invalid_target_question_id" for w in decision.validation_warnings)


def test_end_session_blocked_without_knockout_or_complete():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    from app.modules.interview_engine.models.judge import EndSessionPayload
    j = JudgeOutput(
        thought="ending",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.end_session,
        next_action_payload=EndSessionPayload(initiated_by="agent_initiated"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="x", elapsed_ms=1000,
    )
    # Should fall back to advance (q2) not actually end.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert any(w.code == "end_session_not_allowed" for w in decision.validation_warnings)
