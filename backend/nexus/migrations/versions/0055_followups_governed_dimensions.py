"""followups governed dimensions backfill

Revision ID: 0055_followups_governed_dimensions
Revises: 0054_session_evidence
Create Date: 2026-06-11
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

# Lazy import: env.py inserts the project root onto sys.path before running
# upgrade/downgrade, but `alembic heads` (revision scan) never runs env.py so
# a top-level `from app...` would raise ModuleNotFoundError during the scan.
# Moving the import here keeps it normal (no importlib) while staying scan-safe.
def _get_helpers():
    from app.migrations_support.followups_backfill import downgrade_value, upgrade_value  # noqa: PLC0415
    return upgrade_value, downgrade_value


revision = "0055"
down_revision = "0054_session_evidence"
branch_labels = None
depends_on = None


def _rewrite(transform) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, follow_ups FROM stage_questions")).fetchall()
    for row_id, follow_ups in rows:
        value = follow_ups if isinstance(follow_ups, (list, dict)) else json.loads(follow_ups or "[]")
        new_value = transform(value)
        conn.execute(
            sa.text("UPDATE stage_questions SET follow_ups = CAST(:fu AS JSONB) WHERE id = :id"),
            {"fu": json.dumps(new_value), "id": row_id},
        )


def upgrade() -> None:
    upgrade_value, _ = _get_helpers()
    _rewrite(upgrade_value)


def downgrade() -> None:
    _, downgrade_value = _get_helpers()
    _rewrite(downgrade_value)
