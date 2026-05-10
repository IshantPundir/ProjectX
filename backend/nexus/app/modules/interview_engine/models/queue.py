"""QuestionQueue Pydantic models — per-question state machine."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class QuestionStatus(StrEnum):
    pending = "pending"
    active = "active"
    completed = "completed"
    skipped = "skipped"  # only legal for non-mandatory questions


class QuestionState(BaseModel):
    question_id: str
    position: int = Field(ge=0)
    is_mandatory: bool
    status: QuestionStatus
    main_asked_at_turn: int | None = None
    probes_asked_ids: list[str] = Field(default_factory=list)
    probes_remaining_ids: list[str] = Field(default_factory=list)
    anchors_hit_ids: list[int] = Field(default_factory=list)
    time_spent_ms: int = Field(ge=0, default=0)
    turn_count: int = Field(ge=0, default=0)
    push_back_count: int = Field(
        ge=0,
        default=0,
        description=(
            "Number of push_back actions the State Engine has applied to "
            "this question. Hard-capped at 2: the engine downgrades a 3rd "
            "incoming push_back to advance to prevent loops on candidates "
            "who genuinely cannot give specifics. Surfaced to the Judge "
            "via JudgeInputPayload.active_question_push_back_count."
        ),
    )
    consecutive_dont_know_count: int = Field(
        ge=0,
        default=0,
        description=(
            "Number of consecutive candidate utterances on this question "
            "that match the 'I don't know' family (regex in "
            "state/engine.py::_DONT_KNOW_PATTERNS). Reset to 0 when the "
            "candidate gives any other utterance OR when the question "
            "advances. Surfaced to the Judge via "
            "JudgeInputPayload.active_question_consecutive_dont_know_count "
            "so the Judge can escalate to acknowledge_no_experience after "
            "the first 'I don't know' on an experience-class signal "
            "instead of looping on clarify."
        ),
    )
    quality_observations: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Cumulative count of observations on this question by quality "
            "grade (`thin` / `concrete` / `strong`). Used by the State "
            "Engine to gate `advance` (must have at least one >= concrete) "
            "and surfaced to the Report Builder for downstream analytics."
        ),
    )


class QuestionQueueSnapshot(BaseModel):
    questions: list[QuestionState] = Field(default_factory=list)
    active_index: int | None = None  # None before first question is delivered
