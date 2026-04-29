"""SQLAlchemy ORM models.

Tables: clients, users, organizational_units, roles,
        user_role_assignments, user_invites
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Client(Base):
    """Tenant root."""
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, server_default="''")
    industry: Mapped[str] = mapped_column(Text, server_default="''")
    size: Mapped[str] = mapped_column(Text, server_default="''")
    logo_url: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String, nullable=False, server_default="trial")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    super_admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", deferrable=True, initially="DEFERRED"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    """Dashboard user — identity only. Roles live on user_role_assignments."""
    __tablename__ = "users"
    __table_args__ = (
        # Partial unique index: enforces auth_user_id uniqueness only among
        # non-soft-deleted rows. Lets a re-invitation of the same Supabase
        # Auth identity to a fresh tenant succeed after the prior tenant was
        # soft-deleted (and its users were cascade-soft-deleted). See
        # migration 0022 for the full rationale.
        Index(
            "users_auth_user_id_active_uniq",
            "auth_user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    auth_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationalUnit(Base):
    __tablename__ = "organizational_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit_type: Mapped[str] = mapped_column(String, nullable=False)
    is_root: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    company_profile: Mapped[dict | None] = mapped_column(JSONB)
    company_profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company_profile_completed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # Per-unit-type metadata (region offices, division description, team focus,
    # etc). Mapped to DB column "metadata" but exposed on the ORM as
    # `unit_metadata` because SQLAlchemy reserves `metadata` on Base for the
    # MetaData registry. API layer re-aliases to `metadata` for clients.
    unit_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    deletable_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    admin_delete_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Role(Base):
    """Role definition — system or tenant-custom."""
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="roles_unique_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default="''")
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class UserRoleAssignment(Base):
    """Junction: user assigned to org unit with a specific role."""
    __tablename__ = "user_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "org_unit_id", "role_id", name="unique_user_unit_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class UserInvite(Base):
    """Invite to join a tenant — no role info, just email + token."""
    __tablename__ = "user_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    projectx_admin_id: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user_invites.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW() + INTERVAL '72 hours'"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class AuditLog(Base):
    """Append-only audit trail for tenant-scoped mutations.

    NOTE: `tenant_id` and `actor_id` are intentionally PLAIN UUID
    columns, not ForeignKey references, so audit rows survive
    tenant/user hard-delete. See migration
    0023_tenant_hard_delete_cascade. Re-adding either FK would break
    `DELETE FROM clients` for any tenant with audit history (the
    hard-delete cascade would be blocked) and would also break
    user-deletion paths whose actor_id points at the row being
    removed. `actor_email` is denormalized so attribution queries
    keep working after the user row is gone.
    """
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    # Intentionally NOT a ForeignKey — see class docstring + migration 0023.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Intentionally NOT a ForeignKey — see class docstring + migration 0023.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class JobPosting(Base):
    """Phase 2A — the raw-JD-to-enriched-JD-to-signals instrument.
    State machine states: draft, signals_extracting,
    signals_extraction_failed, signals_extracted. Mutations go through
    app.modules.jd.state_machine.transition()."""
    __tablename__ = "job_postings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False)
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
        Boolean, nullable=False, server_default=sql_text("false")
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
    is_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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
    transcript: Mapped[list | None] = mapped_column(JSONB)
    questions_asked: Mapped[int | None] = mapped_column(Integer)
    probes_fired: Mapped[int | None] = mapped_column(Integer)
    agent_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_status: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )


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


class EngineDispatchToken(Base):
    """Phase 3C.2 — JWT minted by /start and embedded in agent dispatch metadata.

    Single-use per (jti, endpoint) is enforced by the sibling
    EngineTokenUse table. tenant_isolation + service_bypass RLS pair.
    """

    __tablename__ = "engine_dispatch_tokens"

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
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EngineTokenUse(Base):
    """Phase 3C.2 — atomic single-use record per (jti, endpoint).

    INSERT ON CONFLICT DO NOTHING is the enforcement primitive.
    Service-bypass-only RLS — no tenant_id column; tenant scope is
    inherited via the FK to engine_dispatch_tokens and re-asserted at
    the application layer via the JWT claim.

    The ``service_bypass`` policy covers both ``USING`` and ``WITH CHECK``
    so bypass-role INSERTs are also gated on the bypass flag (different
    from engine_dispatch_tokens whose service_bypass is USING-only — that
    table is INSERTed under tenant context, this one always under bypass).
    """

    __tablename__ = "engine_token_uses"
    __table_args__ = (
        CheckConstraint(
            "endpoint IN ('config', 'results')",
            name="engine_token_uses_endpoint_check",
        ),
    )

    jti: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engine_dispatch_tokens.jti", ondelete="CASCADE"),
        primary_key=True,
    )
    endpoint: Mapped[str] = mapped_column(Text, primary_key=True)
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    used_ip: Mapped[str | None] = mapped_column(INET)
