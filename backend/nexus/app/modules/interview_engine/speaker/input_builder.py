"""Speaker input builder. Anti-leak: never carries rubric content."""
from __future__ import annotations

from app.modules.interview_engine.models.judge import JudgeOutput, TurnMetadata
from app.modules.interview_engine.models.speaker import (
    InstructionKind, SpeakerInput,
)
from app.modules.interview_engine.state.claims import CandidateClaimsPool
from app.modules.interview_engine.state.queue import QuestionQueue
from app.modules.interview_runtime import (
    QuestionConfig, TranscriptEntry,
)


def build_speaker_input(
    *,
    instruction_kind: InstructionKind,
    judge_output: JudgeOutput,
    active_question: QuestionConfig | None,
    queue: QuestionQueue,
    claims_pool: CandidateClaimsPool,
    recent_turns: list[TranscriptEntry],
    persona_name: str,
    last_candidate_utterance: str | None,
    candidate_name: str | None = None,
) -> SpeakerInput:
    """Anti-leak guarantee: NEVER include positive_evidence, red_flags, rubric.

    For instruction_kind=redirect (Task 8), copy JudgeOutput.turn_metadata
    into SpeakerInput.turn_metadata so the Speaker can pick tone (warm
    greeting vs neutral redirect vs calm de-escalation vs generic
    injection deflection). For ALL other kinds, turn_metadata stays
    None — preventing tone-leak across scaffolds.
    """
    bank_text: str | None = None
    failed_signal_value: str | None = None
    turn_metadata: TurnMetadata | None = None

    if instruction_kind in (
        InstructionKind.deliver_first_question,
        InstructionKind.deliver_question,
    ):
        bank_text = active_question.text if active_question else None

    elif instruction_kind == InstructionKind.deliver_probe:
        active_state = queue.active_state()
        if active_question and active_state and active_state.probes_asked_ids:
            last_probe_id = active_state.probes_asked_ids[-1]
            idx = int(last_probe_id)
            if 0 <= idx < len(active_question.follow_ups):
                bank_text = active_question.follow_ups[idx]

    elif instruction_kind == InstructionKind.clarify:
        bank_text = active_question.text if active_question else None

    elif instruction_kind == InstructionKind.acknowledge_no_experience:
        from app.modules.interview_engine.models.judge import (
            AcknowledgeNoExperiencePayload,
        )
        if isinstance(judge_output.next_action_payload, AcknowledgeNoExperiencePayload):
            failed_signal_value = judge_output.next_action_payload.failed_signal_value

    elif instruction_kind == InstructionKind.redirect:
        # Task 8 NEW path. The Speaker needs bank_text (to restate the
        # active question) AND turn_metadata (to pick tone). Only the
        # active_question.text field is exposed — never rubric content.
        bank_text = active_question.text if active_question else None
        turn_metadata = judge_output.turn_metadata

    # InstructionKind.repeat: bank_text is None; orchestrator uses cached_utterance.
    # InstructionKind.polite_close: bank_text is None and turn_metadata is None —
    # Speaker uses canned scaffolds.

    return SpeakerInput(
        instruction_kind=instruction_kind,
        bank_text=bank_text,
        last_candidate_utterance=last_candidate_utterance,
        recent_turns=recent_turns,
        claims_pool_snapshot=claims_pool.snapshot().entries,
        persona_name=persona_name,
        candidate_name=candidate_name,
        failed_signal_value=failed_signal_value,
        turn_metadata=turn_metadata,
    )
