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
    redirect_off_topic = "redirect_off_topic"
    redirect_abusive = "redirect_abusive"
    safe_redirect_injection = "safe_redirect_injection"
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


# Payload types and JudgeOutput follow in Task 1.6.
