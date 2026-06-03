"""ORM model for the session_reels table (Candidate Reel feature).

One reel row per session (``session_id`` UNIQUE). Generated asynchronously by
the ``generate_session_reel`` Dramatiq actor; status transitions:

    pending → generating → ready
                         ↘ failed   (retryable via regenerate)

``generation_started_at`` + ``attempts`` make the generating state observable and
support crash recovery. Tenant isolation via the canonical RLS pair (migration
0049). Mirrors session_reports.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SessionReel(Base):
    __tablename__ = "session_reels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("1")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'pending'")
    )
    generation_error: Mapped[str | None] = mapped_column(Text)
    generation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    edl: Mapped[dict | None] = mapped_column(JSONB)
    chapters: Mapped[list | None] = mapped_column(JSONB)
    r2_key: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric)
    model_versions: Mapped[dict | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
