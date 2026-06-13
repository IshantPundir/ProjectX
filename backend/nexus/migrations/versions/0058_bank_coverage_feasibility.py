"""bank coverage feasibility column

Revision ID: 0058
Revises: 0057
Create Date: 2026-06-13

Adds stage_question_banks.coverage_feasibility (JSONB, nullable) — the typed over-
subscription verdict from the coverage planner (feasible / secondary_only / recommended
minutes) surfaced as a recruiter badge. Existing table; inherits its RLS policy pair. No
data backfill — legacy banks read NULL (no badge).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stage_question_banks",
        sa.Column("coverage_feasibility", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stage_question_banks", "coverage_feasibility")
