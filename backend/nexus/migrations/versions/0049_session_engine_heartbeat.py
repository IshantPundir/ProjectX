"""add sessions.last_engine_heartbeat_at (engine liveness pulse)

Revision ID: 0049
Revises: 0048
Create Date: 2026-05-27

The running interview engine writes a periodic liveness pulse to this column.
The stuck-session reaper uses COALESCE(last_engine_heartbeat_at, state_changed_at)
as the "last sign of life", so a legitimately long interview (still pulsing) is
never reaped, while a dead engine (pulse goes stale, or never connected) is reaped
once that timestamp ages past reaper_stuck_threshold_seconds. Nullable; no RLS
change (existing tenant-scoped table keeps its policy pair).
"""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("last_engine_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "last_engine_heartbeat_at")
