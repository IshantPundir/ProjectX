"""Pydantic schemas for the question_bank module.

Three groups:
1. LLM output schemas — what `instructor` validates from the LLM response
2. API request bodies — what FastAPI endpoints accept
3. API response shapes — what endpoints return

signal_values are TEXT, not UUID, because Phase 2B signals don't have
stable UUIDs (see the spec's data model section).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

# BankStatus is the single canonical definition — re-exported from
# state_machine.py so both layers stay in sync. (B8 consolidation.)
from app.modules.question_bank.state_machine import BankStatus  # noqa: E402

QuestionSource = Literal["ai_generated", "ai_regenerated", "recruiter"]


# ---------------------------------------------------------------------------
# LLM output schemas (validated by `instructor`)
# ---------------------------------------------------------------------------

class QuestionRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excellent: str = Field(
        ..., min_length=20,
        description="Anchor for top-of-scale — what a strong answer contains",
    )
    meets_bar: str = Field(
        ..., min_length=20,
        description="Anchor for middle — what an acceptable answer contains",
    )
    below_bar: str = Field(
        ..., min_length=20,
        description="Anchor for bottom — what a weak answer looks like",
    )


class GeneratedQuestion(BaseModel):
    """One question as returned by the LLM inside a StageQuestionBankOutput."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(..., ge=0)
    text: str = Field(..., min_length=10, max_length=500)
    signal_values: list[str] = Field(
        ..., min_length=1, max_length=3,
        description=(
            "Signal values from the pinned snapshot that this question probes. "
            "Must exactly match values in the snapshot's signals array."
        ),
    )
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool
    follow_ups: list[str] = Field(..., min_length=0, max_length=3)
    positive_evidence: list[str] = Field(..., min_length=3, max_length=5)
    red_flags: list[str] = Field(..., min_length=2, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)
    question_kind: Literal[
        "technical_depth",
        "behavioral_star",
        "compliance_binary",
    ] = Field(
        ...,
        description=(
            "Which task subclass the engine routes this question to. See "
            "the common prompt §6 for selection rules. The 4th engine-side "
            "value `open_culture` is intentionally NOT in this Literal — "
            "it is a forward-compat slot the generator never emits."
        ),
    )


class StageQuestionBankOutput(BaseModel):
    """Full LLM response for one stage's bank generation.

    The bank is a STANDARDIZED TEMPLATE consumed downstream by:
      - the live screening AI (uses `text` + `follow_ups` + `signal_values`
        to drive the session and `rubric` + `positive_evidence` + `red_flags`
        for in-flight scoring)
      - the post-session report builder (cites `signal_values` and
        compares answers against the rubric anchors)

    The bank does NOT carry runtime narrative — fields like coverage notes
    and per-session summaries are produced by the screening AI / report
    builder, not by the bank-generation LLM. This keeps the bank purely
    declarative ("what we plan to test") and prevents the generator from
    self-rationalising things only the runtime knows ("what was actually
    covered and how well").
    """

    model_config = ConfigDict(extra="forbid")

    questions: list[GeneratedQuestion] = Field(..., min_length=1, max_length=15)


class SingleQuestionOutput(BaseModel):
    """LLM response for a single-question regeneration (the regen-one flow).

    Unlike the bulk output, this returns exactly one question — no wrapper.
    """

    model_config = ConfigDict(extra="forbid")

    question: GeneratedQuestion
    reasoning: str = Field(
        ..., min_length=20, max_length=500,
        description="Why this question covers the signal at the right angle",
    )


# ---------------------------------------------------------------------------
# API request bodies
# ---------------------------------------------------------------------------

