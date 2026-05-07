"""Speaker input builder. Anti-leak: never carries rubric content."""
from __future__ import annotations

from app.modules.interview_engine.models.judge import JudgeOutput
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
) -> SpeakerInput:
    """Anti-leak guarantee: NEVER include positive_evidence, red_flags, rubric."""
    bank_text: str | None = None
    failed_signal_value: str | None = None

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

    # InstructionKind.repeat: bank_text is None; orchestrator uses cached_utterance.
    # Redirects + polite_close: bank_text is None; Speaker uses canned scaffolds.

    return SpeakerInput(
        instruction_kind=instruction_kind,
        bank_text=bank_text,
        last_candidate_utterance=last_candidate_utterance,
        recent_turns=recent_turns,
        claims_pool_snapshot=claims_pool.snapshot().entries,
        persona_name=persona_name,
        failed_signal_value=failed_signal_value,
    )
