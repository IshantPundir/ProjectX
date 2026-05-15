"""ats_unified_sync

Revision ID: 0036
Revises: 0035
Create Date: 2026-05-14

Single consolidated migration for the job-scoped ATS sync refactor. Source
spec: docs/superpowers/specs/2026-05-14-job-scoped-ats-sync-design.md.

Because the codebase has no live production tenants, this is a clean
cutover — no transitional columns, no data backfill, no shadow-table
deprecation window. The migration ships the final target schema directly:

  - users: nullable auth_user_id + source/external_id/external_source_metadata
  - organizational_units: source/external_id/external_source_metadata
  - job_postings: change-tracking + quarantine columns
  - candidate_job_assignments: external_status + external_pipeline_status +
    external_last_modified_at
  - ats_connections: last_synced_at + tenant_timezone + status_sync_mode;
    drop the vestigial scheduler columns (last_synced_cursors,
    poll_interval_seconds, next_poll_at, poll_lock_acquired_at)
  - drop ats_user_mappings + ats_client_mappings entirely (replaced by
    users.external_id + organizational_units.external_id)
  - rename ats_job_recruiter_assignments → ats_job_assignments with a real
    user_id FK + role column
  - new tables: ats_stage_mappings + ats_advisory_actions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def _apply_canonical_rls(table: str) -> None:
    """Canonical tenant_isolation + service_bypass pair with NULLIF-wrapped
    current_tenant cast. Mirrors helper in 0031_ats_core."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table}
          USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)
    op.execute(f"""
        CREATE POLICY service_bypass ON {table}
          USING (current_setting('app.bypass_rls', true) = 'true');
    """)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexus_app;")


