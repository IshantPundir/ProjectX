"""drop clients.workspace_mode — agency/enterprise distinction is gone

The workspace_mode column drove a single behavioural branch: blocking
`client_account` org-unit creation outside agency mode. That gate has been
removed — every tenant can now nest `client_account` units regardless of
how they identified during onboarding — so the column has no remaining
read or write sites in application code.

Drops:
  - clients.workspace_mode column (and its CHECK + DEFAULT, which Postgres
    drops automatically with the column)

Downgrade restores the column with the original NOT NULL DEFAULT 'enterprise'
and CHECK constraint so a rollback returns to the prior schema shape. Existing
rows get the default; no agency-mode information is recoverable.

Revision ID: 0020_drop_workspace_mode
Revises: 0019_relax_io_stage_cols
Create Date: 2026-04-26
"""

from alembic import op


revision = "0020_drop_workspace_mode"
down_revision = "0019_relax_io_stage_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.clients DROP COLUMN IF EXISTS workspace_mode")


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.clients
        ADD COLUMN workspace_mode TEXT NOT NULL DEFAULT 'enterprise'
            CHECK (workspace_mode IN ('enterprise', 'agency'))
        """
    )
