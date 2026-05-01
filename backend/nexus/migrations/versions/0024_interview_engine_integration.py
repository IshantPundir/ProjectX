"""Phase 3C.2 — Interview engine integration.

Adds:
  1. `engine_dispatch_tokens` (tenant-scoped) — one row per minted engine
     dispatch JWT.
  2. `engine_token_uses` (service-bypass-only) — composite (jti, endpoint)
     primary key enforces single-use semantics per endpoint.
  3. Seven columns on `sessions` for engine result persistence:
     raw_result_json, transcript, questions_asked, probes_fired,
     agent_completed_at, result_status, error_code.

Down migration drops both tables and the seven columns. WARNING: down loses
raw_result_json + transcript for completed sessions; the rollback runbook
requires a backup export first.

Revision ID: 0024_engine_integration
Revises: 0023_tenant_hard_delete_cascade
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024_engine_integration"
down_revision = "0023_tenant_hard_delete_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- sessions: result + status columns (Phase 3C.2 result persistence) ----
    op.add_column("sessions", sa.Column("raw_result_json", postgresql.JSONB()))
    op.add_column("sessions", sa.Column("transcript", postgresql.JSONB()))
    op.add_column("sessions", sa.Column("questions_asked", sa.Integer()))
    op.add_column("sessions", sa.Column("probes_fired", sa.Integer()))
    op.add_column("sessions", sa.Column("agent_completed_at", sa.DateTime(timezone=True)))
    op.add_column("sessions", sa.Column("result_status", sa.Text()))
    op.add_column("sessions", sa.Column("error_code", sa.Text()))
    op.create_check_constraint(
        "sessions_result_status_check",
        "sessions",
        "result_status IS NULL OR result_status IN ('ok', 'partial', 'error')",
    )

    # ---- engine_dispatch_tokens ----
    op.create_table(
        "engine_dispatch_tokens",
        sa.Column("jti", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "idx_engine_dispatch_tokens_session",
        "engine_dispatch_tokens",
        ["session_id"],
    )
    op.execute("ALTER TABLE engine_dispatch_tokens ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY "tenant_isolation" ON engine_dispatch_tokens
          USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY "service_bypass" ON engine_dispatch_tokens
          USING (current_setting('app.bypass_rls', true) = 'true');
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON engine_dispatch_tokens TO nexus_app"
    )

    # ---- engine_token_uses ----
    op.create_table(
        "engine_token_uses",
        sa.Column("jti", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("used_ip", postgresql.INET()),
        sa.PrimaryKeyConstraint("jti", "endpoint", name="engine_token_uses_pkey"),
        sa.ForeignKeyConstraint(
            ["jti"],
            ["engine_dispatch_tokens.jti"],
            ondelete="CASCADE",
            name="engine_token_uses_jti_fkey",
        ),
        sa.CheckConstraint(
            "endpoint IN ('config', 'results')",
            name="engine_token_uses_endpoint_check",
        ),
    )
    op.execute("ALTER TABLE engine_token_uses ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY "service_bypass" ON engine_token_uses
          USING      (current_setting('app.bypass_rls', true) = 'true')
          WITH CHECK (current_setting('app.bypass_rls', true) = 'true');
        """
    )
    op.execute("GRANT SELECT, INSERT ON engine_token_uses TO nexus_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS engine_token_uses CASCADE")
    op.execute("DROP TABLE IF EXISTS engine_dispatch_tokens CASCADE")
    op.drop_constraint("sessions_result_status_check", "sessions", type_="check")
    op.drop_column("sessions", "error_code")
    op.drop_column("sessions", "result_status")
    op.drop_column("sessions", "agent_completed_at")
    op.drop_column("sessions", "probes_fired")
    op.drop_column("sessions", "questions_asked")
    op.drop_column("sessions", "transcript")
    op.drop_column("sessions", "raw_result_json")
