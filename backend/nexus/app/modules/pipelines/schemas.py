"""Pipeline Builder Pydantic schemas.

All enum-style fields use Literal types for strict validation.
Signal filter and pass criteria are JSONB shapes validated via nested models."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Enums ---

StageType = Literal[
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
]

StageDifficulty = Literal["easy", "medium", "hard"]
AdvanceBehavior = Literal["auto_advance", "manual_review"]

# --- Signal filter ---

SignalFilterType = Literal["competency", "experience", "credential", "behavioral"]


class SignalFilter(BaseModel):
    """Which signal types this stage probes.

    Every stage in the pipeline can probe every signal — there is no
    stage-level filtering by weight, priority, or signal origin stage.
    Question-generation at runtime (Phase 2C.2) will allocate probe
    time across signals based on weight × priority × stage depth.

    The one dimension here is `include_types`, because some types
    genuinely do not belong in every stage:

    - `credential` is verified via documents, not live interviews
    - `behavioral` is best probed by humans, not AI stages

    Recruiters narrow this per stage to reflect that reality.
    """
    model_config = ConfigDict(extra="forbid")

    include_types: list[SignalFilterType]


# --- Pass criteria (discriminated union) ---


class PassCriteriaKnockout(BaseModel):
    type: Literal["all_knockouts_pass"]


class PassCriteriaThreshold(BaseModel):
    type: Literal["score_threshold"]
    threshold: int = Field(ge=0, le=100)


class PassCriteriaManual(BaseModel):
    type: Literal["manual_review"]


PassCriteria = PassCriteriaKnockout | PassCriteriaThreshold | PassCriteriaManual


# --- Stage schemas ---


class PipelineStageBase(BaseModel):
    position: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    stage_type: StageType
    duration_minutes: int = Field(gt=0, le=240)
    difficulty: StageDifficulty
    signal_filter: SignalFilter
    pass_criteria: PassCriteria
    advance_behavior: AdvanceBehavior


class PipelineStageInput(PipelineStageBase):
    """Stage as sent by the frontend when creating/updating."""

    model_config = ConfigDict(extra="forbid")


class PipelineStageUpdateInput(PipelineStageInput):
    """Stage input used on UPDATE — carries optional id to preserve row identity.

    Existing stages pass their id; new stages (added via the UI "+ Add stage"
    button) omit it. The service's diff-and-sync update matches incoming items
    by id to existing rows.
    """

    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None


class PipelineStageResponse(PipelineStageBase):
    """Stage as returned by the API."""

    id: UUID


# --- Template schemas ---


class PipelineTemplateResponse(BaseModel):
    id: UUID
    org_unit_id: UUID
    name: str
    description: str | None = None
    is_default: bool
    from_starter: str | None = None
    stages: list[PipelineStageResponse]
    created_at: datetime
    updated_at: datetime


class CreateTemplateFromScratch(BaseModel):
    source: Literal["scratch"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_default: bool = False
    stages: list[PipelineStageInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_positions_sequential(self) -> "CreateTemplateFromScratch":
        positions = sorted(s.position for s in self.stages)
        if positions != list(range(len(positions))):
            raise ValueError("stage positions must be sequential starting at 0")
        return self


class CreateTemplateFromStarter(BaseModel):
    source: Literal["starter"]
    starter_key: str
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_default: bool = False


CreateTemplateRequest = CreateTemplateFromScratch | CreateTemplateFromStarter


class UpdateTemplateRequest(BaseModel):
    """Partial update — all fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    stages: list[PipelineStageInput] | None = None

    @model_validator(mode="after")
    def check_positions_if_stages_provided(self) -> "UpdateTemplateRequest":
        if self.stages is not None:
            positions = sorted(s.position for s in self.stages)
            if positions != list(range(len(positions))):
                raise ValueError("stage positions must be sequential starting at 0")
        return self


# --- Starter pack response ---


class StarterTemplate(BaseModel):
    key: str
    name: str
    description: str
    stages: list[PipelineStageBase]


# --- Job pipeline instance ---


class JobPipelineInstanceResponse(BaseModel):
    id: UUID
    job_posting_id: UUID
    source_template_id: UUID | None = None
    source_template_name: str | None = None
    stages: list[PipelineStageResponse]
    created_at: datetime
    updated_at: datetime


class CreateJobPipelineFromTemplate(BaseModel):
    source: Literal["template"]
    template_id: UUID


class CreateJobPipelineFromStarter(BaseModel):
    source: Literal["starter"]
    starter_key: str


class CreateJobPipelineFromScratch(BaseModel):
    source: Literal["scratch"]
    stages: list[PipelineStageInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_positions_sequential(self) -> "CreateJobPipelineFromScratch":
        positions = sorted(s.position for s in self.stages)
        if positions != list(range(len(positions))):
            raise ValueError("stage positions must be sequential starting at 0")
        return self


CreateJobPipelineRequest = (
    CreateJobPipelineFromTemplate
    | CreateJobPipelineFromStarter
    | CreateJobPipelineFromScratch
)


class UpdateJobPipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[PipelineStageUpdateInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_positions_sequential(self) -> "UpdateJobPipelineRequest":
        positions = sorted(s.position for s in self.stages)
        if positions != list(range(len(positions))):
            raise ValueError("stage positions must be sequential starting at 0")
        return self


class SaveAsTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_default: bool = False
