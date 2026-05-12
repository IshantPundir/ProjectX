"""ats_job_status_filter_and_progress

Revision ID: 0032
Revises: 0031
Create Date: 2026-05-12

Adds:
  * ats_connections.job_status_filter  (JSONB NULL)
    Persists which Ceipal job statuses to fetch; NULL = not yet configured.
    Importer's jobs phase short-circuits when NULL.
  * ats_sync_logs.progress              (JSONB NOT NULL DEFAULT '{}')
    Mid-flight per-phase counter (e.g. {"jobs": {"processed": 245, "total": 662}}).
    Written by the importer every row; polled by the recruiter UI.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ats_connections",
        sa.Column("job_status_filter", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "ats_sync_logs",
        sa.Column(
            "progress",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ats_sync_logs", "progress")
    op.drop_column("ats_connections", "job_status_filter")
