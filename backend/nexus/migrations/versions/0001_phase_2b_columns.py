"""phase_2b_add_enrichment_status_and_prompt_version

Revision ID: 0001_phase_2b_columns
Revises:
Create Date: 2026-04-10

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_phase_2b_columns"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("enrichment_status", sa.Text(), server_default="idle", nullable=False),
    )
    op.add_column(
        "job_postings",
        sa.Column("enrichment_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("prompt_version", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_posting_signal_snapshots", "prompt_version")
    op.drop_column("job_postings", "enrichment_error")
    op.drop_column("job_postings", "enrichment_status")
