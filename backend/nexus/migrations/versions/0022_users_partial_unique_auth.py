"""make users.auth_user_id unique only among non-deleted rows

When a tenant is soft-deleted, we cascade-soft-delete its users (see
admin/service.py::delete_client). But the original schema put a plain
UNIQUE on users.auth_user_id, which doesn't know about lifecycle state.

The result: re-inviting the same admin email after soft-deleting their
tenant reused the same Supabase Auth identity (correct — `accept_invite`
calls find_user_by_email + reset_password) but then tried to INSERT a
NEW users row with the SAME auth_user_id for the NEW tenant. Plain
UNIQUE blocked that insert with `users_auth_user_id_key` violation,
breaking re-onboarding.

A partial unique index — enforced only WHERE deleted_at IS NULL — is
the standard soft-delete pattern. Old soft-deleted rows preserve the
audit trail without blocking re-creation.

Revision ID: 0022_users_partial_unique_auth
Revises: 0021_clients_blocked_at
Create Date: 2026-04-26
"""

from alembic import op


revision = "0022_users_partial_unique_auth"
down_revision = "0021_clients_blocked_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Drop the old plain UNIQUE so the backfill below isn't blocked.
    op.execute(
        "ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_auth_user_id_key"
    )

    # 2) Backfill: apply the cascade to any clients that were already
    #    soft-deleted before this migration shipped. Without this step,
    #    those tenants' users rows still have deleted_at IS NULL and would
    #    collide with the new partial index when the same Supabase Auth
    #    identity is rebound to a fresh tenant. Idempotent — re-runs are
    #    no-ops because of the deleted_at IS NULL / status = 'pending'
    #    filters.
    op.execute(
        """
        UPDATE public.users
           SET deleted_at = NOW(),
               is_active  = FALSE,
               updated_at = NOW()
         WHERE deleted_at IS NULL
           AND tenant_id IN (
               SELECT id FROM public.clients WHERE deleted_at IS NOT NULL
           )
        """
    )
    op.execute(
        """
        UPDATE public.user_invites
           SET status = 'revoked'
         WHERE status = 'pending'
           AND tenant_id IN (
               SELECT id FROM public.clients WHERE deleted_at IS NOT NULL
           )
        """
    )

    # 3) Create the partial unique index. Now safe — the only auth_user_id
    #    rows with deleted_at IS NULL are those whose tenant is still
    #    active.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_auth_user_id_active_uniq "
        "ON public.users (auth_user_id) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.users_auth_user_id_active_uniq")
    op.execute(
        "ALTER TABLE public.users "
        "ADD CONSTRAINT users_auth_user_id_key UNIQUE (auth_user_id)"
    )
