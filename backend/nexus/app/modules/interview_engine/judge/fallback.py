"""Synthetic JudgeOutput synthesizer for fallback flows.

When the Judge LLM call fails (timeout, parse_error, validation_error) or there
is no advance target available, we synthesize a JudgeOutput so the State Engine
has a uniform input shape. The fallback reason is recorded on the
``JUDGE_FALLBACK`` audit event (``original_failure_context``) — the synthesized
``JudgeOutput`` itself carries no audit-only prose anymore.
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
    # Phase 9.5 (2026-05-12): validation_error synthesizes clarify
    # instead of advance. The original behavior force-walked the queue
    # whenever the Judge model produced a malformed output (e.g. the
    # push_back+concrete cross-field combo); this killed the interview
    # early. Clarify is no-op on the queue and asks the candidate to
    # elaborate — better fallback for "model produced something but it
    # didn't validate" than skipping the question entirely.
    if reason == FallbackReason.validation_error:
        from app.modules.interview_engine.models.judge import ClarifyPayload
        return JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.clarify,
            next_action_payload=ClarifyPayload(),
            turn_metadata=TurnMetadata(),
        )
    if next_pending_mandatory_id is None:
        return JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.polite_close,
            next_action_payload=PoliteClosePayload(),
            turn_metadata=TurnMetadata(),
        )
    return JudgeOutput(
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(
            target_question_id=next_pending_mandatory_id,
        ),
        turn_metadata=TurnMetadata(),
    )
