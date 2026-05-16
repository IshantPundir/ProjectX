"""sessions.error_code CHECK constraint.

Revision ID: 0039
Revises: 0038
Create Date: 2026-05-16

Pins error_code to the enumerated taxonomy defined in
app/modules/session/error_codes.py. The Literal there and the CHECK
here must move together — adding a value to one without the other
breaks INSERT/UPDATE.

Dev DB has zero non-null error_code rows at write time, so no backfill
needed.
"""
from __future__ import annotations

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sessions
          ADD CONSTRAINT sessions_error_code_check
          CHECK (
            error_code IS NULL OR error_code IN (
              'engine_session_config_invalid',
              'engine_company_profile_missing',
              'engine_question_bank_not_ready',
              'engine_room_join_failed',
              'engine_internal_error',
              'engine_unresponsive'
            )
          )
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN sessions.error_code IS
        'Coded reason for state=error. See app/modules/session/error_codes.py for taxonomy.'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_error_code_check")
    op.execute("COMMENT ON COLUMN sessions.error_code IS NULL")
