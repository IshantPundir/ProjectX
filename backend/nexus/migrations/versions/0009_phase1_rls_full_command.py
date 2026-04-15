"""phase1 tables: full-command tenant_isolation policies

Revision ID: 0009_phase1_rls_full_command
Revises: 0008_audit_log_tenant_insert
Create Date: 2026-04-15

Phase 1 tables (clients, users, organizational_units, user_role_assignments,
user_invites) were initially created with `tenant_isolation` defined as
`FOR SELECT USING (...)`. That only filters rows on SELECT. The companion
`service_bypass` policy uses `USING (app.bypass_rls = 'true')` as its
implicit WITH CHECK, which under tenant-scoped sessions evaluates false and
therefore blocks INSERT/UPDATE/DELETE entirely under real RLS. Migration 0008
fixed this for audit_log by adding a dedicated FOR INSERT WITH CHECK policy;
this migration applies the more general canonical pattern to the remaining
Phase 1 tables.

The canonical pattern (already used by Phase 2A/2B/2C tables like
job_postings, pipeline_*, question_banks) is:

    CREATE POLICY "tenant_isolation" ON <table>
        USING (<tenant_col> = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (<tenant_col> = current_setting('app.current_tenant', true)::uuid)

This provides:
  * row filtering on SELECT/UPDATE/DELETE via USING
  * cross-tenant write blocking on INSERT/UPDATE via WITH CHECK
  * defense-in-depth backstop for any application code that forgets to
    filter by tenant_id in its WHERE clause

The service_bypass policy is untouched; all existing bypass_db code paths
(/complete-invite, /me, /onboarding/complete, admin provision) continue to
work exactly as before.

IMPORTANT — RLS is not actually enforced today. The application connects as
the `postgres` role which has rolbypassrls=true, so every policy on every
table is bypassed at session time. This migration corrects the policies so
that they will Do The Right Thing once the application switches to a role
without the BYPASSRLS attribute. A follow-up migration (0010) creates that
role and a subsequent config change flips DATABASE_URL to use it. This
migration is safe to apply independently and is a no-op under the current
postgres connection.
"""

from alembic import op

revision = "0009_phase1_rls_full_command"
down_revision = "0008_audit_log_tenant_insert"
branch_labels = None
depends_on = None


# (table, tenant column, old policy name)
TABLE_POLICIES = [
    ("clients", "id", "tenant_read"),
    ("users", "tenant_id", "tenant_isolation"),
    ("organizational_units", "client_id", "tenant_isolation"),
    ("user_role_assignments", "tenant_id", "tenant_isolation"),
    ("user_invites", "tenant_id", "tenant_isolation"),
]


def upgrade() -> None:
    for table, col, old_name in TABLE_POLICIES:
        op.execute(f'DROP POLICY IF EXISTS "{old_name}" ON public.{table}')
        op.execute(
            f"""
            CREATE POLICY "tenant_isolation" ON public.{table}
                USING ({col} = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK ({col} = current_setting('app.current_tenant', true)::uuid)
            """
        )


def downgrade() -> None:
    for table, col, old_name in TABLE_POLICIES:
        op.execute(f'DROP POLICY IF EXISTS "tenant_isolation" ON public.{table}')
        op.execute(
            f"""
            CREATE POLICY "{old_name}" ON public.{table}
                FOR SELECT USING ({col} = current_setting('app.current_tenant', true)::UUID)
            """
        )
