"""Synthetic JudgeOutput synthesizer for fallback flows.

When the Judge LLM call fails (timeout, parse_error, validation_error) or there
is no advance target available, we synthesize a JudgeOutput so the State Engine
has a uniform input shape. The reason is encoded in `thought` for audit
traceability.
"""
from __future__ import annotations

from enum import StrEnum

from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, PoliteClosePayload,
    TurnMetadata,
)


class FallbackReason(StrEnum):
    timeout = "timeout"
    parse_error = "parse_error"
    validation_error = "validation_error"
    no_advance_target = "no_advance_target"


def synthesize_fallback(
    *,
    reason: FallbackReason,
    next_pending_mandatory_id: str | None,
) -> JudgeOutput:
    if next_pending_mandatory_id is None:
        return JudgeOutput(
            thought=f"judge_fallback_{reason.value}",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.polite_close,
            next_action_payload=PoliteClosePayload(
                reason="judge_fallback_no_advance_target",
            ),
            turn_metadata=TurnMetadata(),
        )
    return JudgeOutput(
        thought=f"judge_fallback_{reason.value}",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(
            target_question_id=next_pending_mandatory_id,
        ),
        turn_metadata=TurnMetadata(),
    )
