"""Pydantic request / response schemas for the JD module.

These define the HTTP surface; internal ORM models live in app/models.py.
Conversions between them live in service.py and router.py.

Signal Schema v2: universal flat list with type, priority, weight,
knockout, stage, evaluation_method, and provenance metadata."""

from datetime import date, datetime
from functools import cache
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Enum-style Literal types
# ---------------------------------------------------------------------------

JobStatus = Literal[
    "draft",
    "signals_extracting",
    "signals_extraction_failed",
    "signals_extracted",
    "signals_confirmed",
    "pipeline_built",
    "active",
    "archived",
]

EnrichmentStatus = Literal["idle", "streaming", "completed", "failed"]

SignalType = Literal["competency", "experience", "credential", "behavioral"]
SignalPriority = Literal["required", "preferred"]
SignalStage = Literal["screen", "interview"]
EvaluationMethod = Literal[
    "verbal_response",
    "code_exercise",
    "scenario_walkthrough",
    "credential_verify",
    "behavioral_question",
]

EmploymentType = Literal[
    "full_time", "part_time", "contract", "contract_to_hire", "internship",
]
WorkArrangement = Literal["onsite", "remote", "hybrid"]
SalaryCurrency = Literal["USD", "EUR", "GBP", "INR", "CAD", "AUD"]
TravelRequired = Literal["none", "occasional", "moderate", "extensive"]
StartDatePref = Literal["immediate", "within_30_days", "within_60_days", "flexible"]
SeniorityLevel = Literal["junior", "mid", "senior", "lead", "principal"]

# ---------------------------------------------------------------------------
# Evaluation method defaults — (type, stage) → default method
# ---------------------------------------------------------------------------

_EVALUATION_DEFAULTS: dict[tuple[str, str], EvaluationMethod] = {
    ("competency", "screen"): "verbal_response",
    ("competency", "interview"): "code_exercise",
    ("experience", "screen"): "verbal_response",
    ("experience", "interview"): "scenario_walkthrough",
    ("credential", "screen"): "credential_verify",
    ("credential", "interview"): "credential_verify",
    ("behavioral", "screen"): "behavioral_question",
    ("behavioral", "interview"): "behavioral_question",
}


@cache
def default_evaluation_method(
    signal_type: SignalType, stage: SignalStage,
) -> EvaluationMethod:
    """Return the default evaluation method for a (type, stage) pair.

    Pure function over a small fixed lookup table — `functools.cache`
    makes that purity machine-verifiable and lets future readers know
    the output is stable for any given (type, stage) input.

    **Cross-module consumers** (in addition to this module's own
    `jd/router.py::_snapshot_to_response`):

    - ``app.modules.interview_runtime.service._project_signal_metadata``
      (Phase A.1 onwards) — uses this to fill `evaluation_method` when
      projecting `JobPostingSignalSnapshot.signals` JSONB into
      `SessionConfig.signal_metadata`. Initial-extraction snapshots
      persist `SignalItemV2` dumps which lack `evaluation_method`;
      this default is the read-time backstop.

    Renaming or deprecating this function requires updating both
    call sites — `interview_runtime` will refuse to start sessions if
    the import breaks, but a silent semantic change here would
    propagate into the structured agent's signal-evaluation routing.
    """
    return _EVALUATION_DEFAULTS.get(
        (signal_type, stage), "verbal_response",
    )


# ---------------------------------------------------------------------------
# Signal item schemas
# ---------------------------------------------------------------------------

class SignalItemResponse(BaseModel):
    """Signal as returned by the API (read-only, fully resolved)."""

    value: str
    type: SignalType
    priority: SignalPriority
    weight: Literal[1, 2, 3]
    knockout: bool
    stage: SignalStage
    evaluation_method: EvaluationMethod
    evaluation_hint: str | None = None
    source: Literal["ai_extracted", "ai_inferred", "recruiter"]
    inference_basis: str | None = None


class SignalItemInput(BaseModel):
    """Signal as sent by the frontend (evaluation_method nullable — server fills default)."""

    value: str = Field(min_length=1)
    type: SignalType
    priority: SignalPriority
    weight: Literal[1, 2, 3] = 2
    knockout: bool = False
    stage: SignalStage
    evaluation_method: EvaluationMethod | None = None
    evaluation_hint: str | None = None
    source: Literal["ai_extracted", "ai_inferred", "recruiter"]
    inference_basis: str | None = None

    @model_validator(mode="after")
    def check_provenance(self) -> "SignalItemInput":
        if self.source == "ai_inferred" and not self.inference_basis:
            raise ValueError(
                "Signal with source='ai_inferred' must have an inference_basis"
            )
        if self.source in ("ai_extracted", "recruiter") and self.inference_basis is not None:
            raise ValueError(
                f"Signal with source='{self.source}' must have inference_basis=null"
            )
        return self


