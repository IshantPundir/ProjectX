"""bank_generation_status_by_kind

Revision ID: 0040
Revises: 0039
Create Date: 2026-05-19

Adds per-question-kind generation status tracking to stage_question_banks.
The canonical bank.status remains authoritative; this column surfaces which
kind (behavioral_star / technical_depth) succeeded or failed for per-kind retry.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stage_question_banks",
        sa.Column(
            "generation_status_by_kind",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        "COMMENT ON COLUMN stage_question_banks.generation_status_by_kind IS "
        "'Per-question-kind generation status. Shape: "
        "{\"behavioral_star\": status, \"technical_depth\": status} "
        "where status in (\"reviewing\", \"failed\", \"skipped_no_eligible_signals\"). "
        "The single canonical bank.status reflects the union.'"
    )


def downgrade() -> None:
    op.drop_column("stage_question_banks", "generation_status_by_kind")
