"""Pydantic request + response schemas for candidates, assignments, stage transitions, resume upload, and GDPR redaction.

Request models are validated at the router boundary. Response models are what the
frontend consumes directly — keep them stable. New fields on a response are additive;
removing or renaming one is a breaking change.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class CandidateSource(StrEnum):
    MANUAL = "manual"
    CSV = "csv"
    CEIPAL = "ceipal"
    GREENHOUSE = "greenhouse"
    WORKDAY = "workday"


class CandidateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(
        ...,
        min_length=3,
        max_length=200,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    phone: str | None = Field(None, max_length=50)
    location: str | None = Field(None, max_length=200)
    current_title: str | None = Field(None, max_length=200)
    linkedin_url: HttpUrl | None = None
    notes: str | None = Field(None, max_length=5000)
    source: CandidateSource = CandidateSource.MANUAL
    external_id: str | None = Field(None, max_length=200)
    source_metadata: dict | None = None


class CandidateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=200)
    phone: str | None = Field(None, max_length=50)
    location: str | None = Field(None, max_length=200)
    current_title: str | None = Field(None, max_length=200)
    linkedin_url: HttpUrl | None = None
    notes: str | None = Field(None, max_length=5000)


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str | None  # null after PII redaction
    email: str | None  # null after PII redaction
    phone: str | None
    location: str | None
    current_title: str | None
    linkedin_url: str | None
    resume_s3_key: str | None
    resume_uploaded_at: datetime | None
    notes: str | None
    source: str
    external_id: str | None
    created_at: datetime
    updated_at: datetime
    pii_redacted_at: datetime | None


class AssignmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_posting_id: UUID
    target_stage_id: UUID | None = None  # defaults to JD's first stage


class AssignmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: AssignmentStatus


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    candidate_id: UUID
    job_posting_id: UUID
    job_title: str
    current_stage_id: UUID
    current_stage_name: str
    status: AssignmentStatus
    status_changed_at: datetime
    assigned_at: datetime
    entered_at_pipeline_version: int | None = None


class StageTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_stage_id: UUID
    reason: str | None = Field(None, max_length=500)
    override: bool = False


class StageProgressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    stage_id: UUID
    entered_at: datetime
    exited_at: datetime | None
    outcome: str | None
    override: bool
    reason: str | None


class ResumeUploadUrlResponse(BaseModel):
    upload_url: str
    s3_key: str
    expires_in_seconds: int


class ResumeConfirmRequest(BaseModel):
    # The s3_key is derived server-side from candidate_id and ignored here.
    # Kept optional for backward compatibility with frontends still sending it;
    # remove once all clients have been updated.
    model_config = ConfigDict(extra="forbid")
    s3_key: str | None = Field(
        default=None,
        deprecated=True,
        description="Ignored — the backend derives the canonical key from candidate_id.",
    )


class RedactPIIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["I understand this permanently removes PII"]


class KanbanCandidateCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    candidate_id: UUID
    assignment_id: UUID
    name: str | None
    email: str | None
    status: AssignmentStatus
    current_stage_id: UUID
    latest_session_state: str | None = None  # populated in Phase 3C


class KanbanColumnResponse(BaseModel):
    stage_id: UUID
    stage_name: str
    position: int
    candidates: list[KanbanCandidateCard]


class KanbanBoardResponse(BaseModel):
    job_posting_id: UUID
    stages: list[KanbanColumnResponse]
