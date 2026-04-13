"""add updated_by to job_postings

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_add_updated_by"
down_revision = "0001_phase_2b_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_postings", "updated_by")
