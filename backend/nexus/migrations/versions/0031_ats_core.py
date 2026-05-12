"""ats_core

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-12

Adds the per-tenant ATS integration tables (ats_connections,
ats_client_mappings, ats_user_mappings, ats_job_recruiter_assignments,
ats_sync_logs), plus the column additions needed on organizational_units,
job_postings, candidates, and candidate_job_assignments. RLS policies use
the canonical tenant_isolation + service_bypass pair wrapped in
NULLIF(..., '')::uuid per app/CLAUDE.md.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


_NEW_TABLES = (
    "ats_connections",
    "ats_client_mappings",
    "ats_user_mappings",
    "ats_job_recruiter_assignments",
    "ats_sync_logs",
)


def _apply_canonical_rls(table: str) -> None:
    """Apply the canonical tenant_isolation + service_bypass RLS pair
    with NULLIF-wrapped current_tenant cast (per app/CLAUDE.md)."""
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
    # ---- ats_connections ----
    op.create_table(
        "ats_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor", sa.Text(), nullable=False),
        sa.Column("credentials_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("access_token_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_cursors", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False,
                  server_default=sa.text("900")),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("poll_lock_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_error", sa.Text()),
        sa.Column("rate_limit_qps", sa.Numeric()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("disabled_reason", sa.Text()),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint("tenant_id", "vendor", name="uq_ats_connections_tenant_vendor"),
    )
    op.create_index("ix_ats_connections_due", "ats_connections",
                    ["next_poll_at"], postgresql_where=sa.text("active = true"))
    _apply_canonical_rls("ats_connections")

    # ---- ats_client_mappings ----
    op.create_table(
        "ats_client_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_client_id", sa.Text(), nullable=False),
        sa.Column("external_client_name", sa.Text(), nullable=False),
        sa.Column("org_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_unit_id"], ["organizational_units.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "ats_vendor", "external_client_id",
                            name="uq_ats_client_mappings_external"),
    )
    _apply_canonical_rls("ats_client_mappings")

    # ---- ats_user_mappings ----
    op.create_table(
        "ats_user_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column("external_user_email", sa.Text(), nullable=False),
        sa.Column("external_user_display_name", sa.Text(), nullable=False),
        sa.Column("external_user_role", sa.Text()),
        sa.Column("external_user_status", sa.Text()),
        sa.Column("external_user_metadata", postgresql.JSONB()),
        sa.Column("internal_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("mapped_at", sa.DateTime(timezone=True)),
        sa.Column("mapped_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["internal_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["mapped_by"], ["users.id"]),
        sa.UniqueConstraint("tenant_id", "ats_vendor", "external_user_id",
                            name="uq_ats_user_mappings_external"),
    )
    _apply_canonical_rls("ats_user_mappings")

    # ---- ats_job_recruiter_assignments ----
    op.create_table(
        "ats_job_recruiter_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ats_vendor", sa.Text(), nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_posting_id"], ["job_postings.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_posting_id", "external_user_id",
                            name="uq_ats_job_recruiter_assignments"),
    )
    _apply_canonical_rls("ats_job_recruiter_assignments")

    # ---- ats_sync_logs ----
    op.create_table(
        "ats_sync_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),  # running | success | partial | failed
        sa.Column("entity_counts", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_phase", sa.Text()),
        sa.Column("error_summary", sa.Text()),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["ats_connections.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_ats_sync_logs_connection_started", "ats_sync_logs",
                    ["connection_id", "started_at"])
    _apply_canonical_rls("ats_sync_logs")

    # ---- Column additions to existing tables ----
    op.add_column("organizational_units",
                  sa.Column("company_profile_completion_status", sa.Text(),
                            nullable=False, server_default=sa.text("'complete'")))
    op.create_check_constraint(
        "ck_org_units_completion_status",
        "organizational_units",
        "company_profile_completion_status IN ('pending', 'complete')",
    )

    op.add_column("job_postings",
                  sa.Column("external_status", sa.Text()))
    # Broaden the status CHECK constraint to add blocked_pending_client_setup.
    # The exact name of the existing CHECK constraint depends on the original migration;
    # discover and drop the existing CHECK before recreating.
    op.execute("""
        DO $$
        DECLARE
            cname text;
        BEGIN
            SELECT conname INTO cname
            FROM pg_constraint
            WHERE conrelid = 'job_postings'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%status%';
            IF cname IS NOT NULL THEN
                EXECUTE 'ALTER TABLE job_postings DROP CONSTRAINT ' || cname;
            END IF;
        END$$;
    """)
    op.create_check_constraint(
        "ck_job_postings_status",
        "job_postings",
        "status IN ('draft', 'signals_extracting', 'signals_extraction_failed', "
        "'signals_extracted', 'signals_confirmed', "
        "'pipeline_built', 'active', 'archived', "
        "'blocked_pending_client_setup')",
    )

    op.add_column("candidate_job_assignments",
                  sa.Column("source", sa.Text(), nullable=False,
                            server_default=sa.text("'manual'")))
    op.add_column("candidate_job_assignments",
                  sa.Column("external_id", sa.Text()))
    op.add_column("candidate_job_assignments",
                  sa.Column("source_metadata", postgresql.JSONB()))
    op.execute("""
        CREATE UNIQUE INDEX candidate_job_assignments_external_idx
          ON candidate_job_assignments (tenant_id, source, external_id)
          WHERE external_id IS NOT NULL;
    """)

    op.execute("""
        CREATE UNIQUE INDEX candidates_tenant_source_external_idx
          ON candidates (tenant_id, source, external_id)
          WHERE pii_redacted_at IS NULL AND external_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS candidates_tenant_source_external_idx;")
    op.execute("DROP INDEX IF EXISTS candidate_job_assignments_external_idx;")
    op.drop_column("candidate_job_assignments", "source_metadata")
    op.drop_column("candidate_job_assignments", "external_id")
    op.drop_column("candidate_job_assignments", "source")

    op.drop_constraint("ck_job_postings_status", "job_postings")
    # Restore the prior CHECK to avoid leaving the column unchecked.
    op.create_check_constraint(
        "ck_job_postings_status",
        "job_postings",
        "status IN ('draft', 'signals_extracting', 'signals_extraction_failed', "
        "'signals_extracted', 'signals_confirmed', "
        "'pipeline_built', 'active', 'archived')",
    )
    op.drop_column("job_postings", "external_status")

    op.drop_constraint("ck_org_units_completion_status", "organizational_units")
    op.drop_column("organizational_units", "company_profile_completion_status")

    for table in reversed(_NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS service_bypass ON {table};")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.drop_table(table)
