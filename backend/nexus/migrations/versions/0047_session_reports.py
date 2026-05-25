"""session_reports — offline candidate evaluation report (Phase 3D reporting).

Adds the session_reports table (one current report per session) with the
canonical tenant_isolation + service_bypass RLS pair (NULLIF discipline).

Rollback: downgrade drops the table (policies drop with it). Safe — no other
table references session_reports.

Revision ID: 0047
Revises: 0046
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    """Apply canonical tenant_isolation + service_bypass RLS pair.

    Uses NULLIF(..., '')::uuid per CLAUDE.md discipline to avoid the
    empty-string crash when a custom GUC reverts after SET LOCAL.
    """
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
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexus_app;"
    )


def upgrade() -> None:
    op.create_table(
        "session_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
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
            unique=True,
        ),
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("generation_error", sa.Text()),
        sa.Column("engine_version", sa.Text()),
        sa.Column("verdict", sa.Text()),
        sa.Column("verdict_reason", sa.Text()),
        sa.Column("overall_score", sa.Integer()),
        sa.Column("overall_coverage", sa.Numeric()),
        sa.Column("overall_confidence", sa.Text()),
        sa.Column("dimension_scores", postgresql.JSONB()),
        sa.Column("knockout_results", postgresql.JSONB()),
        sa.Column("signal_scorecards", postgresql.JSONB()),
        sa.Column("question_scorecards", postgresql.JSONB()),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("rubric_snapshot", postgresql.JSONB()),
        sa.Column("scoring_manifest", postgresql.JSONB()),
        sa.Column("human_decision", postgresql.JSONB()),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.execute(
        "ALTER TABLE session_reports ADD CONSTRAINT session_reports_status_check "
        "CHECK (status IN ('pending','generating','ready','failed'))"
    )
    op.execute(
        "ALTER TABLE session_reports ADD CONSTRAINT session_reports_verdict_check "
        "CHECK (verdict IS NULL OR verdict IN ('advance','borderline','reject'))"
    )
    op.create_index(
        "ix_session_reports_assignment_id",
        "session_reports",
        ["assignment_id"],
    )
    op.create_index(
        "ix_session_reports_tenant_verdict",
        "session_reports",
        ["tenant_id", "verdict"],
    )
    _enable_rls("session_reports")


def downgrade() -> None:
    op.drop_table("session_reports")
