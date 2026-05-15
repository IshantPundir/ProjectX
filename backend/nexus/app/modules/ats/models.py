"""ORM mappings for the ATS integration tables.

Schema source-of-truth is migration `0036_ats_unified_sync` (which supersedes
`0031_ats_core`). Under the unified-storage model, ATS-imported users and
org_units live in their primary tables (`users`, `organizational_units`)
tagged with `source` + `external_id`. The shadow tables `ats_user_mappings`
and `ats_client_mappings` no longer exist.

The classes that remain in this module are:

  - ATSConnection — per-(tenant, vendor) integration: encrypted credentials,
    cursor, timezone, sync mode.
  - ATSJobAssignment — recruiter-user ↔ job mapping with role (rename of
    the legacy `ats_job_recruiter_assignments` table).
  - ATSStageMapping — mirror-mode opt-in: external_status_label → ProjectX
    pipeline stage + action.
  - ATSAdvisoryAction — pending recruiter task surfaced when an external
    submission status changes under `advisory` mode.
  - ATSSyncLog — one row per sync run.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ATSConnection(Base):
    """Per-(tenant, vendor) ATS integration with encrypted credentials + tokens.

    Cursor model: a single `last_synced_at` TIMESTAMPTZ. NULL on a fresh
    connection — the next sync omits Ceipal's `modifiedAfter` parameter and
    pulls the full filter, which acts as the implicit first-sync. After
    every successful sync, `last_synced_at` is advanced to the
    sync_started_at value. The super-admin escape hatch `reset-cursor`
    clears it back to NULL to force a full re-scan.
    """
    __tablename__ = "ats_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "vendor", name="uq_ats_connections_tenant_vendor"),
        CheckConstraint(
            "status_sync_mode IN ('advisory', 'mirror', 'one_way')",
            name="ats_connections_status_sync_mode_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    credentials_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Sync cursor. NULL → next sync pulls the full filter (first-sync semantics).
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Captured once at first sync from any user record's `timezone` field;
    # fallback is UTC for an empty tenant. Used by the adapter to normalize
    # Ceipal's timezone-naive timestamps to UTC.
    tenant_timezone: Mapped[str | None] = mapped_column(Text)
    # Lifecycle-event mode for ATS-driven submission status changes.
    status_sync_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'advisory'")
    )
    last_poll_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_error: Mapped[str | None] = mapped_column(Text)
    rate_limit_qps: Mapped[float | None] = mapped_column(Numeric)
    job_status_filter: Mapped[dict | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSJobAssignment(Base):
    """Recruiter-user ↔ job_posting mapping for ATS-synced jobs.

    Renamed from `ats_job_recruiter_assignments` by migration 0036. The raw
    Ceipal `external_user_id` string was replaced by a real FK to `users.id`
    (now that ATS users live in the `users` table). The `role` column
    classifies which Ceipal field referenced the user: assigned_recruiter,
    primary_recruiter, posted_by, or created_by.
    """
    __tablename__ = "ats_job_assignments"
    __table_args__ = (
        UniqueConstraint(
            "job_posting_id", "user_id", "role",
            name="uq_ats_job_assignments_job_user_role",
        ),
        CheckConstraint(
            "role IN ('assigned_recruiter', 'primary_recruiter', 'posted_by', 'created_by')",
            name="ats_job_assignments_role_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSStageMapping(Base):
    """Mirror-mode: vendor-specific external_status_label → ProjectX stage.

    Empty by default. Only populated for connections that have opted into
    `status_sync_mode='mirror'`. Each row maps a Ceipal-side submission
    status string (e.g. "L2 Rejected") to a ProjectX pipeline stage + an
    action (`move_to_stage` | `reject` | `archive` | `no_op`). Advisory-mode
    and one_way-mode connections never read this table.
    """
    __tablename__ = "ats_stage_mappings"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_status_label",
            name="uq_ats_stage_mappings",
        ),
        CheckConstraint(
            "action_on_match IN ('move_to_stage', 'reject', 'archive', 'no_op')",
            name="ats_stage_mappings_action_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ats_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_status_label: Mapped[str] = mapped_column(Text, nullable=False)
    projectx_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_stages.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_on_match: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSAdvisoryAction(Base):
    """Pending recruiter task surfaced when an external submission status
    changes under `advisory` mode.

    Lifecycle:
      - `pending` — created when ats.submission.status_changed fires
      - `applied` — recruiter clicked Apply; the stage move was executed
      - `dismissed` — recruiter clicked Dismiss; no action taken
      - `superseded` — a later status change overwrote this pending action
    """
    __tablename__ = "ats_advisory_actions"
    __table_args__ = (
        CheckConstraint(
            "resolution IN ('pending', 'applied', 'dismissed', 'superseded')",
            name="ats_advisory_actions_resolution_check",
        ),
        CheckConstraint(
            "suggested_action IN ('move_to_stage', 'reject', 'archive')",
            name="ats_advisory_actions_suggested_action_check",
        ),
        Index(
            "idx_ats_advisory_actions_pending",
            "tenant_id", "assignment_id",
            postgresql_where=sql_text("resolution = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ats_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    triggering_audit_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    external_status_before: Mapped[str | None] = mapped_column(Text)
    external_status_after: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_target_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_stages.id", ondelete="CASCADE"),
    )
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'pending'")
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class ATSSyncLog(Base):
    """One row per sync run; status ∈ {running, success, partial, failed}."""
    __tablename__ = "ats_sync_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ats_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    entity_counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    error_phase: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
