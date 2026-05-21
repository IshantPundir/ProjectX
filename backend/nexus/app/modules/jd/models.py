"""JD pipeline ORM models."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JobPosting(Base):
    """Phase 2A — the raw-JD-to-enriched-JD-to-signals instrument.
    State machine states: draft, signals_extracting,
    signals_extraction_failed, signals_extracted. Mutations go through
    app.modules.jd.state_machine.transition()."""
    __tablename__ = "job_postings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    # Nullable: ATS-imported jobs without a matching client mapping land
    # with org_unit_id=NULL and status='blocked_pending_client_setup'. They
    # show on /jobs with a 'Not set up' chip until a recruiter links them
    # to an org_unit. See migration 0033.
    org_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    project_scope_raw: Mapped[str | None] = mapped_column(Text)
    description_enriched: Mapped[str | None] = mapped_column(Text)
    enriched_manually_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="'draft'")
    status_error: Mapped[str | None] = mapped_column(Text)
    enrichment_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="idle")
    enrichment_error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="'native'")
    external_id: Mapped[str | None] = mapped_column(Text)
    external_status: Mapped[str | None] = mapped_column(Text)
    external_last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    import_quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_last_error: Mapped[str | None] = mapped_column(Text)
    target_headcount: Mapped[int | None] = mapped_column(Integer)
    deadline: Mapped[date | None] = mapped_column(Date)
    employment_type: Mapped[str | None] = mapped_column(Text)
    work_arrangement: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    salary_range_min: Mapped[int | None] = mapped_column(Integer)
    salary_range_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(Text)
    travel_required: Mapped[str | None] = mapped_column(Text)
    start_date_pref: Mapped[str | None] = mapped_column(Text)
    interview_engine_version: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
        doc="v2 engine opt-in: NULL inherits the global default; 'v1'/'v2' overrides it.",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class JobPostingSignalSnapshot(Base):
    """Phase 2A — immutable versioned snapshot of extracted+inferred signals
    for a job posting. Written by the Dramatiq actor after a successful
    Call 1. version=1 is the initial extraction. 2B+ will populate confirmed_by/at."""
    __tablename__ = "job_posting_signal_snapshots"
    __table_args__ = (
        UniqueConstraint("job_posting_id", "version", name="uq_snapshot_job_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    job_posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    signals: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    seniority_level: Mapped[str] = mapped_column(String, nullable=False)
    role_summary: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
