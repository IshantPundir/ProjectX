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


# Instruction kinds where the Speaker does NOT need conversational context.
# For these, the speaker output is a short scaffold-driven utterance
# (de-escalation, terminal close, no-experience acknowledgement, repeat
# replay, push-for-specifics) — none of which benefit from recent_turns
# or claims. Stripping both fields cuts ~500-1500 tok of input per call
# without affecting tone, which is driven by turn_metadata + bank_text
# + persona.
_NON_CONTEXTUAL_KINDS: frozenset[InstructionKind] = frozenset({
    InstructionKind.redirect,
    InstructionKind.repeat,
    InstructionKind.acknowledge_no_experience,
    InstructionKind.polite_close,
    InstructionKind.push_back,
})


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
    recent_reply_starts: list[str] | None = None,
    is_post_cap_advance: bool = False,
    closing_disclosure_signal: str | None = None,
) -> SpeakerInput:
    """Anti-leak guarantee: NEVER include positive_evidence, red_flags, rubric.

    For instruction_kind=redirect (Task 8), copy JudgeOutput.turn_metadata
    into SpeakerInput.turn_metadata so the Speaker can pick tone (warm
    greeting vs neutral redirect vs calm de-escalation vs generic
    injection deflection). For ALL other kinds, turn_metadata stays
    None — preventing tone-leak across scaffolds.

    Non-contextual kinds (redirect / repeat / acknowledge_no_experience /
    polite_close) get an empty recent_turns and empty claims pool: the
    Speaker only needs bank_text + last_candidate_utterance + flags +
    persona to compose a short scaffolded utterance, and carrying the
    transcript would inflate the prompt by 500-1500 tokens with no quality
    benefit on these turns.
    """
    bank_text: str | None = None
    failed_signal_value: str | None = None
    turn_metadata: TurnMetadata | None = None
    push_back_reason_code: str | None = None

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
        # v2: if the candidate is clarifying after a probe, rephrase the
        # PROBE, not the main question. The Speaker's clarify.txt v2 handles
        # short bank_text as "rephrase the follow-up" — do not widen back
        # to the main topic. (Diagnostic: session 70c126b4 turns 5-6 —
        # candidate said "I didn't quite understand the question" after a
        # probe, and the agent rephrased the MAIN question Q2 instead of
        # the probe about metrics. UX failure.)
        active_state = queue.active_state()
        if active_question and active_state and active_state.probes_asked_ids:
            last_probe_id = active_state.probes_asked_ids[-1]
            idx = int(last_probe_id)
            if 0 <= idx < len(active_question.follow_ups):
                bank_text = active_question.follow_ups[idx]
        if bank_text is None:
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

    elif instruction_kind == InstructionKind.push_back:
        # Phase 9.2 push_back path. The Speaker needs bank_text (so it can
        # reference the active question abstractly when needed) and the
        # push_back_reason_code (to pick the per-reason template). Only
        # active_question.text is exposed — anti-leak still holds.
        from app.modules.interview_engine.models.judge import PushBackPayload
        bank_text = active_question.text if active_question else None
        if isinstance(judge_output.next_action_payload, PushBackPayload):
            push_back_reason_code = judge_output.next_action_payload.reason_code

    # InstructionKind.repeat: bank_text is None; orchestrator uses cached_utterance.
    # InstructionKind.polite_close: bank_text is None and turn_metadata is None —
    # Speaker uses canned scaffolds. Q-3 (Phase 9.3): if the State Engine
    # signaled this close was triggered by a knockout policy override
    # (originating from a no-experience disclosure), populate
    # failed_signal_value so polite_close.txt can acknowledge the
    # disclosure inline before the canned close. Without this, the
    # candidate hears a generic "thanks for your time" right after
    # disclosing they don't have experience — abrupt and impersonal.
    if instruction_kind == InstructionKind.polite_close and closing_disclosure_signal:
        failed_signal_value = closing_disclosure_signal

    # Non-contextual kinds: omit transcript + claims pool (see module-level
    # _NON_CONTEXTUAL_KINDS rationale). The conversational context the
    # Speaker uses for these short utterances is the candidate's just-spoken
    # line + the active question, both of which are still passed.
    if instruction_kind in _NON_CONTEXTUAL_KINDS:
        recent_turns_payload: list[TranscriptEntry] = []
        claims_payload = []
    else:
        recent_turns_payload = list(recent_turns)
        claims_payload = claims_pool.snapshot().entries

    # recent_reply_starts: only useful for non-contextual kinds where
    # recent_turns is dropped. For contextual kinds the Speaker already
    # sees the agent's prior turns in recent_turns and can vary
    # naturally; threading these there would be redundant prompt bloat.
    reply_starts_payload: list[str] = (
        list(recent_reply_starts or [])
        if instruction_kind in _NON_CONTEXTUAL_KINDS
        else []
    )

    # is_post_cap_advance (Q-2, Phase 9.3): only meaningful on
    # deliver_question (the new question being delivered after a
    # cap-forced advance). Drop on every other path so the Speaker
    # scaffold's segue logic doesn't fire on, e.g., a post-clarify
    # deliver_first_question.
    post_cap_payload = (
        is_post_cap_advance
        and instruction_kind == InstructionKind.deliver_question
    )

    return SpeakerInput(
        instruction_kind=instruction_kind,
        bank_text=bank_text,
        last_candidate_utterance=last_candidate_utterance,
        recent_turns=recent_turns_payload,
        claims_pool_snapshot=claims_payload,
        persona_name=persona_name,
        candidate_name=candidate_name,
        failed_signal_value=failed_signal_value,
        turn_metadata=turn_metadata,
        push_back_reason_code=push_back_reason_code,
        recent_reply_starts=reply_starts_payload,
        is_post_cap_advance=post_cap_payload,
    )
