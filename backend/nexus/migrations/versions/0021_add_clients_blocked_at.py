"""add clients.blocked_at — tenant-level block/unblock state

Pairs with the existing `deleted_at` column to define three tenant states
enforced at the auth chokepoints (`/api/auth/login` and the
`get_current_user_roles` dependency every authenticated route runs through):

  - active:  blocked_at IS NULL AND deleted_at IS NULL
  - blocked: blocked_at IS NOT NULL AND deleted_at IS NULL  (reversible)
  - deleted: deleted_at IS NOT NULL                          (soft delete)

Soft delete is the only delete this migration supports — hard delete (cascade
through user_invites/org_units/jobs + Supabase auth user removal) is a
separate destructive operation that does not exist yet.

Revision ID: 0021_clients_blocked_at
Revises: 0020_drop_workspace_mode
Create Date: 2026-04-26
"""

from alembic import op


revision = "0021_clients_blocked_at"
down_revision = "0020_drop_workspace_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.clients ADD COLUMN blocked_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.clients DROP COLUMN IF EXISTS blocked_at")
