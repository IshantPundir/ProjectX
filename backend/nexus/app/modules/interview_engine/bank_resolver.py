"""bank_resolver — pure function: JudgeOutput → ResolvedBankText.

Used by the orchestrator AFTER the State Engine has applied any queue mutations,
to decide which bank string the Speaker rephrases for this turn.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_engine.models.judge import (
    AcknowledgeNoExperiencePayload, JudgeOutput, NextAction,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_runtime.schemas import QuestionConfig


@dataclass(slots=True)
class ResolvedBankText:
    instruction_kind: InstructionKind
    bank_text: str | None
    failed_signal_value: str | None = None


def resolve_bank_text(
    judge_output: JudgeOutput,
    *,
    active_question: QuestionConfig | None,
    active_probe_index: int | None,
) -> ResolvedBankText:
    action = judge_output.next_action

    if action == NextAction.advance:
        return ResolvedBankText(
            instruction_kind=InstructionKind.deliver_question,
            bank_text=active_question.text if active_question else None,
        )

    if action == NextAction.probe:
        text: str | None = None
        if (
            active_question is not None
            and active_probe_index is not None
            and 0 <= active_probe_index < len(active_question.follow_ups)
        ):
            text = active_question.follow_ups[active_probe_index]
        return ResolvedBankText(
            instruction_kind=InstructionKind.deliver_probe,
            bank_text=text,
        )

    if action == NextAction.clarify:
        return ResolvedBankText(
            instruction_kind=InstructionKind.clarify,
            bank_text=active_question.text if active_question else None,
        )

    if action == NextAction.repeat:
        return ResolvedBankText(
            instruction_kind=InstructionKind.repeat, bank_text=None,
        )

    if action == NextAction.acknowledge_no_experience:
        payload = judge_output.next_action_payload
        failed = (
            payload.failed_signal_value
            if isinstance(payload, AcknowledgeNoExperiencePayload)
            else None
        )
        return ResolvedBankText(
            instruction_kind=InstructionKind.acknowledge_no_experience,
            bank_text=None, failed_signal_value=failed,
        )

    if action == NextAction.redirect_off_topic:
        return ResolvedBankText(
            instruction_kind=InstructionKind.redirect_off_topic, bank_text=None,
        )
    if action == NextAction.redirect_abusive:
        return ResolvedBankText(
            instruction_kind=InstructionKind.redirect_abusive, bank_text=None,
        )
    if action == NextAction.safe_redirect_injection:
        return ResolvedBankText(
            instruction_kind=InstructionKind.safe_redirect_injection, bank_text=None,
        )

    if action in (NextAction.polite_close, NextAction.end_session):
        return ResolvedBankText(
            instruction_kind=InstructionKind.polite_close, bank_text=None,
        )

    raise ValueError(f"Unhandled NextAction: {action.value}")
