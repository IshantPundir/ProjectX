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
    TERMINATED = "terminated"


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
    proctoring_enabled: bool
    # The terminating reason when state == 'terminated' (a violation kind or
    # 'soft_threshold_exceeded'); null otherwise. Lets a reloaded wizard show
    # the candidate why their interview ended instead of the cam/mic step.
    proctoring_outcome: str | None = None


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


class AudioProcessingHints(BaseModel):
    """Browser-side audio constraints derived from server-side AIConfig.

    The frontend passes these straight into ``getUserMedia({ audio: ... })``
    or LiveKit's ``AudioCaptureOptions``. Server is the source of truth so
    the candidate session app stays dumb about deployment-mode details.

      noise_suppression=True   (browser handles light NS; no server-side NC)
      echo_cancellation=True   (load-bearing for full-duplex barge-in)
      auto_gain_control=True   (stabilizes input dynamic range)
    """
    model_config = ConfigDict(extra="forbid")
    noise_suppression: bool
    echo_cancellation: bool
    auto_gain_control: bool


ProctoringKind = Literal[
    "tab_switch",
    "focus_loss",
    "focus_abandoned",
    "fullscreen_abandoned",
    "devtools",
    "fullscreen_exit",
    "keyboard",
    # Vision proctoring (client MediaPipe, head-pose-coarse). Soft violations:
    # they count toward the shared soft-violation limit and the backend
    # terminates on escalation, same as the behavioral soft kinds. Coarse/
    # higher-false-positive than the behavioral signals — see spec D1.
    "multiple_faces",
    "face_not_visible",
    "looking_away_sustained",
]


class ProctoringConfig(BaseModel):
    """Per-tenant proctoring policy delivered to the candidate frontend
    on /start and /rejoin. enabled=False means the frontend mounts no
    proctoring listeners at all."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    soft_violation_limit: int = Field(ge=1, le=20)
    fullscreen_grace_seconds: int = Field(ge=3, le=60)


class ProctoringEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ProctoringKind
    occurred_at: datetime


class ProctoringEventResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    terminated: bool
    violation_count: int
    soft_violation_count: int
    already_terminal: bool = False


class StartSessionResponse(BaseModel):
    """200 OK shape after /start successfully provisions LiveKit + dispatches agent."""
    model_config = ConfigDict(from_attributes=True)
    livekit_url: str
    livekit_token: str
    room_name: str
    session_id: UUID
    audio_processing_hints: AudioProcessingHints
    proctoring: ProctoringConfig


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


class CandidateSessionStateResponse(BaseModel):
    """Minimal state snapshot for the candidate's fallback poll.

    Returned by GET /api/candidate-session/{token}/state. No PII —
    only the state machine value, the error_code (when state='error'),
    and the timestamp of the last state change. Used by frontend/session's
    useSessionStateFallback hook to surface engine failures when the
    LK room attribute path can't (pre-room-connect crashes).
    """
    model_config = ConfigDict(from_attributes=True)
    state: SessionState
    error_code: str | None
    state_changed_at: datetime
