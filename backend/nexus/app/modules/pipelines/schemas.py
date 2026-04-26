"""Pipeline Builder Pydantic schemas.

All enum-style fields use Literal types for strict validation.
Signal filter and pass criteria are JSONB shapes validated via nested models."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Enums ---

# Stage type v5 — see migration 0016_stage_v5_participants and
# docs/superpowers/specs/2026-04-22-pipeline-stage-types-design.md.
StageType = Literal[
    "intake",
    "phone_screen",
    "ai_screening",
    "human_interview",
    "debrief",
    "take_home",
]

ParticipantRole = Literal["interviewer", "observer", "reviewer"]


class StageParticipantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    role: ParticipantRole


class StageParticipantResponse(StageParticipantInput):
    """Adds display fields so the UI doesn't round-trip separately."""
    full_name: str
    email: str


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


# Per-type participant role gate. The backend is authoritative; the
# frontend categories helper mirrors this table.
_PARTICIPANT_ROLE_FOR_TYPE: dict[StageType, ParticipantRole | None] = {
    "intake":          None,
    "take_home":       None,
    "phone_screen":    "interviewer",
    "human_interview": "interviewer",
    "ai_screening":    "observer",
    "debrief":         "reviewer",
}


def _validate_participants_role_for_type(
    stage_type: StageType,
    participants: list[StageParticipantInput] | None,
) -> None:
    """Enforce the stage-type / participant-role contract.

    `participants=None` is the "don't touch" sentinel used by the PATCH
    auto-save path on PipelineStageUpdateInput — callers supply it when
    they want to update other fields without replacing the staffing.
    The validator treats None as a no-op.
    """
    if participants is None:
        return
    allowed = _PARTICIPANT_ROLE_FOR_TYPE[stage_type]
    if allowed is None:
        if participants:
            raise ValueError(
                f"stage_type={stage_type!r} cannot carry participants"
            )
        return
    for p in participants:
        if p.role != allowed:
            raise ValueError(
                f"stage_type={stage_type!r} only accepts role={allowed!r}, "
                f"got {p.role!r} for user_id={p.user_id}"
            )


# Field-rule enums (private to this module).
_REQUIRED = "required"
_FORBIDDEN = "forbidden"
_OPTIONAL = "optional"
_LOCKED = "locked"

_FIELD_RULES_BY_TYPE: dict[str, dict[str, str]] = {
    "intake": {
        "duration_minutes": _FORBIDDEN, "difficulty": _FORBIDDEN,
        "signal_filter": _FORBIDDEN, "pass_criteria": _LOCKED,
        "advance_behavior": _LOCKED, "sla_days": _OPTIONAL,
        "otp_required": _FORBIDDEN,
    },
    "phone_screen": {
        "duration_minutes": _REQUIRED, "difficulty": _REQUIRED,
        "signal_filter": _REQUIRED, "pass_criteria": _REQUIRED,
        "advance_behavior": _REQUIRED, "sla_days": _OPTIONAL,
        "otp_required": _OPTIONAL,
    },
    "ai_screening": {
        "duration_minutes": _REQUIRED, "difficulty": _REQUIRED,
        "signal_filter": _REQUIRED, "pass_criteria": _REQUIRED,
        "advance_behavior": _REQUIRED, "sla_days": _OPTIONAL,
        "otp_required": _OPTIONAL,
    },
    "human_interview": {
        "duration_minutes": _REQUIRED, "difficulty": _REQUIRED,
        "signal_filter": _REQUIRED, "pass_criteria": _REQUIRED,
        "advance_behavior": _REQUIRED, "sla_days": _OPTIONAL,
        "otp_required": _OPTIONAL,
    },
    "debrief": {
        "duration_minutes": _FORBIDDEN, "difficulty": _FORBIDDEN,
        "signal_filter": _FORBIDDEN, "pass_criteria": _LOCKED,
        "advance_behavior": _LOCKED, "sla_days": _OPTIONAL,
        "otp_required": _FORBIDDEN,
    },
    "take_home": {
        "duration_minutes": _FORBIDDEN, "difficulty": _FORBIDDEN,
        "signal_filter": _FORBIDDEN, "pass_criteria": _FORBIDDEN,
        "advance_behavior": _FORBIDDEN, "sla_days": _FORBIDDEN,
        "otp_required": _FORBIDDEN,
    },
}

