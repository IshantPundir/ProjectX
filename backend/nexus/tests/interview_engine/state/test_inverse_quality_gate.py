"""Inverse quality gate — push_back + concrete/strong observation
downgrades to probe (or advance if probes exhausted).

Mirror of state/engine.py's existing quality_gated_advance (advance +
all-thin → push_back). Closes the gap that previously caused the
JudgeOutput validator to raise ValidationError → fallback synthesizes
advance → question bank force-walked.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def session_config_two_questions(make_session_config, make_question):
    """Two mandatory questions, each with two follow-up probes."""
    return make_session_config(
        questions=[
            make_question(
                qid="q1", position=0, mandatory=True,
                text="Walk me through your work on this topic.",
                signal_values=["S1"],
                follow_ups=["fu0-q1", "fu1-q1"],
            ),
            make_question(
                qid="q2", position=1, mandatory=True,
                text="Tell me about another aspect.",
                signal_values=["S1"],
                follow_ups=["fu0-q2", "fu1-q2"],
            ),
        ],
        signals=["S1"],
    )


def test_push_back_with_concrete_obs_downgrades_to_probe(
    session_config_two_questions,
) -> None:
    """Judge emits push_back + concrete → State Engine consumes the
    next probe instead of incrementing push_back_count."""
    from app.modules.interview_engine.state.engine import StateEngine
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, Observation,
        CoverageQuality, CoverageTransition, TurnMetadata,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    engine = StateEngine(session_config=session_config_two_questions)
    # Advance to the first question (synthesizes the session-start
    # JudgeOutput then applies it).
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t-0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )

    judge_output = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[Observation(
            signal_value=session_config_two_questions.signal_metadata[0].value,
            anchor_id=0,
            evidence_quote="I built an enterprise operations platform",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(),
    )
    decision = engine.process_judge_output(
        turn_id="t-1", judge_output=judge_output,
        candidate_utterance_text="long substantive answer", elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_probe
    # push_back_count should NOT have been incremented (we downgraded)
    queue = engine.queue_snapshot()
    assert queue.questions[0].push_back_count == 0
    # warning recorded
    codes = [w.code for w in decision.validation_warnings]
    assert "inverse_quality_gate" in codes


def test_push_back_with_thin_obs_keeps_push_back(
    session_config_two_questions,
) -> None:
    """Judge emits push_back + thin → push_back fires normally,
    push_back_count increments."""
    from app.modules.interview_engine.state.engine import StateEngine
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, Observation,
        CoverageQuality, CoverageTransition, TurnMetadata,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    engine = StateEngine(session_config=session_config_two_questions)
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t-0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )

    judge_output = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[Observation(
            signal_value=session_config_two_questions.signal_metadata[0].value,
            anchor_id=0,
            evidence_quote="I would add validation",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.thin,
        )],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="vague_answer"),
        turn_metadata=TurnMetadata(),
    )
    decision = engine.process_judge_output(
        turn_id="t-1", judge_output=judge_output,
        candidate_utterance_text="thin answer", elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.push_back
    queue = engine.queue_snapshot()
    assert queue.questions[0].push_back_count == 1
    codes = [w.code for w in decision.validation_warnings]
    assert "inverse_quality_gate" not in codes


def test_push_back_concrete_no_probes_left_advances(
    session_config_two_questions,
) -> None:
    """Judge emits push_back + concrete and all probes consumed →
    falls back to advance to next pending mandatory."""
    from app.modules.interview_engine.state.engine import StateEngine
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, ProbePayload, Observation,
        CoverageQuality, CoverageTransition, TurnMetadata,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    engine = StateEngine(session_config=session_config_two_questions)
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t-0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Consume both probes via two probe actions so probes_remaining_ids is empty.
    for i, probe_id in enumerate(["0", "1"], start=1):
        engine.process_judge_output(
            turn_id=f"t-{i}",
            judge_output=JudgeOutput(
                reasoning="Test-synthesized reasoning string for unit test fixture.",
                observations=[],
                candidate_claims=[],
                next_action=NextAction.probe,
                next_action_payload=ProbePayload(probe_id=probe_id),
                turn_metadata=TurnMetadata(),
            ),
            candidate_utterance_text="answer", elapsed_ms=i * 1000,
        )

    judge_output = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[Observation(
            signal_value=session_config_two_questions.signal_metadata[0].value,
            anchor_id=0,
            evidence_quote="concrete claim",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(),
    )
    decision = engine.process_judge_output(
        turn_id="t-3", judge_output=judge_output,
        candidate_utterance_text="another answer", elapsed_ms=4000,
    )
    # Probes exhausted → fallback advance picks deliver_question on next q.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    codes = [w.code for w in decision.validation_warnings]
    assert "inverse_quality_gate" in codes
