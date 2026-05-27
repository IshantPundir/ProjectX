"""drop job_postings.interview_engine_version

Revision ID: 0048
Revises: 0047
Create Date: 2026-05-27

The v1/v2 engine-selection flag is retired: v2 is the sole interview engine, so
the per-job override column and its CHECK are removed. Downgrade re-adds them
(nullable; NULL formerly meant "inherit the global default") as the rollback path.
"""

from alembic import op
import sqlalchemy as sa

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_CK = "ck_job_postings_interview_engine_version"


def upgrade() -> None:
    op.drop_constraint(_CK, "job_postings", type_="check")
    op.drop_column("job_postings", "interview_engine_version")


def downgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("interview_engine_version", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        _CK,
        "job_postings",
        "interview_engine_version IS NULL OR interview_engine_version IN ('v1', 'v2')",
    )
