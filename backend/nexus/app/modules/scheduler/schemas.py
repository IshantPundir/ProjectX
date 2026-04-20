"""Scheduler module schemas — recruiter-side invite lifecycle."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class InviteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: UUID
    otp_required: bool | None = None  # None → inherit job_pipeline_stages.otp_required_default


class InviteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    session_id: UUID
    token_expires_at: datetime
