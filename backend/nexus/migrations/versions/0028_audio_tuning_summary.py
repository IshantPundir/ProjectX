"""Audio pipeline empirical-tuning column.

Adds `sessions.audio_tuning_summary JSONB DEFAULT NULL`. Holds the
per-session pause/interruption/latency snapshot computed by the engine's
`_compute_audio_tuning_summary` helper at session close. Queried by
audit/tuning notebooks to land production-grade defaults for the
adaptive-interruption + ai-coustics QUAIL_L pipeline (see
docs/superpowers/specs/2026-05-06-audio-pipeline-design.md).

PG11+ metadata-only (no rewrite). Down-migration drops the column.

Revision ID: 0028_audio_tuning_summary
Revises: 0027_tenant_settings
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0028_audio_tuning_summary"
down_revision: str | None = "0027_tenant_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "audio_tuning_summary",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "audio_tuning_summary")
