"""extracted_keyterms

Revision ID: 0041
Revises: 0040
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add stage_question_banks.extracted_keyterms (JSONB, nullable).

    Populated by question_bank/actors.py:generate_question_bank_stage as its
    final step. NULL means "extraction hasn't run for this bank yet" — the
    engine falls back to candidate-name-only STT boosting. Per spec
    docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md,
    legacy banks are NOT backfilled; recruiter regenerates to populate.
    """
    op.add_column(
        "stage_question_banks",
        sa.Column("extracted_keyterms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stage_question_banks", "extracted_keyterms")
