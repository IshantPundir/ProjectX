"""Question bank ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StageQuestionBank(Base):
    """Phase 2C.2 — per-stage question bank.

    1:1 with a job_pipeline_stages row (UNIQUE on stage_id). Pins the
    signal snapshot used at generation time so re-generation can detect
    drift."""

    __tablename__ = "stage_question_banks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_stages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting_signal_snapshots.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'v1'"))
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    pipeline_version_at_generation: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    stage_config_snapshot: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    generation_status_by_kind: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc=(
            "Per-question-kind generation status. Shape: "
            "{'behavioral_star': status, 'technical_depth': status}. "
            "See spec 2026-05-19-behavioral-layer-and-intro-design.md §1."
        ),
    )
    is_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    extracted_keyterms: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StageQuestion(Base):
    """Phase 2C.2 — single question within a stage question bank.

    Note: this class has a ``text`` column which would shadow the
    module-level ``text()`` SQL function within the class body, so
    server_default expressions here use the ``sql_text`` alias."""

    __tablename__ = "stage_questions"
    __table_args__ = (
        CheckConstraint(
            "question_kind IN ('technical_depth', 'behavioral_star', "
            "'compliance_binary', 'open_culture')",
            name="stage_questions_question_kind_check",
        ),
        CheckConstraint(
            "difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')",
            name="stage_questions_difficulty_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stage_question_banks.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    signal_values: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    estimated_minutes: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    follow_ups: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    positive_evidence: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    red_flags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    rubric: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluation_hint: Mapped[str] = mapped_column(Text, nullable=False)
    question_kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'technical_depth'")
    )
    difficulty: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by_recruiter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    # onupdate uses clock_timestamp() — NOT NOW() — so updated_at reflects
    # the wall-clock moment of the UPDATE rather than the transaction start.
    # Matches the Postgres trigger in migration 0017 (defense-in-depth pair).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("NOW()"),
        onupdate=sql_text("clock_timestamp()"),
    )
