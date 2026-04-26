"""pipeline versioning + stage pause + persisted stale + activation states

Adds:
  - job_pipeline_instances.pipeline_version (monotonic per-instance counter)
  - job_pipeline_stages.paused_at (soft-removal state)
  - stage_question_banks.{pipeline_version_at_generation, stage_config_snapshot, is_stale}
  - candidate_job_assignments.entered_at_pipeline_version (forensic stamp)
  - ck_job_postings_status CHECK with new states (pipeline_built, active, archived)

Data migration: any job in 'signals_confirmed' that already has a pipeline
instance is moved to 'pipeline_built' (matches the new auto-apply-removed
front-door flow — see spec §10).

Revision ID: 0018_pipeline_ver_pause
Revises: 0017_sq_updated_at_trigger
Create Date: 2026-04-26
"""
from __future__ import annotations

from alembic import op

revision = "0018_pipeline_ver_pause"
down_revision = "0017_sq_updated_at_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # job_pipeline_instances: monotonic version counter
    op.execute("""
        ALTER TABLE job_pipeline_instances
          ADD COLUMN pipeline_version int NOT NULL DEFAULT 1
    """)

    # job_pipeline_stages: pause state
    op.execute("""
        ALTER TABLE job_pipeline_stages
          ADD COLUMN paused_at timestamptz NULL
    """)
    op.execute("""
        CREATE INDEX ix_job_pipeline_stages_paused_at
          ON job_pipeline_stages (instance_id) WHERE paused_at IS NOT NULL
    """)

    # stage_question_banks: forensic + persisted is_stale
    op.execute("""
        ALTER TABLE stage_question_banks
          ADD COLUMN pipeline_version_at_generation int NULL,
          ADD COLUMN stage_config_snapshot jsonb NULL,
          ADD COLUMN is_stale bool NOT NULL DEFAULT false
    """)

    # Backfill is_stale to match current compute_is_stale (signal-snapshot drift only).
    op.execute("""
        UPDATE stage_question_banks qb
           SET is_stale = COALESCE(
               qb.signal_snapshot_id != (
                   SELECT id FROM job_posting_signal_snapshots
                    WHERE job_posting_id = (
                        SELECT instance.job_posting_id
                          FROM job_pipeline_stages stage
                          JOIN job_pipeline_instances instance ON instance.id = stage.instance_id
                         WHERE stage.id = qb.stage_id
                    )
                    AND confirmed_at IS NOT NULL
                    ORDER BY version DESC LIMIT 1
               ),
               false
           )
    """)

    # candidate_job_assignments: forensic version stamp
    op.execute("""
        ALTER TABLE candidate_job_assignments
          ADD COLUMN entered_at_pipeline_version int NULL
    """)

    # job_postings.status CHECK — net-new constraint
    op.execute("""
        ALTER TABLE job_postings
          ADD CONSTRAINT ck_job_postings_status
          CHECK (status IN ('draft', 'signals_extracting', 'signals_extraction_failed',
                            'signals_extracted', 'signals_confirmed',
                            'pipeline_built', 'active', 'archived'))
    """)

    # Data migration: confirmed jobs that already auto-applied → pipeline_built
    op.execute("""
        UPDATE job_postings
           SET status = 'pipeline_built'
         WHERE status = 'signals_confirmed'
           AND id IN (SELECT job_posting_id FROM job_pipeline_instances)
    """)


def downgrade() -> None:
    # Lossy: pipeline_built/active rows downgrade to signals_confirmed.
    op.execute("""
        UPDATE job_postings
           SET status = 'signals_confirmed'
         WHERE status IN ('pipeline_built', 'active')
    """)
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS ck_job_postings_status")

    op.execute("ALTER TABLE candidate_job_assignments DROP COLUMN IF EXISTS entered_at_pipeline_version")

    op.execute("""
        ALTER TABLE stage_question_banks
          DROP COLUMN IF EXISTS is_stale,
          DROP COLUMN IF EXISTS stage_config_snapshot,
          DROP COLUMN IF EXISTS pipeline_version_at_generation
    """)

    op.execute("DROP INDEX IF EXISTS ix_job_pipeline_stages_paused_at")
    op.execute("ALTER TABLE job_pipeline_stages DROP COLUMN IF EXISTS paused_at")

    op.execute("ALTER TABLE job_pipeline_instances DROP COLUMN IF EXISTS pipeline_version")
