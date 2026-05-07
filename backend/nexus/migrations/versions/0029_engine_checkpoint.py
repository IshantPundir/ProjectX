"""engine_checkpoint

Revision ID: 0029
Revises: 0028_audio_tuning_summary
Create Date: 2026-05-07

Adds `sessions.engine_checkpoint JSONB NULL`. Holds the last per-turn
snapshot (EngineCheckpoint) written by the structured agent for crash
recovery. Written every 10 turns or 30 s; queried by the agent on
session rejoin to restore state without replaying history.

PG11+ metadata-only (no rewrite). Down-migration drops the column.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0029"
down_revision: str | None = "0028_audio_tuning_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("engine_checkpoint", postgresql.JSONB(), nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN sessions.engine_checkpoint IS "
        "'Last per-turn snapshot for crash recovery. Written every 10 turns or 30s.';"
    )


def downgrade() -> None:
    op.drop_column("sessions", "engine_checkpoint")
