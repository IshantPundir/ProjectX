"""rename service_role_bypass → service_bypass on Phase 2A/2C tables

Revision ID: 0012_rename_service_bypass
Revises: 0011_rls_nullif_tenant
Create Date: 2026-04-15

CLAUDE.md's canonical policy-pair uses the name `service_bypass`. Phase 1
migrations and several Supabase SQL files follow this. But a handful of
Phase 2A/2C alembic + Supabase migrations shipped with the older name
`service_role_bypass` instead. This is a cosmetic inconsistency today
(both names work — they compare the same GUC), but the startup RLS
completeness check added in I8 wants to look up a single canonical name
per tenant-scoped table.

This migration drops the `service_role_bypass` policy from every affected
table and recreates it as `service_bypass` with the same USING expression.
Idempotent: every DROP uses IF EXISTS, and the recreate is guarded against
the case where `service_bypass` already exists (which it will on
databases that were originally provisioned via the Supabase SQL migrations
that use the canonical name).

The old migration files are NOT edited — migration history is
append-only. Likewise the historical Supabase SQL migration is left
alone; only the live database is adjusted.

Tables affected:
  Alembic 0004 (pipeline builder):
    pipeline_templates, pipeline_template_stages,
    job_pipeline_instances, job_pipeline_stages
  Alembic 0006 (question banks):
    stage_question_banks, stage_questions
  Supabase 20260410000001 (phase 2a job postings):
    job_postings, job_posting_signal_snapshots, sessions
"""

from alembic import op

revision = "0012_rename_service_bypass"
down_revision = "0011_rls_nullif_tenant"
branch_labels = None
depends_on = None


AFFECTED_TABLES = [
    "pipeline_templates",
    "pipeline_template_stages",
    "job_pipeline_instances",
    "job_pipeline_stages",
    "stage_question_banks",
    "stage_questions",
    "job_postings",
    "job_posting_signal_snapshots",
    "sessions",
]


def upgrade() -> None:
    for table in AFFECTED_TABLES:
        # Drop the old name if present.
        op.execute(
            f'DROP POLICY IF EXISTS "service_role_bypass" ON public.{table}'
        )
        # Drop the new name if it already exists (fresh Supabase installs
        # provisioned via the canonical SQL migration) so the CREATE is
        # safe to run against both paths.
        op.execute(
            f'DROP POLICY IF EXISTS "service_bypass" ON public.{table}'
        )
        op.execute(
            f"""
            CREATE POLICY "service_bypass" ON public.{table}
                USING (current_setting('app.bypass_rls', true) = 'true')
            """
        )


def downgrade() -> None:
    # Restore the old inconsistent name. Pure cosmetic rollback — the
    # policy expression is unchanged.
    for table in AFFECTED_TABLES:
        op.execute(
            f'DROP POLICY IF EXISTS "service_bypass" ON public.{table}'
        )
        op.execute(
            f"""
            CREATE POLICY "service_role_bypass" ON public.{table}
                USING (current_setting('app.bypass_rls', true) = 'true')
            """
        )
