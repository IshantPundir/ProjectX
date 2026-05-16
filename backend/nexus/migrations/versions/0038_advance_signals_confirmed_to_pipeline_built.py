"""advance signals_confirmed jobs to pipeline_built (one-shot backfill)

`jd.confirm_signals` now auto-creates the bookend Intake → Debrief pipeline
and advances the job to `pipeline_built` in the same transaction (spec
`docs/superpowers/specs/2026-05-15-job-activation-gate-design.md`). Any
existing job left in `signals_confirmed` from before this change needs the
same treatment so the new "In review" chip + activation banner show up
correctly.

This is a no-op when there are zero `signals_confirmed` rows. Safe to run
unconditionally.

Revision ID: 0038
Revises: 0037
Create Date: 2026-05-15
"""
from __future__ import annotations

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Create a JobPipelineInstance for every signals_confirmed job
    #    that does not already have one. pipeline_version defaults to 1.
    op.execute(
        """
        INSERT INTO job_pipeline_instances (
            id, tenant_id, job_posting_id, source_template_id,
            pipeline_version, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            j.tenant_id,
            j.id,
            NULL,
            1,
            now(),
            now()
        FROM job_postings j
        LEFT JOIN job_pipeline_instances i ON i.job_posting_id = j.id
        WHERE j.status = 'signals_confirmed'
          AND i.id IS NULL
        """
    )

    # 2) Insert the Intake bookend stage for every instance that has no
    #    stages yet (covers both the rows we just created above and any
    #    pre-existing empty instances). job_pipeline_stages has no
    #    updated_at column — only created_at.
    op.execute(
        """
        INSERT INTO job_pipeline_stages (
            id, tenant_id, instance_id, position, name, stage_type,
            duration_minutes, difficulty, signal_filter, pass_criteria,
            advance_behavior, sla_days, otp_required_default,
            created_at
        )
        SELECT
            gen_random_uuid(),
            i.tenant_id,
            i.id,
            0,
            'Intake',
            'intake',
            NULL, NULL, NULL, NULL,
            'auto_advance',
            NULL,
            FALSE,
            now()
        FROM job_pipeline_instances i
        JOIN job_postings j ON j.id = i.job_posting_id
        WHERE j.status = 'signals_confirmed'
          AND NOT EXISTS (
            SELECT 1 FROM job_pipeline_stages s WHERE s.instance_id = i.id
          )
        """
    )

    # 3) Insert the Debrief bookend stage. Same idempotency guard — only
    #    insert if no debrief stage exists for the instance yet.
    op.execute(
        """
        INSERT INTO job_pipeline_stages (
            id, tenant_id, instance_id, position, name, stage_type,
            duration_minutes, difficulty, signal_filter, pass_criteria,
            advance_behavior, sla_days, otp_required_default,
            created_at
        )
        SELECT
            gen_random_uuid(),
            i.tenant_id,
            i.id,
            1,
            'Debrief',
            'debrief',
            NULL, NULL, NULL, NULL,
            'manual_review',
            NULL,
            FALSE,
            now()
        FROM job_pipeline_instances i
        JOIN job_postings j ON j.id = i.job_posting_id
        WHERE j.status = 'signals_confirmed'
          AND NOT EXISTS (
            SELECT 1 FROM job_pipeline_stages s
            WHERE s.instance_id = i.id AND s.stage_type = 'debrief'
          )
        """
    )

    # 4) Advance every signals_confirmed job to pipeline_built. The
    #    state-machine CHECK constraint allows the transition (see
    #    migration 0018's broadened status list).
    op.execute(
        "UPDATE job_postings SET status = 'pipeline_built' "
        "WHERE status = 'signals_confirmed'"
    )


def downgrade() -> None:
    # No-op. Reverting status would require dropping the bookend stages,
    # which is destructive. If we need to revert, do it manually with
    # the recruiter in the loop.
    pass
