"""ORM model for the session_reports table (Phase 3D reporting).

One report row per session — the `session_id` column carries a UNIQUE
constraint enforced both at the DB level (migration 0047) and via
SQLAlchemy's `unique=True`.  The report is generated asynchronously by a
Dramatiq actor and transitions through status values:

    pending → generating → ready
                         ↘ failed

Tenant isolation is enforced by the canonical RLS policy pair applied in
migration 0047 (`tenant_isolation` + `service_bypass`).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SessionReport(Base):
    __tablename__ = "session_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("1")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'pending'")
    )
    generation_error: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(Text)
    verdict_reason: Mapped[str | None] = mapped_column(Text)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    overall_coverage: Mapped[float | None] = mapped_column(Numeric)
    overall_confidence: Mapped[str | None] = mapped_column(Text)
    dimension_scores: Mapped[dict | None] = mapped_column(JSONB)
    signal_scorecards: Mapped[list | None] = mapped_column(JSONB)
    question_scorecards: Mapped[list | None] = mapped_column(JSONB)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    rubric_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    scoring_manifest: Mapped[dict | None] = mapped_column(JSONB)
    human_decision: Mapped[dict | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
