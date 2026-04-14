"""add coverage_notes column to stage_question_banks

Revision ID: 0007_add_coverage_notes
Revises: 0006_question_banks
Create Date: 2026-04-14

Phase 2C.2 fix: persist the LLM's chain-of-thought about question allocation
so recruiters/auditors can understand why the bank was structured a particular
way. Dropped previously; now stored on the bank row.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_add_coverage_notes"
down_revision = "0006_question_banks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stage_question_banks",
        sa.Column("coverage_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stage_question_banks", "coverage_notes")
