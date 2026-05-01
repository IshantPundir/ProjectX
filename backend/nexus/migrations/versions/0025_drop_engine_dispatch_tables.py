"""Phase 3 — drop engine_dispatch_tokens + engine_token_uses.

Phase 3 of the modular-monolith uplift retired the engine-dispatch JWT
layer. The interview engine now runs in-process inside nexus and reads
SessionConfig / posts SessionResult via direct calls into
``app.modules.interview_runtime.service``. With no JWT, the
``engine_dispatch_tokens`` (per-issued token rows) and ``engine_token_uses``
(single-use enforcement on jti+endpoint pair) tables are dead weight.

The downgrade recreates the table structure but cannot recover any data.
In-flight dispatches at rollback time would be unrecoverable — but with
zero users in production at the time of this migration, the rollback
hazard is theoretical.

Revision ID: 0025_drop_engine_dispatch_tables
Revises: 0024_engine_integration
Create Date: 2026-05-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers
revision = "0025_drop_engine_dispatch_tables"
down_revision = "0024_engine_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the dependent table first — engine_token_uses references
    # engine_dispatch_tokens via the jti FK.
    op.execute("DROP TABLE IF EXISTS engine_token_uses CASCADE")
    op.execute("DROP TABLE IF EXISTS engine_dispatch_tokens CASCADE")


def downgrade() -> None:
    # Recreate engine_dispatch_tokens with the exact 0024 schema.
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

    # Recreate engine_token_uses (service-bypass-only, composite PK on
    # (jti, endpoint), CASCADE on engine_dispatch_tokens.jti deletion).
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
