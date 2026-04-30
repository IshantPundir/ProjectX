"""Candidate-facing session schemas.

Request/response models for the /api/candidate-session/{token}/* surface
plus the shared SessionState enum used on both candidate-side and
recruiter-side responses.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionState(StrEnum):
    CREATED = "created"
    PRE_CHECK = "pre_check"
    CONSENTED = "consented"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class PreCheckResponse(BaseModel):
    """Returned by GET /api/candidate-session/{token}/pre-check — describes
    the session context + where the wizard should resume."""
    model_config = ConfigDict(from_attributes=True)
    session_id: UUID
    company_name: str
    job_title: str
    stage_name: str
    duration_minutes: int
    consent_text: str
    state: SessionState
    otp_required: bool
    otp_verified_at: datetime | None
    # Timestamp of the most recent OTP issuance. Frontend uses this to restore
    # the 60s [Send code] cooldown after a page reload.
    otp_issued_at: datetime | None


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consented: Literal[True]
    user_agent: str = Field(..., min_length=1, max_length=500)


class VerifyOtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., pattern=r"^\d{6}$")


class VerifyOtpErrorResponse(BaseModel):
    """Shape for 422 responses when OTP verification fails but retries remain."""
    code: Literal["INVALID_OTP", "OTP_EXPIRED", "OTP_MAX_ATTEMPTS_REACHED"]
    detail: str
    attempts_remaining: int


class StartSessionResponse(BaseModel):
    """200 OK shape after /start successfully provisions LiveKit + dispatches agent."""
    model_config = ConfigDict(from_attributes=True)
    livekit_url: str
    livekit_token: str
    room_name: str
    session_id: UUID


class SessionDetailResponse(BaseModel):
    """Recruiter-side session detail view."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    assignment_id: UUID
    stage_id: UUID
    stage_name: str
    state: SessionState
    state_changed_at: datetime
    otp_required: bool
    consent_recorded_at: datetime | None
    scheduled_for: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class SessionListPage(BaseModel):
    items: list[SessionDetailResponse]
    total: int
    offset: int
    limit: int