def upgrade() -> None:
    # ─── users: unify ATS provenance into the row ─────────────────────────
    # auth_user_id becomes nullable so ATS-imported users (no Supabase auth
    # account yet) can live in this table. The partial-unique index from 0022
    # keeps working — multiple NULLs are allowed by partial unique indexes.
    op.alter_column("users", "auth_user_id", nullable=True)
    op.add_column(
        "users",
        sa.Column("source", sa.Text(), nullable=False, server_default="native"),
    )
    op.add_column("users", sa.Column("external_id", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("external_source_metadata", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "users_source_external_id_check",
        "users",
        "(source = 'native') OR (source LIKE 'ats_%' AND external_id IS NOT NULL)",
    )
    op.create_index(
        "users_external_identity_uniq",
        "users",
        ["tenant_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    # ─── organizational_units: same unification pattern ───────────────────
    # NOTE: organizational_units uses `client_id` as its tenant column (not
    # `tenant_id`) — historic naming. The unique index uses client_id.
    op.add_column(
        "organizational_units",
        sa.Column("source", sa.Text(), nullable=False, server_default="native"),
    )
    op.add_column(
        "organizational_units",
        sa.Column("external_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "organizational_units",
        sa.Column("external_source_metadata", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "org_units_source_external_id_check",
        "organizational_units",
        "(source = 'native') OR (source LIKE 'ats_%' AND external_id IS NOT NULL)",
    )
    op.create_index(
        "org_units_external_identity_uniq",
        "organizational_units",
        ["client_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    # ─── job_postings: change-tracking + quarantine ───────────────────────
    op.add_column(
        "job_postings",
        sa.Column("external_last_modified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "job_postings_ats_modified",
        "job_postings",
        ["tenant_id", "source", "external_last_modified_at"],
        postgresql_where=sa.text("source LIKE 'ats_%'"),
    )
    op.add_column(
        "job_postings",
        sa.Column(
            "import_retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "job_postings",
        sa.Column("import_quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_postings",
        sa.Column("import_last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "job_postings_quarantined",
        "job_postings",
        ["tenant_id"],
        postgresql_where=sa.text("import_quarantined_at IS NOT NULL"),
    )

    # ─── candidate_job_assignments: external lifecycle tracking ───────────
    op.add_column(
        "candidate_job_assignments",
        sa.Column("external_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "candidate_job_assignments",
        sa.Column("external_pipeline_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "candidate_job_assignments",
        sa.Column("external_last_modified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ─── ats_connections: cursor + tz + sync-mode + drop vestigial cols ───
    op.add_column(
        "ats_connections",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ats_connections",
        sa.Column("tenant_timezone", sa.Text(), nullable=True),
    )
    op.add_column(
        "ats_connections",
        sa.Column(
            "status_sync_mode",
            sa.Text(),
            nullable=False,
            server_default="advisory",
        ),
    )
    op.create_check_constraint(
        "ats_connections_status_sync_mode_check",
        "ats_connections",
        "status_sync_mode IN ('advisory', 'mirror', 'one_way')",
    )
    # Drop the vestigial scheduler columns introduced by 0031. The cron
    # scheduler is deferred to a future spec; that work will add its own
    # column set when it lands.
    op.drop_index("ix_ats_connections_due", table_name="ats_connections")
    op.drop_column("ats_connections", "poll_lock_acquired_at")
    op.drop_column("ats_connections", "next_poll_at")
    op.drop_column("ats_connections", "poll_interval_seconds")
    op.drop_column("ats_connections", "last_synced_cursors")

    # ─── Drop legacy shadow tables ────────────────────────────────────────
    # ats_user_mappings is replaced by users.source + users.external_id.
    # ats_client_mappings is replaced by organizational_units.source +
    # organizational_units.external_id.
    op.drop_table("ats_user_mappings")
    op.drop_table("ats_client_mappings")

    # ─── Rename + refactor ats_job_recruiter_assignments ──────────────────
    op.drop_constraint(
        "uq_ats_job_recruiter_assignments",
        "ats_job_recruiter_assignments",
        type_="unique",
    )
    op.rename_table("ats_job_recruiter_assignments", "ats_job_assignments")
    # The raw external_user_id string is replaced by a real FK to users.id.
    # ats_vendor is no longer needed — users.source carries that information.
    op.drop_column("ats_job_assignments", "external_user_id")
    op.drop_column("ats_job_assignments", "ats_vendor")
    op.add_column(
        "ats_job_assignments",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.add_column(
        "ats_job_assignments",
        sa.Column("role", sa.Text(), nullable=False),
    )
    op.create_check_constraint(
        "ats_job_assignments_role_check",
        "ats_job_assignments",
        "role IN ('assigned_recruiter', 'primary_recruiter', 'posted_by', 'created_by')",
    )
    op.create_unique_constraint(
        "uq_ats_job_assignments_job_user_role",
        "ats_job_assignments",
        ["job_posting_id", "user_id", "role"],
    )

    # ─── New table: ats_stage_mappings (mirror-mode opt-in) ───────────────
    op.create_table(
        "ats_stage_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_status_label", sa.Text(), nullable=False),
        sa.Column("projectx_stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_on_match", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["ats_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["projectx_stage_id"], ["job_pipeline_stages.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "action_on_match IN ('move_to_stage', 'reject', 'archive', 'no_op')",
            name="ats_stage_mappings_action_check",
        ),
        sa.UniqueConstraint(
            "connection_id",
            "external_status_label",
            name="uq_ats_stage_mappings",
        ),
    )
    _apply_canonical_rls("ats_stage_mappings")

    # ─── New table: ats_advisory_actions ──────────────────────────────────
    op.create_table(
        "ats_advisory_actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("triggering_audit_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_status_before", sa.Text(), nullable=True),
        sa.Column("external_status_after", sa.Text(), nullable=False),
        sa.Column(
            "suggested_target_stage_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("suggested_action", sa.Text(), nullable=False),
        sa.Column(
            "resolution",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["ats_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["candidate_job_assignments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suggested_target_stage_id"],
            ["job_pipeline_stages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "resolution IN ('pending', 'applied', 'dismissed', 'superseded')",
            name="ats_advisory_actions_resolution_check",
        ),
        sa.CheckConstraint(
            "suggested_action IN ('move_to_stage', 'reject', 'archive')",
            name="ats_advisory_actions_suggested_action_check",
        ),
    )
    op.create_index(
        "idx_ats_advisory_actions_pending",
        "ats_advisory_actions",
        ["tenant_id", "assignment_id"],
        postgresql_where=sa.text("resolution = 'pending'"),
    )
    _apply_canonical_rls("ats_advisory_actions")


def downgrade() -> None:
    # ─── Drop new tables ──────────────────────────────────────────────────
    op.drop_index(
        "idx_ats_advisory_actions_pending", table_name="ats_advisory_actions"
    )
    op.drop_table("ats_advisory_actions")

    op.drop_table("ats_stage_mappings")

    # ─── Recreate ats_user_mappings ───────────────────────────────────────
    op.create_table(
        "ats_user_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column("external_user_email", sa.Text(), nullable=False),
        sa.Column("external_user_display_name", sa.Text(), nullable=False),
        sa.Column("external_user_role", sa.Text()),
        sa.Column("external_user_status", sa.Text()),
        sa.Column("external_user_metadata", postgresql.JSONB()),
        sa.Column("internal_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("mapped_at", sa.DateTime(timezone=True)),
        sa.Column("mapped_by", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["internal_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["mapped_by"], ["users.id"]),
        sa.UniqueConstraint(
            "tenant_id",
            "ats_vendor",
            "external_user_id",
            name="uq_ats_user_mappings_external",
        ),
    )
    _apply_canonical_rls("ats_user_mappings")

    # ─── Recreate ats_client_mappings ─────────────────────────────────────
    op.create_table(
        "ats_client_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_client_id", sa.Text(), nullable=False),
        sa.Column("external_client_name", sa.Text(), nullable=False),
        sa.Column("org_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB()),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_unit_id"], ["organizational_units.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "ats_vendor",
            "external_client_id",
            name="uq_ats_client_mappings_external",
        ),
    )
    _apply_canonical_rls("ats_client_mappings")

    # ─── Revert ats_job_assignments → ats_job_recruiter_assignments ───────
    op.drop_constraint(
        "uq_ats_job_assignments_job_user_role",
        "ats_job_assignments",
        type_="unique",
    )
    op.drop_constraint(
        "ats_job_assignments_role_check",
        "ats_job_assignments",
        type_="check",
    )
    op.drop_column("ats_job_assignments", "role")
    op.drop_column("ats_job_assignments", "user_id")
    op.add_column(
        "ats_job_assignments",
        sa.Column("ats_vendor", sa.Text(), nullable=False, server_default="ceipal"),
    )
    op.add_column(
        "ats_job_assignments",
        sa.Column("external_user_id", sa.Text(), nullable=False, server_default=""),
    )
    op.alter_column("ats_job_assignments", "ats_vendor", server_default=None)
    op.alter_column("ats_job_assignments", "external_user_id", server_default=None)
    op.create_unique_constraint(
        "uq_ats_job_recruiter_assignments",
        "ats_job_assignments",
        ["job_posting_id", "external_user_id"],
    )
    op.rename_table("ats_job_assignments", "ats_job_recruiter_assignments")

    # ─── Restore vestigial scheduler columns on ats_connections ──────────
    op.add_column(
        "ats_connections",
        sa.Column(
            "last_synced_cursors",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "ats_connections",
        sa.Column(
            "poll_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("900"),
        ),
    )
    op.add_column(
        "ats_connections",
        sa.Column(
            "next_poll_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "ats_connections",
        sa.Column("poll_lock_acquired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ats_connections_due",
        "ats_connections",
        ["next_poll_at"],
        postgresql_where=sa.text("active = true"),
    )
    op.drop_constraint(
        "ats_connections_status_sync_mode_check",
        "ats_connections",
        type_="check",
    )
    op.drop_column("ats_connections", "status_sync_mode")
    op.drop_column("ats_connections", "tenant_timezone")
    op.drop_column("ats_connections", "last_synced_at")

    # ─── candidate_job_assignments ───────────────────────────────────────
    op.drop_column("candidate_job_assignments", "external_last_modified_at")
    op.drop_column("candidate_job_assignments", "external_pipeline_status")
    op.drop_column("candidate_job_assignments", "external_status")

    # ─── job_postings ────────────────────────────────────────────────────
    op.drop_index("job_postings_quarantined", table_name="job_postings")
    op.drop_column("job_postings", "import_last_error")
    op.drop_column("job_postings", "import_quarantined_at")
    op.drop_column("job_postings", "import_retry_count")
    op.drop_index("job_postings_ats_modified", table_name="job_postings")
    op.drop_column("job_postings", "external_last_modified_at")

    # ─── organizational_units ────────────────────────────────────────────
    op.drop_index(
        "org_units_external_identity_uniq", table_name="organizational_units"
    )
    op.drop_constraint(
        "org_units_source_external_id_check",
        "organizational_units",
        type_="check",
    )
    op.drop_column("organizational_units", "external_source_metadata")
    op.drop_column("organizational_units", "external_id")
    op.drop_column("organizational_units", "source")

    # ─── users ───────────────────────────────────────────────────────────
    op.drop_index("users_external_identity_uniq", table_name="users")
    op.drop_constraint(
        "users_source_external_id_check", "users", type_="check"
    )
    op.drop_column("users", "external_source_metadata")
    op.drop_column("users", "external_id")
    op.drop_column("users", "source")
    # Revert auth_user_id back to NOT NULL. Note: this will fail if any
    # row has auth_user_id IS NULL (i.e. ATS-only users that were imported
    # under the new orchestrator). Downgrade is best-effort: clear those
    # rows before reverting.
    op.execute(
        "DELETE FROM users WHERE auth_user_id IS NULL AND deleted_at IS NULL"
    )
    op.alter_column("users", "auth_user_id", nullable=False)
