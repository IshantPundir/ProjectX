"""bank spoken-question fields: primary_signal + question_kind taxonomy switch

Revision ID: 0045
Revises: 0044
Create Date: 2026-05-22

Interview-engine-v2 M2. Adds stage_questions.primary_signal (nullable) and switches
the question_kind CHECK OUTRIGHT to the new spoken taxonomy (no old∪new union; dev
mode, no backward compat — all banks are regenerated). A new-only CHECK re-validates
existing rows, so old-kind rows would block the ALTER: we CLEAR stage_questions first
(regeneration repopulates). No RLS change (columns/constraint on an already-policied
table). Rollback restores the original CHECK + drops primary_signal.
"""

from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

_CK = "stage_questions_question_kind_check"
_NEW = (
    "question_kind IN ('experience_check', 'behavioral', "
    "'technical_scenario', 'compliance_binary')"
)
_OLD = (
    "question_kind IN ('technical_depth', 'behavioral_star', "
    "'compliance_binary', 'open_culture')"
)


def upgrade() -> None:
    op.execute("DELETE FROM stage_questions")
    op.add_column(
        "stage_questions",
        sa.Column("primary_signal", sa.Text(), nullable=True),
    )
    op.drop_constraint(_CK, "stage_questions", type_="check")
    op.create_check_constraint(_CK, "stage_questions", _NEW)


def downgrade() -> None:
    op.execute("DELETE FROM stage_questions")
    op.drop_constraint(_CK, "stage_questions", type_="check")
    op.create_check_constraint(_CK, "stage_questions", _OLD)
    op.drop_column("stage_questions", "primary_signal")
