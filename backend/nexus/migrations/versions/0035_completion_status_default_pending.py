"""completion_status default pending

Revision ID: 0035
Revises: 0034
Create Date: 2026-05-14

Flips the DB default for organizational_units.company_profile_completion_status
from 'complete' to 'pending'. Under the column-level profile model
(migration 0034), newly-created units have NULL about/industry/hiring_bar
and must start as 'pending'. update_org_unit's derive_completion_status
will flip the status to 'complete' once all three strict-profile columns
are filled.

The old default of 'complete' was correct when company_profile was a
required JSONB validated at create time (recruiter filled the strict
4-field form in the onboarding wizard). After 0034 made the strict
fields optional column-level TEXT, the default became misleading:
a recruiter could create a client_account with no profile fields and
the JD creation gate would let work proceed against an empty profile.
"""
from __future__ import annotations

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE organizational_units "
        "ALTER COLUMN company_profile_completion_status SET DEFAULT 'pending'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE organizational_units "
        "ALTER COLUMN company_profile_completion_status SET DEFAULT 'complete'"
    )
