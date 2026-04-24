"""stage_questions.updated_at auto-refresh trigger

Adds a BEFORE UPDATE trigger on stage_questions that sets
NEW.updated_at to clock_timestamp() on every UPDATE.

Uses clock_timestamp() rather than NOW() so the timestamp advances even
when INSERT and UPDATE happen within the same transaction (NOW() is pinned
to transaction start time). This matters for test isolation and for the SSE
backstop max(updated_at) detection that depends on strictly-increasing values.

Defense-in-depth: works regardless of whether the UPDATE comes from the ORM,
raw SQL, psql, or a Dramatiq actor. The ORM-level onupdate (Task 2) catches
the happy path; this catches everything else.

The trigger function is named generically so other tables can reuse it:
  CREATE TRIGGER <table>_touch_updated_at
    BEFORE UPDATE ON <table>
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

Revision ID: 0017_stage_questions_updated_at_trigger
Revises: 0016_stage_v5_participants
Create Date: 2026-04-24
"""
from __future__ import annotations

from alembic import op

revision = "0017_sq_updated_at_trigger"
down_revision = "0016_stage_v5_participants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Generic helper function — reusable by any table with an updated_at column.
    # clock_timestamp() returns the real wall-clock time at the moment the
    # function is called, unlike NOW() which is pinned to transaction start.
    op.execute("""
        CREATE OR REPLACE FUNCTION touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER stage_questions_touch_updated_at
            BEFORE UPDATE ON stage_questions
            FOR EACH ROW
            EXECUTE FUNCTION touch_updated_at()
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS stage_questions_touch_updated_at ON stage_questions;"
    )
    # Keep the function — it may be in use by other tables added in later tasks.
    # Uncomment the line below if no other triggers reference it at rollback time:
    # op.execute("DROP FUNCTION IF EXISTS touch_updated_at();")
