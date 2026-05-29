"""add session recording lifecycle columns

Revision ID: 0050
Revises: 0049
Create Date: 2026-05-28

The interview engine session is recorded as a single MP4 (RoomComposite
egress: candidate camera + mixed candidate/agent audio) uploaded by LiveKit
Cloud Egress straight to an S3-compatible bucket (Cloudflare R2 at MVP).
`sessions.recording_s3_key` already existed (added in 0024 as a stub); this
migration adds the lifecycle columns the report page needs to know whether a
recording exists, is still processing, or failed:

  recording_status         pending | recording | ready | failed | absent
  recording_egress_id      LiveKit egress id (for pull-based status reconcile)
  recording_started_at     when egress was started (≈ session start)
  recording_ready_at       when the object was confirmed uploaded
  recording_duration_seconds  playback duration (from egress result)
  recording_bytes          object size (from egress result)

No RLS change — `sessions` keeps its existing tenant_isolation +
service_bypass policy pair; new columns inherit it.

Rollback: see downgrade() — drops the six columns; recording_s3_key is left
intact as it predates this migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "recording_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'absent'"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("recording_egress_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "recording_started_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("recording_ready_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("recording_duration_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("recording_bytes", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "recording_bytes")
    op.drop_column("sessions", "recording_duration_seconds")
    op.drop_column("sessions", "recording_ready_at")
    op.drop_column("sessions", "recording_started_at")
    op.drop_column("sessions", "recording_egress_id")
    op.drop_column("sessions", "recording_status")
