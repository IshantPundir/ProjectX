"""Pipeline template and instance ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PipelineTemplate(Base):
    """Phase 2C.1 — reusable interview pipeline template per org unit.

    Templates are owned by an org unit and can be applied to jobs as
    a starting point. Editing a template does NOT affect existing job
    pipelines (jobs get snapshotted instances)."""

    __tablename__ = "pipeline_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    org_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    from_starter: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class PipelineTemplateStage(Base):
    """Ordered stage within a pipeline template."""

    __tablename__ = "pipeline_template_stages"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "position", name="uq_template_stage_position"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage_type: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: intake / debrief stages have these fields FORBIDDEN by the
    # field-rules validator (migration 0019 relaxes the DB constraint to match).
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String, nullable=True)
    signal_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pass_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    advance_behavior: Mapped[str | None] = mapped_column(String, nullable=True)
    sla_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class JobPipelineInstance(Base):
    """Per-job pipeline instance — snapshotted from a template.

    Editing an instance does NOT propagate to the source template."""

    __tablename__ = "job_pipeline_instances"
    __table_args__ = (
        UniqueConstraint("job_posting_id", name="uq_job_pipeline_instance_job"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_templates.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    pipeline_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class JobPipelineStage(Base):
    """Ordered stage within a job pipeline instance."""

    __tablename__ = "job_pipeline_stages"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "position", name="uq_job_pipeline_stage_position"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage_type: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: intake / debrief stages have these fields FORBIDDEN by the
    # field-rules validator (migration 0019 relaxes the DB constraint to match).
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String, nullable=True)
    signal_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pass_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    advance_behavior: Mapped[str | None] = mapped_column(String, nullable=True)
    sla_days: Mapped[int | None] = mapped_column(Integer)
    otp_required_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PipelineStageParticipant(Base):
    """Instance-level staffing for a pipeline stage.

    Only attached to job_pipeline_stages (instance rows) — templates are
    staffing-agnostic. Cascades on stage delete and user delete.
    """

    __tablename__ = "pipeline_stage_participants"
    __table_args__ = (
        UniqueConstraint("stage_id", "user_id", "role", name="uq_stage_user_role"),
    )

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
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # CHECK enforced at DB (ck_stage_participants_role)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
