"""question_difficulty

Revision ID: 0042
Revises: 0041
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add stage_questions.difficulty (TEXT, nullable).

    Per-question difficulty for finer control than the stage-level setting.
    NULL means "inherit the stage difficulty" — build_session_config falls
    back to StageConfig.difficulty when this is NULL. Legacy banks are NOT
    backfilled (regeneration stamps the stage difficulty). The CHECK allows
    NULL or one of the three difficulty literals.
    """
    op.add_column(
        "stage_questions",
        sa.Column("difficulty", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "stage_questions_difficulty_check",
        "stage_questions",
        "difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "stage_questions_difficulty_check", "stage_questions", type_="check",
    )
    op.drop_column("stage_questions", "difficulty")
