"""followups governed dimensions backfill

Revision ID: 0055_followups_governed_dimensions
Revises: 0054_session_evidence
Create Date: 2026-06-11
"""
from __future__ import annotations

import importlib.util
import json
import os

import sqlalchemy as sa
from alembic import op

# Load followups_migration directly by file path to avoid triggering
# the question_bank package __init__.py (which has a circular import
# chain at migration-load time when the full app is not yet initialised).
_HERE = os.path.dirname(__file__)
_HELPER_PATH = os.path.normpath(
    os.path.join(_HERE, "../../app/modules/question_bank/followups_migration.py")
)
_spec = importlib.util.spec_from_file_location(
    "followups_migration", _HELPER_PATH
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
upgrade_value = _mod.upgrade_value
downgrade_value = _mod.downgrade_value

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
    _rewrite(upgrade_value)


def downgrade() -> None:
    _rewrite(downgrade_value)
