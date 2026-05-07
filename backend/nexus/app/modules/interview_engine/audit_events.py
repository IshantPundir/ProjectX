"""Pydantic payload schemas for engine audit event kinds.

Every event written via EventCollector.append uses one of these payload shapes.
The collector itself doesn't validate — these models are for type discipline at
the call sites (orchestrator, JudgeService, SpeakerService) and for parsing
audit envelopes downstream.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# Turn boundaries
class TurnStartedPayload(BaseModel):
    turn_id: str
    turn_index: int = Field(ge=0)
    stt_text_raw: str | None = None     # verbatim Deepgram output
    stt_text_used: str | None = None    # what the Judge sees (= raw in v1)


class TurnCompletedPayload(BaseModel):
    turn_id: str
    turn_index: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


# Judge
class JudgeCallPayload(BaseModel):
    turn_id: str
    model: str
    prompt_hash: str
    input_summary: dict[str, Any]
    output: dict[str, Any]              # JudgeOutput.model_dump(mode="json")
    latency_ms: int = Field(ge=0)
    usage: dict[str, int] | None = None  # {"prompt_tokens": …, "completion_tokens": …}


class JudgeSyntheticPayload(BaseModel):
    turn_id: str
    output: dict[str, Any]
    reason: Literal["session_start"] = "session_start"


class JudgeFallbackPayload(BaseModel):
    turn_id: str
    reason: Literal["timeout", "parse_error", "validation_error", "no_advance_target"]
    original_failure_context: dict[str, Any]
    synthesized_output: dict[str, Any]


class JudgeValidationPayload(BaseModel):
    turn_id: str
    level: Literal["warning", "error"]
    code: str
    details: dict[str, Any]


# State mutations
class StateMutationPayload(BaseModel):
    turn_id: str
    seq: int = Field(ge=1)
    kind: Literal[
        "ledger.append", "queue.advance", "queue.probe", "queue.complete",
        "claims.add", "claims.drop_oldest",
        "lifecycle.transition", "knockout.recorded",
    ]
    before: dict[str, Any] | None
    after: dict[str, Any]


# Speaker
class SpeakerCallPayload(BaseModel):
    turn_id: str
    model: str
    prompt_hash: str
    instruction_kind: str
    bank_text_present: bool
    latency_ms_first_token: int = Field(ge=0)
    latency_ms_total: int = Field(ge=0)
    usage: dict[str, int] | None = None
    final_utterance: str


class SpeakerCachedPayload(BaseModel):
    turn_id: str
    instruction_kind: Literal["repeat"]
    source_turn_id: str
    final_utterance: str


class SpeakerOutputPayload(BaseModel):
    turn_id: str
    final_utterance: str


class SpeakerErrorPayload(BaseModel):
    turn_id: str
    model: str
    error_class: str
    error_message: str = Field(max_length=500)
    recovery_utterance: str


# Lifecycle / checkpoint
class LifecycleTransitionPayload(BaseModel):
    turn_id: str | None
    from_state: str
    to_state: str


class CheckpointWrittenPayload(BaseModel):
    turn_id: str
    last_audit_seq_flushed: int = Field(ge=0)
    captured_at_ms: int = Field(ge=0)


# Frontend
class FrontendAttributePayload(BaseModel):
    turn_id: str | None
    attribute_name: str
    value: str


# Session terminal — fired when lifecycle is closing/closed and a candidate
# turn arrives. The orchestrator bypasses Judge entirely and plays a canned
# terminal message. This event records the attempt for forensic completeness.
class SessionTerminalDeliveredPayload(BaseModel):
    turn_id: str
    lifecycle_state: Literal["closing", "closed"]
    lifecycle_outcome: str | None  # last_outcome value if set
    message: str  # the canned terminal text actually delivered
