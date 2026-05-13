"""job_postings_org_unit_nullable

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-13

Drops the NOT NULL on job_postings.org_unit_id. ATS-imported jobs whose
external_client_id has no matching ats_client_mappings row land here with
org_unit_id=NULL and status='blocked_pending_client_setup'. They show up
on the /jobs list with a 'Not set up' chip — the recruiter wires them to
an org_unit later via a separate flow (not yet built).

The status CHECK constraint is unchanged. NULL org_unit_id is the sole
signal that the job is awaiting a client mapping.
"""
from __future__ import annotations

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("job_postings", "org_unit_id", nullable=True)


def downgrade() -> None:
    # Guard: if any rows have NULL org_unit_id, fail loud instead of silently
    # discarding the constraint mismatch. Operator can either delete the
    # unlinked rows or assign them an org_unit before re-applying NOT NULL.
    op.execute(
        """
        DO $$
        DECLARE
            null_count int;
        BEGIN
            SELECT COUNT(*) INTO null_count FROM job_postings WHERE org_unit_id IS NULL;
            IF null_count > 0 THEN
                RAISE EXCEPTION 'Cannot downgrade: % job_postings have NULL org_unit_id', null_count;
            END IF;
        END$$;
        """
    )
    op.alter_column("job_postings", "org_unit_id", nullable=False)
