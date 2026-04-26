"""relax io stage columns — allow NULL for intake/debrief forbidden fields

intake and debrief stage types have FORBIDDEN constraints (via the Pydantic
field-rules validator) on duration_minutes, difficulty, signal_filter, and
advance_behavior.  For debrief, pass_criteria is LOCKED (always set), but for
intake and debrief both, signal_filter / difficulty / duration_minutes are
None after validation.

Previously those DB columns were NOT NULL, so inserting an intake or debrief
stage from the "scratch" picker path would raise a NOT NULL violation.

This migration drops the NOT NULL constraints on those five columns in BOTH
stage tables so the application layer is authoritative on the shape.

Rollback: adds the NOT NULL constraints back.  NOTE: rollback will fail if
any row has NULL in these columns — check before rolling back.

Revision ID: 0019_relax_io_stage_cols
Revises: 0018_pipeline_ver_pause
Create Date: 2026-04-26
"""
from __future__ import annotations

from alembic import op

revision = "0019_relax_io_stage_cols"
down_revision = "0018_pipeline_ver_pause"
branch_labels = None
depends_on = None

_TABLES = ["pipeline_template_stages", "job_pipeline_stages"]
_COLS = ["duration_minutes", "difficulty", "signal_filter", "pass_criteria", "advance_behavior"]


def upgrade() -> None:
    for table in _TABLES:
        for col in _COLS:
            op.execute(f'ALTER TABLE {table} ALTER COLUMN {col} DROP NOT NULL')


def downgrade() -> None:
    for table in _TABLES:
        for col in _COLS:
            op.execute(f'ALTER TABLE {table} ALTER COLUMN {col} SET NOT NULL')
