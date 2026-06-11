"""drop generation_status_by_kind

Redundant with the bank.status state machine after the generation pipeline
collapsed to a single call (no per-phase partial state). Dev mode: column drop,
no data preserved.

Revision ID: 0056_drop_generation_status_by_kind
Revises: 0055
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("stage_question_banks", "generation_status_by_kind")


def downgrade() -> None:
    op.add_column(
        "stage_question_banks",
        sa.Column(
            "generation_status_by_kind",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
