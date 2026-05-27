"""Candidate interview session ORM models.

The historical EngineDispatchToken / EngineTokenUse classes were retired
in Phase 3 of the modular-monolith uplift (alembic 0025) — the engine
no longer mints a JWT or reaches over HTTP, so those tables and their
ORM mirrors are gone.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Session(Base):
    """Phase 3C: candidate interview session.

    Upgraded from the Phase 2A stub. Represents one invitation + pre-check +
    (future) LiveKit interview attempt against a specific candidate_job_assignment
    at a specific stage.
    """
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_pipeline_stages.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'created'"))
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    consent_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    otp_hash: Mapped[str | None] = mapped_column(Text)
    otp_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    otp_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    livekit_room_name: Mapped[str | None] = mapped_column(Text)
    recording_s3_key: Mapped[str | None] = mapped_column(Text)
    raw_result_json: Mapped[dict | None] = mapped_column(JSONB)
    knockout_failures: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    audio_tuning_summary: Mapped[dict | None] = mapped_column(JSONB)
    engine_checkpoint: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
    )
    transcript: Mapped[list | None] = mapped_column(JSONB)
    questions_asked: Mapped[int | None] = mapped_column(Integer)
    probes_fired: Mapped[int | None] = mapped_column(Integer)
    agent_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Liveness pulse written periodically by the running engine (see agent.py
    # heartbeat task). The reaper treats a session as alive while this is fresh,
    # so a legitimately long interview is never reaped; a dead engine stops
    # pulsing and is reaped once the pulse goes stale. NULL until the first beat.
    last_engine_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    result_status: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    proctoring_violations: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    proctoring_outcome: Mapped[str | None] = mapped_column(Text)
    proctoring_violation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )


class CandidateSessionToken(Base):
    """Single-use candidate JWT tracking — atomic used_at UPDATE enforces single-use.

    One row minted per invite/resend. The JWT's `jti` claim is this row's PK.
    `used_at` is set exactly once by `POST /api/candidate-session/{token}/start`
    via an atomic `UPDATE … WHERE used_at IS NULL RETURNING`. Replay → zero rows → 409.
    """
    __tablename__ = "candidate_session_tokens"

    jti: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_ip: Mapped[str | None] = mapped_column(INET)
    used_user_agent: Mapped[str | None] = mapped_column(Text)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_session_tokens.jti")
    )