# ---------------------------------------------------------------------------
# Snapshot schema
# ---------------------------------------------------------------------------

class SignalSnapshotResponse(BaseModel):
    """Versioned signal snapshot returned on job detail."""

    signals: list[SignalItemResponse]
    seniority_level: str
    role_summary: str
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    version: int


# ---------------------------------------------------------------------------
# Job posting schemas
# ---------------------------------------------------------------------------

class JobPostingCreate(BaseModel):
    """POST /api/jobs request body."""

    model_config = ConfigDict(extra="forbid")

    org_unit_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description_raw: str = Field(min_length=50, max_length=50_000)
    project_scope_raw: str | None = Field(default=None, max_length=20_000)
    target_headcount: int | None = Field(default=None, ge=1, le=10_000)
    deadline: date | None = None
    employment_type: EmploymentType | None = None
    work_arrangement: WorkArrangement | None = None
    location: str | None = Field(default=None, max_length=500)
    salary_range_min: int | None = Field(default=None, ge=0)
    salary_range_max: int | None = Field(default=None, ge=0)
    salary_currency: SalaryCurrency | None = None
    travel_required: TravelRequired | None = None
    start_date_pref: StartDatePref | None = None
    skip_enrichment: bool = Field(
        default=False,
        description=(
            "If true, signal extraction runs against the raw JD; "
            "JD enrichment phase is skipped entirely."
        ),
    )


class SaveSignalsRequest(BaseModel):
    """PUT /api/jobs/{id}/signals — save edited signals."""

    signals: list[SignalItemInput]
    seniority_level: SeniorityLevel
    role_summary: str = Field(min_length=10, max_length=2000)


class JobPostingSummary(BaseModel):
    """Row shape for GET /api/jobs (list view)."""

    id: UUID
    title: str
    org_unit_id: UUID
    org_unit_name: str | None = None
    created_by_email: str | None = None
    updated_by_email: str | None = None
    status: JobStatus
    status_error: str | None = None
    created_at: datetime
    updated_at: datetime
    # Aggregate fields derived from the latest signal snapshot. Both default
    # to 0 for jobs that don't have a snapshot yet (draft / extracting).
    # `needs_review_count` mirrors the UI's needs-review heuristic on the
    # JD Review page: AI-inferred signals with weight < 2 are flagged as
    # "double-check". Exposing it on the list row lets the jobs index
    # surface the design's "2 signals to double-check" inline hint without
    # fetching per-row details.
    signal_count: int = 0
    needs_review_count: int = 0


class JobPostingWithSnapshot(BaseModel):
    """Row shape for GET /api/jobs/{id} — full payload with latest snapshot."""

    id: UUID
    title: str
    org_unit_id: UUID
    # Enrichment fields — shared with JobPostingSummary so list and detail
    # responses are field-complete and identical in shape for these columns.
    org_unit_name: str | None = None
    created_by_email: str | None = None
    updated_by_email: str | None = None
    signal_count: int = 0
    needs_review_count: int = 0
    description_raw: str
    project_scope_raw: str | None = None
    description_enriched: str | None = None
    status: JobStatus
    status_error: str | None = None
    target_headcount: int | None = None
    deadline: date | None = None
    employment_type: str | None = None
    work_arrangement: str | None = None
    location: str | None = None
    salary_range_min: int | None = None
    salary_range_max: int | None = None
    salary_currency: str | None = None
    travel_required: str | None = None
    start_date_pref: str | None = None
    created_at: datetime
    updated_at: datetime
    latest_snapshot: SignalSnapshotResponse | None = None
    enrichment_status: EnrichmentStatus = "idle"
    enrichment_error: str | None = None
    is_confirmed: bool = False
    can_manage: bool = False


class JobStatusEvent(BaseModel):
    """SSE event payload shape (serialized to JSON in the event data field)."""

    job_id: UUID
    status: JobStatus
    error: str | None = None
    signal_snapshot_version: int | None = None
    enrichment_status: EnrichmentStatus = "idle"
    is_confirmed: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            "signals_extracted",
            "signals_extraction_failed",
            "signals_confirmed",
        }
