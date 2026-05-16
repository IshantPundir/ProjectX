"""Candidate identity + assignment + stage-progress ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Candidate(Base):
    """Phase 3B — candidate identity. PII-bearing; scoped per tenant.

    ``source`` records the origin channel (``manual`` / ``ats_ceipal`` /
    ``ats_greenhouse`` / …). ``pii_redacted_at`` marks GDPR-compliant
    soft-erasure — service-layer enforcement decides which fields are
    nulled out on redaction."""

    __tablename__ = "candidates"
    __table_args__ = (
        # Partial unique index — matches migration 0013_candidates_core.
        # Declared on the ORM so Base.metadata.create_all builds it in the
        # test DB too (tests don't run alembic), keeping the duplicate-email
        # constraint enforceable in unit tests.
        Index(
            "candidates_tenant_email_active_idx",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=sql_text("pii_redacted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    # name/email are nullable so `redact_pii` can wipe them while preserving
    # the row for audit-trail linkage. Active-candidate uniqueness is guarded
    # by the partial unique index on (tenant_id, email) WHERE pii_redacted_at IS NULL.
    name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    current_title: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    resume_s3_key: Mapped[str | None] = mapped_column(Text)
    resume_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    pii_redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pii_redacted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )


class CandidateJobAssignment(Base):
    """Phase 3B — links a candidate to a job posting.

    One candidate can be assigned to multiple jobs but at most once per
    job (UNIQUE on (candidate_id, job_posting_id)). ``current_stage_id``
    points at the stage the candidate is sitting in right now."""

    __tablename__ = "candidate_job_assignments"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "job_posting_id",
            name="candidate_job_assignments_unique_candidate_job",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
    # Mirrors migration 0031 (`candidate_job_assignments_external_idx` partial
    # unique index). The columns identify ATS-origin assignments so the
    # importer can upsert-by-external-id without colliding with manual rows.
    # Declared on the ORM so Base.metadata.create_all (test DB) builds the
    # columns — without these, importer tests fail with
    # "column candidate_job_assignments.source does not exist".
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'manual'"),
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    # External lifecycle tracking. Populated whenever an ATS sync has
    # claimed the row — regardless of whether ``source`` is ``'manual'``
    # (recruiter created it first; ATS later attached an external_id) or
    # ``'ats_*'`` (created by the sync directly). See migration 0036.
    # Drives the kanban indicator + advisory-action flow.
    external_status: Mapped[str | None] = mapped_column(Text)
    external_pipeline_status: Mapped[str | None] = mapped_column(Text)
    external_last_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    current_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_pipeline_stages.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'active'")
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    entered_at_pipeline_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )


class CandidateStageProgress(Base):
    """Phase 3B — per-stage trail for an assignment.

    One row per stage the candidate has been in for a given assignment.
    ``exited_at`` null = currently sitting in that stage. ``override=true``
    marks a manual stage move that skipped normal advance criteria."""

    __tablename__ = "candidate_stage_progress"

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
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)
    moved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    reason: Mapped[str | None] = mapped_column(Text)
