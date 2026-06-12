"""bank v3: project_deepdive question_kind + self_reviewing bank status

Revision ID: 0057
Revises: 0056
Create Date: 2026-06-12

Two CHECK extensions for the question-bank v3 measurement-instrument redesign:
  1. stage_questions.question_kind  += 'project_deepdive'
  2. stage_question_banks.status     += 'self_reviewing'

Both are supersets of the existing constraint, so existing rows stay valid and no
data clear is needed on upgrade. downgrade() narrows each CHECK, so it first rewrites
any row holding the new value (project_deepdive -> behavioral; self_reviewing ->
generating) to avoid a CHECK violation on constraint recreate.
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_KIND_CK = "stage_questions_question_kind_check"
_KIND_NEW = (
    "question_kind IN ('experience_check', 'behavioral', "
    "'technical_scenario', 'compliance_binary', 'project_deepdive')"
)
_KIND_OLD = (
    "question_kind IN ('experience_check', 'behavioral', "
    "'technical_scenario', 'compliance_binary')"
)

_STATUS_CK = "stage_question_banks_status_check"
_STATUS_NEW = (
    "status IN ('draft', 'generating', 'self_reviewing', "
    "'reviewing', 'confirmed', 'failed')"
)
_STATUS_OLD = (
    "status IN ('draft', 'generating', 'reviewing', 'confirmed', 'failed')"
)


def upgrade() -> None:
    op.drop_constraint(_KIND_CK, "stage_questions", type_="check")
    op.create_check_constraint(_KIND_CK, "stage_questions", _KIND_NEW)

    op.drop_constraint(_STATUS_CK, "stage_question_banks", type_="check")
    op.create_check_constraint(_STATUS_CK, "stage_question_banks", _STATUS_NEW)


def downgrade() -> None:
    # Rewrite values the narrowed CHECK would reject, THEN narrow.
    op.execute(
        "UPDATE stage_questions SET question_kind = 'behavioral' "
        "WHERE question_kind = 'project_deepdive'"
    )
    op.drop_constraint(_KIND_CK, "stage_questions", type_="check")
    op.create_check_constraint(_KIND_CK, "stage_questions", _KIND_OLD)

    op.execute(
        "UPDATE stage_question_banks SET status = 'generating' "
        "WHERE status = 'self_reviewing'"
    )
    op.drop_constraint(_STATUS_CK, "stage_question_banks", type_="check")
    op.create_check_constraint(_STATUS_CK, "stage_question_banks", _STATUS_OLD)
