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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class FollowUpDimension(BaseModel):
    """A governed probe dimension the live engine composes within.

    Shared shape for generation output, the recruiter create/update bodies, and the
    read response. `listen_for` is PERMISSIVE here (may be empty) so the read path
    tolerates legacy / backfilled banks (migration 0055 wraps old string follow-ups
    with `listen_for=[]`) and recruiter-authored follow-ups. The GENERATION guarantee
    that the LLM produces a non-empty `listen_for` is enforced separately on
    `GeneratedQuestion` (see its `_follow_ups_have_listen_for` validator), NOT on this
    shared shape — mirroring the permissive `interview_runtime.schemas.FollowUpDimension`.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(..., min_length=1,
                           description="Stable slug; distinct across the whole bank.")
    intent: str = Field(..., min_length=1,
                        description="What this probe verifies — distinct from the lead and other dimensions.")
    seed_probe: str = Field(..., min_length=1, max_length=240,
                            description="A short single-ask spoken seed probe.")
    listen_for: list[str] = Field(default_factory=list, max_length=4,
                                  description="Observable specifics a strong answer to THIS dimension names. "
                                              "May be empty on legacy/backfilled or recruiter-authored rows.")


class GeneratedQuestion(BaseModel):
    """One question as returned by the LLM inside a StageQuestionBankOutput."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(..., ge=0)
    text: str = Field(
        ..., min_length=10, max_length=240,
        description=(
            "SHORT, single-focus, SPOKEN lead question (~200 chars). One ask — no "
            "'and… and…'. Depth lives in follow_ups, asked one at a time."
        ),
    )
    primary_signal: str = Field(
        ..., min_length=1,
        description=(
            "The SINGLE signal value this lead question opens. Must be one of "
            "signal_values. Makes thread-satisfaction crisp; signal_values stays "
            "the broader set the thread can cover."
        ),
    )
    signal_values: list[str] = Field(
        ..., min_length=1, max_length=3,
        description=(
            "Signal values from the pinned snapshot that this question probes. "
            "Must exactly match values in the snapshot's signals array."
        ),
    )
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool
    follow_ups: list[FollowUpDimension] = Field(..., min_length=0, max_length=3)
    positive_evidence: list[str] = Field(..., min_length=3, max_length=5)

    @field_validator("follow_ups")
    @classmethod
    def _follow_ups_have_listen_for(
        cls, value: list[FollowUpDimension]
    ) -> list[FollowUpDimension]:
        """Generation guarantee: every LLM-authored follow-up must name what to listen for.

        Enforced HERE (the LLM-output model) rather than on the shared FollowUpDimension,
        so the read/recruiter paths stay tolerant of legacy/backfilled empty `listen_for`.
        instructor surfaces this as a validation error → the generation call retries.
        """
        for fu in value:
            if not fu.listen_for:
                raise ValueError(
                    f"follow-up dimension '{fu.dimension}' must have a non-empty listen_for"
                )
        return value
    red_flags: list[str] = Field(..., min_length=2, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)
    question_kind: Literal[
        "experience_check",
        "behavioral",
        "technical_scenario",
        "compliance_binary",
        "project_deepdive",
    ] = Field(
        ...,
        description=(
            "Refined spoken taxonomy: experience_check (claim verification) · "
            "behavioral (true STAR) · technical_scenario (verbal design/depth) · "
            "compliance_binary (hard yes/no gate) · project_deepdive (the senior "
            "spine — a real project the candidate drove, probed for decision "
            "ownership and surviving orthogonal escalation)."
        ),
    )
    difficulty: Literal["easy", "medium", "hard"] | None = Field(
        default=None,
        description=(
            "Per-question difficulty the GENERATOR sets (drives the brain's grading "
            "strictness). None falls back to the stage difficulty at persistence."
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


class BankCritiqueOutput(BaseModel):
    """Critic LLM response: the corrected full bank + a short audit log.

    The critic audits the streamed draft against a fixed checklist and returns the
    CORRECTED bank (same question shape) plus a human-readable `critique` persisted to
    stage_question_banks.coverage_notes (the scoring audit trail).
    """

    model_config = ConfigDict(extra="forbid")

    critique: str = Field(
        ...,
        min_length=10,
        max_length=4000,
        description="What the critic changed and why — coverage gaps closed, anchors "
                    "sharpened, repeats removed, format/seniority fixes.",
    )
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

    text: str = Field(..., min_length=10, max_length=240)
    signal_values: list[str] = Field(..., min_length=1, max_length=3)
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool = False
    follow_ups: list[FollowUpDimension] = Field(default_factory=list, max_length=3)
    positive_evidence: list[str] = Field(default_factory=list, max_length=5)
    red_flags: list[str] = Field(default_factory=list, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)
    position: int | None = Field(default=None, ge=0)


class UpdateQuestionBody(BaseModel):
    """PATCH /questions/{id} — any subset of editable fields."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=10, max_length=240)
    signal_values: list[str] | None = Field(default=None, min_length=1, max_length=3)
    estimated_minutes: float | None = Field(default=None, gt=0, le=15)
    is_mandatory: bool | None = None
    follow_ups: list[FollowUpDimension] | None = Field(default=None, max_length=3)
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
    follow_ups: list[FollowUpDimension]
    positive_evidence: list[str]
    red_flags: list[str]
    rubric: QuestionRubric
    evaluation_hint: str
    edited_by_recruiter: bool
    question_kind: str
    primary_signal: str | None = None
    difficulty: str | None = None
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


def followups_to_jsonb(follow_ups: list[FollowUpDimension]) -> list[dict]:
    """Serialize a list of FollowUpDimension objects into JSONB-ready dicts for
    the stage_questions.follow_ups column."""
    return [fu.model_dump() for fu in follow_ups]


class BanksOverviewResponse(BaseModel):
    banks: list[BankResponse | PlaceholderBankResponse]


class GenerateResponse(BaseModel):
    """202 body returned by any generate endpoint."""

    bank_id: UUID | None = None  # null for pipeline-level generate-all
    status: BankStatus = "generating"
