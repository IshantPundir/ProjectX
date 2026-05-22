"""Data consistency follow-up to migration 0045.

Revision ID: 0046
Revises: 0045
Create Date: 2026-05-22

Migration 0045 cleared stage_questions (DELETE FROM stage_questions) as part of
its clear-and-regenerate dev-mode taxonomy switch, but did NOT reset the parent
stage_question_banks rows — leaving them at status='confirmed', is_stale=False.
It also left job_postings at status='active' that depended on those now-empty
confirmed banks.

Result: 3 jobs were 'active' with 'confirmed' AI-screening banks that had zero
questions, which (before the companion QuestionBankNotReadyError gate added in
the same commit) would have silently dispatched a candidate into a zero-question
interview.

This migration resets all emptied banks back to a draft/needs-generation state
and reverts the job_postings that depended on them to 'pipeline_built' (the
correct pre-activation status: confirm banks first, then activate).

Both UPDATEs are idempotent: they target only banks with zero question rows, so
a re-run after regeneration + re-activation is a no-op (the banks will have
questions again and the jobs will be 'active' again).
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reset banks that 0045 emptied back to a needs-generation state (a bank
    # with zero questions can't be 'confirmed'). Idempotent: only touches
    # empty banks.
    op.execute(
        """
        UPDATE stage_question_banks b
        SET status = 'draft',
            confirmed_at = NULL,
            confirmed_by = NULL,
            generated_at = NULL,
            generated_by = NULL,
            generation_error = NULL,
            generation_status_by_kind = '{}'::jsonb,
            is_stale = false
        WHERE NOT EXISTS (SELECT 1 FROM stage_questions q WHERE q.bank_id = b.id)
        """
    )
    # Revert jobs that were active on a now-empty bank back to pre-activation
    # ('pipeline_built' -> classifies as "In review": confirm banks, then
    # activate). Idempotent: once regenerated + re-activated, no empty bank
    # matches.
    op.execute(
        """
        UPDATE job_postings
        SET status = 'pipeline_built'
        WHERE status = 'active'
          AND id IN (
            SELECT b.job_posting_id FROM stage_question_banks b
            WHERE NOT EXISTS (SELECT 1 FROM stage_questions q WHERE q.bank_id = b.id)
          )
        """
    )


def downgrade() -> None:
    # Forward-only dev consistency fix: prior confirmed/active states are not
    # stored, so there is nothing to restore. No-op (the 0046 change is
    # data-only, no schema).
    pass
