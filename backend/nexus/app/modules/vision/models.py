"""ORM for session_proctoring_analysis — one row per session (features only).

Stores NO frames/templates (spec §16.6/D6): only derived gaze features, the
risk band, flagged intervals, heatmap, and model_versions for auditability.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SessionProctoringAnalysis(Base):
    __tablename__ = "session_proctoring_analysis"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    risk_band: Mapped[str | None] = mapped_column(Text)
    detector_summary: Mapped[dict | None] = mapped_column(JSONB)
    gaze_heatmap: Mapped[dict | None] = mapped_column(JSONB)
    flagged_intervals: Mapped[list | None] = mapped_column(JSONB)
    gaze_signal_quality: Mapped[str | None] = mapped_column(Text)
    unscorable_pct: Mapped[float | None] = mapped_column(Numeric)
    model_versions: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    frames_analyzed: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
