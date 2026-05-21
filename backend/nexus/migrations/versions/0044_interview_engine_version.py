"""interview engine v2 — per-job selection column

Revision ID: 0044
Revises: 0043
Create Date: 2026-05-22

Adds job_postings.interview_engine_version (nullable). NULL inherits the global
INTERVIEW_ENGINE_DEFAULT_VERSION; 'v1'/'v2' overrides it per job so a single test
job can be flipped to v2 without affecting any live flow. No RLS change (column
on an already-policied table).

Rollback: downgrade() drops the CHECK + column.
"""

from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

_CK = "ck_job_postings_interview_engine_version"


def upgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("interview_engine_version", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        _CK,
        "job_postings",
        "interview_engine_version IS NULL OR interview_engine_version IN ('v1', 'v2')",
    )


def downgrade() -> None:
    op.drop_constraint(_CK, "job_postings", type_="check")
    op.drop_column("job_postings", "interview_engine_version")
