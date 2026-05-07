from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, ProbePayload, TurnMetadata,
    AcknowledgeNoExperiencePayload, RedirectPayload,
)
from app.modules.interview_engine.speaker.input_builder import build_speaker_input
from app.modules.interview_engine.state.claims import CandidateClaimsPool
from app.modules.interview_engine.state.queue import QuestionQueue
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _q(text="Tell me about your work.", follow_ups=None):
    return QuestionConfig(
        id="q1", position=0, text=text, signal_values=["S1"], estimated_minutes=2.0,
        is_mandatory=True, follow_ups=follow_ups or [],
        positive_evidence=["EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C"],
        red_flags=["FLAG-A", "FLAG-B"],
        rubric=QuestionRubric(excellent="EX", meets_bar="MB", below_bar="BB"),
        evaluation_hint="HINT-CONTENT-VERY-SECRET",
        question_kind="technical_depth",
    )


def _judge(action, payload):
    return JudgeOutput(
        thought="t", observations=[], candidate_claims=[],
        next_action=action, next_action_payload=payload,
        turn_metadata=TurnMetadata(),
    )


def test_speaker_input_does_not_leak_positive_evidence():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=_judge(NextAction.advance, AdvancePayload(target_question_id="q1")),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance=None,
    )
    serialized = s.model_dump_json()
    for forbidden in ("EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C", "FLAG-A", "FLAG-B",
                      "EX", "MB", "BB", "HINT-CONTENT-VERY-SECRET"):
        assert forbidden not in serialized, f"{forbidden} leaked into Speaker input"


def test_probe_input_carries_correct_followup_text():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": ["FU-0", "FU-1"]}],
    )
    queue.advance_to("q1", at_turn=0)
    queue.apply_probe(probe_id="1", at_turn=1)
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_probe,
        judge_output=_judge(NextAction.probe, ProbePayload(probe_id="1", probe_rationale="r")),
        active_question=_q(follow_ups=["FU-0", "FU-1"]),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="answer",
    )
    assert s.bank_text == "FU-1"


def test_speaker_input_carries_candidate_name():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_first_question,
        judge_output=_judge(NextAction.advance, AdvancePayload(target_question_id="q1")),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance=None,
        candidate_name="Alice",
    )
    assert s.candidate_name == "Alice"
    assert s.persona_name == "Sam"
    # Anti-leak guarantee still holds.
    serialized = s.model_dump_json()
    for forbidden in ("EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C", "FLAG-A", "FLAG-B"):
        assert forbidden not in serialized


def test_redirect_kind_carries_turn_metadata_only():
    """Task 8: For instruction_kind=redirect, build_speaker_input copies
    JudgeOutput.turn_metadata into SpeakerInput.turn_metadata. The Speaker
    needs both bank_text (to restate the active question) AND turn_metadata
    (to pick tone) for the redirect scaffold.
    """
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    judge_out = JudgeOutput(
        thought="t", observations=[], candidate_claims=[],
        next_action=NextAction.redirect,
        next_action_payload=RedirectPayload(),
        turn_metadata=TurnMetadata(
            candidate_social_or_greeting=True, candidate_off_topic=True,
        ),
    )
    s = build_speaker_input(
        instruction_kind=InstructionKind.redirect,
        judge_output=judge_out,
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=10),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="Hi",
        candidate_name="Ishant",
    )
    assert s.turn_metadata is not None
    assert s.turn_metadata.candidate_social_or_greeting is True
    assert s.turn_metadata.candidate_off_topic is True
    # Anti-leak still holds — the rubric must NOT leak through the redirect path.
    serialized = s.model_dump_json()
    for forbidden in ("EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C", "FLAG-A", "FLAG-B",
                      "EX", "MB", "BB", "HINT-CONTENT-VERY-SECRET"):
        assert forbidden not in serialized, f"{forbidden} leaked into Speaker input"


def test_non_redirect_kind_has_no_turn_metadata():
    """Task 8: deliver_question (or any non-redirect kind) returns
    SpeakerInput with turn_metadata=None. Avoids tone-leak across
    scaffolds (a deliver_question Speaker call shouldn't see whether
    the candidate was off-topic on a previous turn)."""
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    judge_out = JudgeOutput(
        thought="t", observations=[], candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q1"),
        # turn_metadata set, but should be ignored for non-redirect kinds.
        turn_metadata=TurnMetadata(candidate_off_topic=True),
    )
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=judge_out,
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=10),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="answer",
        candidate_name="Ishant",
    )
    assert s.turn_metadata is None


def test_acknowledge_no_experience_carries_failed_signal():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    s = build_speaker_input(
        instruction_kind=InstructionKind.acknowledge_no_experience,
        judge_output=_judge(
            NextAction.acknowledge_no_experience,
            AcknowledgeNoExperiencePayload(failed_signal_value="JQL"),
        ),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="never used JQL",
    )
    assert s.failed_signal_value == "JQL"
    assert s.bank_text is None
