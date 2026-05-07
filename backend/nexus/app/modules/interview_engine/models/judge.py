"""Judge output Pydantic models — structured LLM output for the per-turn pipeline."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


class NextAction(StrEnum):
    advance = "advance"
    probe = "probe"
    clarify = "clarify"
    repeat = "repeat"
    redirect = "redirect"
    acknowledge_no_experience = "acknowledge_no_experience"
    polite_close = "polite_close"
    end_session = "end_session"


class CoverageTransition(StrEnum):
    # Forward progression
    none_to_partial = "none→partial"
    partial_to_partial = "partial→partial"
    partial_to_sufficient = "partial→sufficient"
    none_to_sufficient = "none→sufficient"

    # Failure terminal
    none_to_failed = "none→failed"
    partial_to_failed = "partial→failed"
    sufficient_to_failed = "sufficient→failed"
    failed_to_failed = "failed→failed"

    # Backward transitions are NEVER legal.
    # No "strong" state — answer-quality grading is the Report Builder's job.


class Observation(BaseModel):
    signal_value: str
    anchor_id: int = Field(
        ge=-1,
        description="Index into positive_evidence; -1 sentinel for failure observations.",
    )
    evidence_quote: str = Field(min_length=1, max_length=500)
    coverage_transition: CoverageTransition


class ClaimEntry(BaseModel):
    """Judge-emitted claim shape (no capture metadata).

    State Engine canonicalizes this into models.claims.ClaimEntry by attaching
    captured_at_turn and captured_at_seq.
    """

    claim_topic: str = Field(min_length=1, max_length=40)
    claim_text: str = Field(min_length=1, max_length=200)
    source_quote: str = Field(min_length=1, max_length=500)


class TurnMetadata(BaseModel):
    candidate_disclosed_no_experience: bool = False
    candidate_disclosed_knockout: bool = False
    candidate_off_topic: bool = False
    candidate_abusive: bool = False
    candidate_attempted_injection: bool = False
    candidate_wants_to_end: bool = False
    candidate_social_or_greeting: bool = False   # NEW


# Payload types and JudgeOutput follow in Task 1.6.


class AdvancePayload(BaseModel):
    kind: Literal["advance"] = "advance"
    target_question_id: str


class ProbePayload(BaseModel):
    kind: Literal["probe"] = "probe"
    probe_id: str = Field(description="Array index of follow_ups, e.g. '0', '1', '2'")
    probe_rationale: str = Field(min_length=1, max_length=200)


class ClarifyPayload(BaseModel):
    kind: Literal["clarify"] = "clarify"


class RepeatPayload(BaseModel):
    kind: Literal["repeat"] = "repeat"


class RedirectPayload(BaseModel):
    kind: Literal["redirect"] = "redirect"


class AcknowledgeNoExperiencePayload(BaseModel):
    kind: Literal["acknowledge_no_experience"] = "acknowledge_no_experience"
    failed_signal_value: str = Field(min_length=1)


class PoliteClosePayload(BaseModel):
    kind: Literal["polite_close"] = "polite_close"
    reason: str = Field(min_length=1)


class EndSessionPayload(BaseModel):
    kind: Literal["end_session"] = "end_session"
    initiated_by: Literal["candidate_initiated", "agent_initiated"]


NextActionPayload = Annotated[
    Union[
        AdvancePayload,
        ProbePayload,
        ClarifyPayload,
        RepeatPayload,
        RedirectPayload,
        AcknowledgeNoExperiencePayload,
        PoliteClosePayload,
        EndSessionPayload,
    ],
    Field(discriminator="kind"),
]


class JudgeOutput(BaseModel):
    thought: str = Field(max_length=600)
    observations: list[Observation] = Field(default_factory=list, max_length=10)
    candidate_claims: list[ClaimEntry] = Field(default_factory=list, max_length=5)
    next_action: NextAction
    next_action_payload: NextActionPayload
    turn_metadata: TurnMetadata = Field(default_factory=TurnMetadata)

    @model_validator(mode="after")
    def _check_discriminator_alignment(self) -> "JudgeOutput":
        if self.next_action.value != self.next_action_payload.kind:
            raise ValueError(
                f"next_action {self.next_action.value!r} does not match payload kind "
                f"{self.next_action_payload.kind!r}"
            )
        return self
