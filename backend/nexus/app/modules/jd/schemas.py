"""Pydantic request / response schemas for the JD module.

These define the HTTP surface; internal ORM models live in app/models.py.
Conversions between them live in service.py and router.py."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


JobStatus = Literal[
    "draft",
    "signals_extracting",
    "signals_extraction_failed",
    "signals_extracted",
]


class SignalItemResponse(BaseModel):
    value: str
    source: Literal["ai_extracted", "ai_inferred", "recruiter"]
    inference_basis: str | None = None


class SignalSnapshotResponse(BaseModel):
    version: int
    required_skills: list[SignalItemResponse]
    preferred_skills: list[SignalItemResponse]
    must_haves: list[SignalItemResponse]
    good_to_haves: list[SignalItemResponse]
    min_experience_years: int
    seniority_level: str
    role_summary: str


class JobPostingCreate(BaseModel):
    """POST /api/jobs request body."""

    model_config = ConfigDict(extra="forbid")

    org_unit_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description_raw: str = Field(min_length=50, max_length=50_000)
    project_scope_raw: str | None = Field(default=None, max_length=20_000)
    target_headcount: int | None = Field(default=None, ge=1, le=10_000)
    deadline: date | None = None


class JobPostingSummary(BaseModel):
    """Row shape for GET /api/jobs (list view)."""

    id: UUID
    title: str
    org_unit_id: UUID
    status: JobStatus
    status_error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobPostingWithSnapshot(BaseModel):
    """Row shape for GET /api/jobs/{id} — full payload with latest snapshot."""

    id: UUID
    title: str
    org_unit_id: UUID
    description_raw: str
    project_scope_raw: str | None = None
    description_enriched: str | None = None
    status: JobStatus
    status_error: str | None = None
    target_headcount: int | None = None
    deadline: date | None = None
    created_at: datetime
    updated_at: datetime
    latest_snapshot: SignalSnapshotResponse | None = None


class JobStatusEvent(BaseModel):
    """SSE event payload shape (serialized to JSON in the event data field)."""

    job_id: UUID
    status: JobStatus
    error: str | None = None
    signal_snapshot_version: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {"signals_extracted", "signals_extraction_failed"}
