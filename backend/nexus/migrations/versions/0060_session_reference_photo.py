"""add session reference-photo columns

Revision ID: 0060
Revises: 0059
Create Date: 2026-06-16

Adds the columns that hold the candidate's reference still captured on the
camera-test step (uploaded to the same R2 bucket as recordings, key on the
session row). Reused later by the report + reel/thumbnail surfaces.

  reference_photo_key           R2 object key (NULL until captured)
  reference_photo_captured_at   when the still was stored

No RLS change — `sessions` keeps its tenant_isolation + service_bypass pair;
new columns inherit it.

Rollback: downgrade() drops both columns.
"""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059_drop_knockout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("reference_photo_key", sa.Text(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("reference_photo_captured_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "reference_photo_captured_at")
    op.drop_column("sessions", "reference_photo_key")
