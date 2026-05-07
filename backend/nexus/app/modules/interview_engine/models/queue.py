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


class QuestionQueueSnapshot(BaseModel):
    questions: list[QuestionState] = Field(default_factory=list)
    active_index: int | None = None  # None before first question is delivered