class CreateQuestionBody(BaseModel):
    """POST /questions — add a hand-written custom question."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=10, max_length=500)
    signal_values: list[str] = Field(..., min_length=1, max_length=3)
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool = False
    follow_ups: list[str] = Field(default_factory=list, max_length=3)
    positive_evidence: list[str] = Field(default_factory=list, max_length=5)
    red_flags: list[str] = Field(default_factory=list, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)
    position: int | None = Field(default=None, ge=0)


class UpdateQuestionBody(BaseModel):
    """PATCH /questions/{id} — any subset of editable fields."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=10, max_length=500)
    signal_values: list[str] | None = Field(default=None, min_length=1, max_length=3)
    estimated_minutes: float | None = Field(default=None, gt=0, le=15)
    is_mandatory: bool | None = None
    follow_ups: list[str] | None = Field(default=None, max_length=3)
    positive_evidence: list[str] | None = Field(default=None, max_length=5)
    red_flags: list[str] | None = Field(default=None, max_length=3)
    rubric: QuestionRubric | None = None
    evaluation_hint: str | None = Field(default=None, min_length=10, max_length=200)
    position: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdateQuestionBody":
        """Reject an empty PATCH body.

        Without this guard, an empty `{}` request body passes Pydantic
        (all fields default to None), hits update_question(), and triggers
        auto_revert_on_edit() — silently flipping a confirmed bank back to
        reviewing with zero field changes. Force callers to send at least
        one semantically meaningful field.
        """
        provided = self.model_dump(exclude_unset=True)
        if not provided:
            raise ValueError("At least one field must be provided to update")
        return self


class ReorderBody(BaseModel):
    """PATCH /reorder — new question order as a list of UUIDs."""

    model_config = ConfigDict(extra="forbid")
    question_ids: list[UUID] = Field(..., min_length=1)


class RegenerateQuestionBody(BaseModel):
    """POST /questions/{id}/regenerate — optionally retarget to different signals."""

    model_config = ConfigDict(extra="forbid")
    replace_signal_values: list[str] | None = Field(
        default=None, min_length=1, max_length=3,
    )


# ---------------------------------------------------------------------------
# API response shapes
# ---------------------------------------------------------------------------

class QuestionResponse(BaseModel):
    id: UUID
    bank_id: UUID
    position: int
    source: QuestionSource
    text: str
    signal_values: list[str]
    estimated_minutes: float
    is_mandatory: bool
    follow_ups: list[str]
    positive_evidence: list[str]
    red_flags: list[str]
    rubric: QuestionRubric
    evaluation_hint: str
    edited_by_recruiter: bool
    created_at: datetime
    updated_at: datetime


class BankResponse(BaseModel):
    id: UUID
    stage_id: UUID
    job_posting_id: UUID
    signal_snapshot_id: UUID
    status: BankStatus
    prompt_version: str
    generation_error: str | None
    coverage_notes: str | None
    generation_status_by_kind: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per-question-kind generation status. Empty dict for legacy banks "
            "(generated before 2026-05-19) or banks not yet generated."
        ),
    )
    generated_at: datetime | None
    generated_by: UUID | None
    confirmed_at: datetime | None
    confirmed_by: UUID | None
    question_count: int       # derived, from len(questions)
    total_minutes: float      # derived, sum of estimated_minutes
    is_stale: bool            # derived, != latest confirmed snapshot
    created_at: datetime
    updated_at: datetime


class BankWithQuestionsResponse(BankResponse):
    questions: list[QuestionResponse]


class PlaceholderBankResponse(BaseModel):
    """Synthetic entry for a stage that has no bank row yet.

    The GET /questions endpoint must be idempotent — it cannot create a
    draft bank row just so the sidebar has something to render. When a
    stage has never been generated, we return this shape instead. The
    frontend keys off `status == "not_generated"` to show the "Generate"
    call-to-action.
    """

    stage_id: UUID
    status: Literal["not_generated"] = "not_generated"
    question_count: int = 0
    total_minutes: float = 0.0


class BanksOverviewResponse(BaseModel):
    banks: list[BankResponse | PlaceholderBankResponse]


class GenerateResponse(BaseModel):
    """202 body returned by any generate endpoint."""

    bank_id: UUID | None = None  # null for pipeline-level generate-all
    status: BankStatus = "generating"
