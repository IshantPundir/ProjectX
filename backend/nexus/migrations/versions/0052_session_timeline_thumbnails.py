"""session_timeline_thumbnails — derived report-timeline thumbnails (questions + flags).

Tenant-scoped, canonical RLS pair (NULLIF discipline). One row per
(session, kind, ref). Written by the vision worker; presigned on read.

Rollback: downgrade drops the table (policies drop with it). Safe — no other
table references it.

Revision ID: 0052
Revises: 0051
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052"
down_revision = "0051"
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
        "session_timeline_thumbnails",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("t_ms", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("session_id", "kind", "ref_id",
                            name="uq_timeline_thumb_session_kind_ref"),
    )
    op.execute(
        "ALTER TABLE session_timeline_thumbnails ADD CONSTRAINT "
        "session_timeline_thumbnails_kind_check CHECK (kind IN ('question','flag'))"
    )
    op.create_index("ix_timeline_thumb_session", "session_timeline_thumbnails",
                    ["session_id"])
    _enable_rls("session_timeline_thumbnails")


def downgrade() -> None:
    op.drop_table("session_timeline_thumbnails")
