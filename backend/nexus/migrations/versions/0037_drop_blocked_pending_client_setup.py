"""drop blocked_pending_client_setup status

Revision ID: 0037
Revises: 0036
Create Date: 2026-05-14

Retires the ``blocked_pending_client_setup`` job status. The unified
job-creation flow (see docs/superpowers/specs/2026-05-14-unified-job-
creation-flow-design.md) lands every job in ``draft`` regardless of source
(manual or ATS), and the profile-completion gate moves from create-time to
the explicit enrich / extract-signals endpoints.

Because there are no live production tenants, this is a clean cutover —
any existing rows in ``blocked_pending_client_setup`` are migrated to
``draft`` and the value is dropped from the CHECK constraint in the same
migration. The unblock cascade (`_unblock_pending_jobs_for_org_unit` +
the cascade block in `org_units/router.py`) is deleted in application
code in the same commit, so no producer of this status remains after
deploy.

Downgrade restores the CHECK value but cannot reverse the row migration
(we don't know which 'draft' rows used to be 'blocked_pending_client_setup').
Acceptable in pre-production.
"""
from __future__ import annotations

from alembic import op

# revision identifiers
revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


_ALLOWED_STATUSES_AFTER = (
    "draft",
    "signals_extracting",
    "signals_extraction_failed",
    "signals_extracted",
    "signals_confirmed",
    "pipeline_built",
    "active",
    "archived",
)

_ALLOWED_STATUSES_BEFORE = _ALLOWED_STATUSES_AFTER + ("blocked_pending_client_setup",)


def _status_check(values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{v}'" for v in values)
    return (
        "ALTER TABLE job_postings ADD CONSTRAINT ck_job_postings_status "
        f"CHECK (status IN ({rendered}))"
    )


def upgrade() -> None:
    # Migrate any rows on the old status. The unblock cascade may have
    # already cleared most of them on dev DBs; this UPDATE is a no-op
    # when no rows match.
    op.execute(
        "UPDATE job_postings "
        "SET status = 'draft' "
        "WHERE status = 'blocked_pending_client_setup'"
    )
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS ck_job_postings_status")
    op.execute(_status_check(_ALLOWED_STATUSES_AFTER))


def downgrade() -> None:
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS ck_job_postings_status")
    op.execute(_status_check(_ALLOWED_STATUSES_BEFORE))