_LOCKED_VALUES: dict[str, dict[str, Any]] = {
    "intake": {
        "pass_criteria": {"type": "all_knockouts_pass"},
        "advance_behavior": "auto_advance",
    },
    "debrief": {
        "pass_criteria": {"type": "manual_review"},
        "advance_behavior": "manual_review",
    },
}


def _validate_fields_for_stage_type(values: dict) -> dict:
    """Validate matrix-driven field rules; mutate `values` for LOCKED fields."""
    stage_type = values.get("stage_type")
    if stage_type not in _FIELD_RULES_BY_TYPE:
        return values
    rules = _FIELD_RULES_BY_TYPE[stage_type]
    locked = _LOCKED_VALUES.get(stage_type, {})
    for field, rule in rules.items():
        present = field in values and values[field] is not None
        if rule == _FORBIDDEN and present:
            raise ValueError(
                f"{field} is not allowed for stage_type='{stage_type}'"
            )
        if rule == _REQUIRED and not present:
            raise ValueError(
                f"{field} is required for stage_type='{stage_type}'"
            )
        if rule == _LOCKED:
            values[field] = locked[field]
    return values


# --- Stage schemas ---


class PipelineStageBase(BaseModel):
    position: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    stage_type: StageType
    duration_minutes: int | None = Field(default=None, gt=0, le=240)
    difficulty: StageDifficulty | None = None
    signal_filter: SignalFilter | None = None
    pass_criteria: PassCriteria | None = None
    advance_behavior: AdvanceBehavior | None = None
    # Stage SLA in days — how long a candidate can sit here before being
    # flagged stalled. Distinct from ``duration_minutes`` (the interview's
    # own length). NULL = no SLA configured.
    sla_days: int | None = Field(default=None, gt=0)
    # OTP gate — whether the candidate must supply an OTP before entering
    # the session. Only applicable to phone_screen / ai_screening /
    # human_interview (OPTIONAL for those; FORBIDDEN for all others).
    otp_required: bool | None = None


class PipelineStageInput(PipelineStageBase):
    """Stage as sent by the frontend when creating/updating."""

    model_config = ConfigDict(extra="forbid")
    participants: list[StageParticipantInput] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _apply_field_rules(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _validate_fields_for_stage_type(data)
        return data

    @model_validator(mode="after")
    def _check_participants_for_stage_type(self) -> "PipelineStageInput":
        _validate_participants_role_for_type(self.stage_type, self.participants)
        return self


class PipelineStageUpdateInput(PipelineStageInput):
    """Stage input used on UPDATE — carries optional id to preserve row identity.

    Existing stages pass their id; new stages (added via the UI "+ Add stage"
    button) omit it. The service's diff-and-sync update matches incoming items
    by id to existing rows.

    `participants=None` means "don't touch participants for this stage" —
    used by auto-save to update unrelated fields without replacing staffing.
    `participants=[]` is an explicit "clear all participants".
    """

    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    # Override the parent's `list[...]` default with an Optional to encode
    # the "don't touch" sentinel. The inherited @model_validator (via the
    # None-tolerant helper) handles both None and non-None correctly.
    participants: list[StageParticipantInput] | None = None  # type: ignore[assignment]

    @model_validator(mode="before")
    @classmethod
    def _apply_field_rules(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _validate_fields_for_stage_type(data)
        return data


class PipelineStageResponse(PipelineStageBase):
    """Stage as returned by the API.

    Templates always return `participants=[]` (templates are staffing-agnostic);
    instance stages may carry real participants.
    """

    id: UUID
    participants: list[StageParticipantResponse] = Field(default_factory=list)


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
    pipeline_version: int
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
