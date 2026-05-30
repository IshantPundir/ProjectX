"""session_proctoring_analysis — post-session vision proctoring features.

One row per session. Stores derived gaze/multi-face features only (no frames).
Canonical tenant_isolation + service_bypass RLS pair (NULLIF discipline).

Rollback: downgrade drops the table (policies + trigger drop with it).

Revision ID: 0051
Revises: 0050
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
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
    op.create_table(
        "session_proctoring_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("risk_band", sa.Text()),
        sa.Column("detector_summary", postgresql.JSONB()),
        sa.Column("gaze_heatmap", postgresql.JSONB()),
        sa.Column("flagged_intervals", postgresql.JSONB()),
        sa.Column("gaze_signal_quality", sa.Text()),
        sa.Column("unscorable_pct", sa.Numeric()),
        sa.Column("model_versions", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("frames_analyzed", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.execute(
        "ALTER TABLE session_proctoring_analysis ADD CONSTRAINT spa_status_check "
        "CHECK (status IN ('pending','running','ready','failed','unscorable'))"
    )
    _enable_rls("session_proctoring_analysis")
    op.execute("""
        CREATE TRIGGER session_proctoring_analysis_touch_updated_at
            BEFORE UPDATE ON session_proctoring_analysis
            FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS session_proctoring_analysis_touch_updated_at ON session_proctoring_analysis;")
    op.drop_table("session_proctoring_analysis")
