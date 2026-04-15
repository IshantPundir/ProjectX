"""phase_2c1_pipeline_builder_tables

Revision ID: 0004_pipeline_builder
Revises: 0003_signal_schema_v2
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_pipeline_builder"
down_revision = "0003_signal_schema_v2"
branch_labels = None
depends_on = None

STAGE_TYPES = ("phone_screen", "ai_interview", "human_interview", "panel_interview", "take_home")
DIFFICULTIES = ("easy", "medium", "hard")
ADVANCE_BEHAVIORS = ("auto_advance", "manual_review")


def _sql_in(values: tuple[str, ...]) -> str:
    """Render a tuple of string literals as a SQL ``IN (...)`` clause.

    Produces output that is byte-identical to ``f"{values!r}"`` for a plain
    tuple of strings but does not rely on Python's tuple ``__repr__`` as a
    stand-in for SQL syntax. Values must be trusted static literals — this
    helper does not escape embedded quotes.
    """
    return "(" + ", ".join("'" + v + "'" for v in values) + ")"


def upgrade() -> None:
    # --- pipeline_templates ---
    op.create_table(
        "pipeline_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("org_unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizational_units.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("from_starter", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_pipeline_templates_org_unit_default "
        "ON pipeline_templates (org_unit_id) WHERE is_default = true"
    )
    op.execute("ALTER TABLE pipeline_templates ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pipeline_templates "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON pipeline_templates "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )

    # --- pipeline_template_stages ---
    op.create_table(
        "pipeline_template_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stage_type", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(), nullable=False),
        sa.Column("signal_filter", postgresql.JSONB(), nullable=False),
        sa.Column("pass_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("advance_behavior", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("template_id", "position", name="uq_template_stage_position"),
    )
    op.create_check_constraint(
        "ck_template_stages_stage_type", "pipeline_template_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES)}"
    )
    op.create_check_constraint(
        "ck_template_stages_difficulty", "pipeline_template_stages",
        f"difficulty IN {_sql_in(DIFFICULTIES)}"
    )
    op.create_check_constraint(
        "ck_template_stages_advance_behavior", "pipeline_template_stages",
        f"advance_behavior IN {_sql_in(ADVANCE_BEHAVIORS)}"
    )
    op.create_check_constraint(
        "ck_template_stages_duration", "pipeline_template_stages",
        "duration_minutes > 0 AND duration_minutes <= 240"
    )
    op.execute("ALTER TABLE pipeline_template_stages ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pipeline_template_stages "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON pipeline_template_stages "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )

    # --- job_pipeline_instances ---
    op.create_table(
        "job_pipeline_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_templates.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("job_posting_id", name="uq_job_pipeline_instance_job"),
    )
    op.execute("ALTER TABLE job_pipeline_instances ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON job_pipeline_instances "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON job_pipeline_instances "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )

    # --- job_pipeline_stages ---
    op.create_table(
        "job_pipeline_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_pipeline_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stage_type", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(), nullable=False),
        sa.Column("signal_filter", postgresql.JSONB(), nullable=False),
        sa.Column("pass_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("advance_behavior", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("instance_id", "position", name="uq_job_pipeline_stage_position"),
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_stage_type", "job_pipeline_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES)}"
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_difficulty", "job_pipeline_stages",
        f"difficulty IN {_sql_in(DIFFICULTIES)}"
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_advance_behavior", "job_pipeline_stages",
        f"advance_behavior IN {_sql_in(ADVANCE_BEHAVIORS)}"
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_duration", "job_pipeline_stages",
        "duration_minutes > 0 AND duration_minutes <= 240"
    )
    op.execute("ALTER TABLE job_pipeline_stages ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON job_pipeline_stages "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON job_pipeline_stages "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )


def downgrade() -> None:
    op.drop_table("job_pipeline_stages")
    op.drop_table("job_pipeline_instances")
    op.drop_table("pipeline_template_stages")
    op.execute("DROP INDEX IF EXISTS ix_pipeline_templates_org_unit_default")
    op.drop_table("pipeline_templates")
