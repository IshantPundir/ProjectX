"""session_reels — AI-directed candidate highlight reel (Candidate Reel feature).

Adds the session_reels table (one current reel per session) with the canonical
tenant_isolation + service_bypass RLS pair (NULLIF discipline), mirroring
session_reports (migration 0047). The reel is generated asynchronously by the
``generate_session_reel`` Dramatiq actor and transitions through:

    pending → generating → ready
                         ↘ failed   (retryable via regenerate)

``generation_started_at`` + ``attempts`` make the generating state observable and
support crash recovery (a stuck ``generating`` row is re-claimed on Dramatiq
redelivery / explicit regenerate).

Rollback: downgrade drops the table (policies drop with it). Safe — no other
table references session_reels.

Revision ID: 0053
Revises: 0052
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    """Apply the canonical tenant_isolation + service_bypass RLS pair.

    NULLIF(..., '')::uuid per CLAUDE.md discipline (avoids the empty-string crash
    when a custom GUC reverts after SET LOCAL).
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
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexus_app;")


def upgrade() -> None:
    op.create_table(
        "session_reels",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "assignment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("generation_error", sa.Text()),
        sa.Column("generation_started_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("edl", postgresql.JSONB()),
        sa.Column("chapters", postgresql.JSONB()),
        sa.Column("r2_key", sa.Text()),
        sa.Column("duration_seconds", sa.Numeric()),
        sa.Column("model_versions", postgresql.JSONB()),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.execute(
        "ALTER TABLE session_reels ADD CONSTRAINT session_reels_status_check "
        "CHECK (status IN ('pending','generating','ready','failed'))"
    )
    op.create_index("ix_session_reels_assignment_id", "session_reels", ["assignment_id"])
    op.create_index("ix_session_reels_tenant_status", "session_reels", ["tenant_id", "status"])
    _enable_rls("session_reels")

    # BEFORE UPDATE trigger — keeps updated_at current on every UPDATE.
    # touch_updated_at() is the shared function created in migration 0017.
    op.execute("""
        CREATE TRIGGER session_reels_touch_updated_at
            BEFORE UPDATE ON session_reels
            FOR EACH ROW
            EXECUTE FUNCTION touch_updated_at();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS session_reels_touch_updated_at ON session_reels;")
    op.drop_table("session_reels")
