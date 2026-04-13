"""question banks + stage questions

Revision ID: 0006_question_banks
Revises: 0005_simplify_signal_filter
Create Date: 2026-04-12

Phase 2C.2 — Question Generation. Two new tables scoped per tenant with RLS.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_question_banks"
down_revision = "0005_simplify_signal_filter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- stage_question_banks ---
    op.create_table(
        "stage_question_banks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("prompt_version", sa.Text(), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("generation_error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('draft', 'generating', 'reviewing', 'confirmed', 'failed')",
            name="stage_question_banks_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["job_pipeline_stages.id"],
            ondelete="CASCADE", name="fk_stage_question_banks_stage",
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_id"], ["job_postings.id"],
            ondelete="CASCADE", name="fk_stage_question_banks_job",
        ),
        sa.ForeignKeyConstraint(
            ["signal_snapshot_id"], ["job_posting_signal_snapshots.id"],
            name="fk_stage_question_banks_signal_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["generated_by"], ["users.id"],
            name="fk_stage_question_banks_generated_by",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"],
            name="fk_stage_question_banks_confirmed_by",
        ),
    )

    op.create_index(
        "ix_stage_question_banks_stage",
        "stage_question_banks",
        ["stage_id"],
        unique=True,
    )
    op.create_index(
        "ix_stage_question_banks_job",
        "stage_question_banks",
        ["job_posting_id"],
    )
    op.create_index(
        "ix_stage_question_banks_tenant_status",
        "stage_question_banks",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_stage_question_banks_snapshot",
        "stage_question_banks",
        ["signal_snapshot_id"],
    )

    # RLS
    op.execute("ALTER TABLE stage_question_banks ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY "tenant_isolation" ON stage_question_banks
          USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("""
        CREATE POLICY "service_role_bypass" ON stage_question_banks
          USING (current_setting('app.bypass_rls', true) = 'true')
    """)

    # --- stage_questions ---
    op.create_table(
        "stage_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("signal_values", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("estimated_minutes", sa.Numeric(4, 1), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("follow_ups", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("positive_evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("red_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("rubric", postgresql.JSONB(), nullable=False),
        sa.Column("evaluation_hint", sa.Text(), nullable=False),
        sa.Column("edited_by_recruiter", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("position >= 0", name="stage_questions_position_nonneg"),
        sa.CheckConstraint(
            "source IN ('ai_generated', 'ai_regenerated', 'recruiter')",
            name="stage_questions_source_check",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id"], ["stage_question_banks.id"],
            ondelete="CASCADE", name="fk_stage_questions_bank",
        ),
    )

    op.create_index(
        "ix_stage_questions_bank_position",
        "stage_questions",
        ["bank_id", "position"],
        unique=True,
    )
    op.execute(
        "CREATE INDEX ix_stage_questions_signal_values_gin "
        "ON stage_questions USING GIN (signal_values)"
    )

    # RLS
    op.execute("ALTER TABLE stage_questions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY "tenant_isolation" ON stage_questions
          USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("""
        CREATE POLICY "service_role_bypass" ON stage_questions
          USING (current_setting('app.bypass_rls', true) = 'true')
    """)


def downgrade() -> None:
    op.drop_index("ix_stage_questions_signal_values_gin", table_name="stage_questions")
    op.drop_index("ix_stage_questions_bank_position", table_name="stage_questions")
    op.drop_table("stage_questions")
    op.drop_index("ix_stage_question_banks_snapshot", table_name="stage_question_banks")
    op.drop_index("ix_stage_question_banks_tenant_status", table_name="stage_question_banks")
    op.drop_index("ix_stage_question_banks_job", table_name="stage_question_banks")
    op.drop_index("ix_stage_question_banks_stage", table_name="stage_question_banks")
    op.drop_table("stage_question_banks")
