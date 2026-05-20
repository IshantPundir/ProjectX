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
    still_confused_count: int = Field(
        ge=0,
        default=0,
        description=(
            "Consecutive turns on this question where the Judge set "
            "turn_metadata.candidate_still_confused=true (generic confusion "
            "/ cannot engage). Reset to 0 on any other turn or on advance. "
            "The State Engine escalates to acknowledge-and-advance once this "
            "reaches 2 (i.e. on the 3rd consecutive confusion). Surfaced to "
            "the Judge via JudgeInputPayload.active_question_still_confused_count."
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
    signal_values: list[str] = Field(
        default_factory=list,
        description=(
            "Signal values this question targets. Used by the non-mandatory "
            "question selector (Cluster G) to check whether any of the "
            "question's signals still have uncovered coverage, gating "
            "whether the non-mandatory question should be asked."
        ),
    )


class QuestionQueueSnapshot(BaseModel):
    questions: list[QuestionState] = Field(default_factory=list)
    active_index: int | None = None  # None before first question is delivered
