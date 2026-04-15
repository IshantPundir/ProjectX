"""null-safe current_tenant cast in every tenant_isolation policy

Revision ID: 0011_rls_nullif_tenant
Revises: 0010_create_nexus_app_role
Create Date: 2026-04-15

Every tenant_isolation policy in the schema uses

    <col> = current_setting('app.current_tenant', true)::uuid

which crashes with `invalid input syntax for type uuid: ""` whenever the
GUC holds an empty string. This happens in normal operation because of
a subtle PostgreSQL quirk:

  1. get_tenant_db issues `SET LOCAL app.current_tenant = '<uuid>'` for
     the duration of its transaction.
  2. When the transaction commits or rolls back, SET LOCAL reverts the
     value — but for a custom GUC that was never declared/reset in the
     session before the SET LOCAL, the "previous value" that PostgreSQL
     restores to is the *empty string*, not NULL.
  3. A subsequent bypass_db request hits the same pooled asyncpg
     connection. It sets `app.bypass_rls = 'true'` but NOT
     `app.current_tenant` — the empty string persists on the connection.
  4. Any SELECT on a table with a tenant_isolation policy runs the
     policy expression. Empty string can't cast to uuid. Boom: 500.

This only became visible after migration 0010 + database.py started
issuing `SET LOCAL ROLE nexus_app`. Under the old `postgres` role the
policies were never evaluated at all (rolbypassrls=true), so the
empty-string cast never ran.

Fix: wrap every `current_setting('app.current_tenant', true)::uuid` with
`NULLIF(current_setting('app.current_tenant', true), '')::uuid`. NULLIF
returns NULL for empty string; `NULL::uuid` is NULL (safe); `<col> = NULL`
evaluates to NULL which is treated as false in a policy expression —
which is the desired behaviour: under an unset/empty current_tenant,
no rows are visible through tenant_isolation (service_bypass remains
the only path that grants access).

All tables that carry a current_tenant policy get the same treatment:
  - Phase 1 (from 0009):  clients, users, organizational_units,
                          user_role_assignments, user_invites
  - Phase 1 other:        audit_log (from 0008, USING + WITH CHECK),
                          roles (roles_visibility policy)
  - Phase 2A:             job_postings, job_posting_signal_snapshots, sessions
  - Phase 2C.1 pipelines: pipeline_templates, pipeline_template_stages,
                          job_pipeline_instances, job_pipeline_stages
  - Phase 2C.2 banks:     stage_question_banks, stage_questions

service_bypass policies are NOT touched — they compare a text GUC
against 'true' and the empty-string case is already a no-op false.
"""

from alembic import op

revision = "0011_rls_nullif_tenant"
down_revision = "0010_create_nexus_app_role"
branch_labels = None
depends_on = None


# (table, tenant_col) — policy is always named `tenant_isolation` and uses
# the full-command (no FOR) form as of 0009.
TENANT_ISOLATION_TABLES = [
    ("clients", "id"),
    ("users", "tenant_id"),
    ("organizational_units", "client_id"),
    ("user_role_assignments", "tenant_id"),
    ("user_invites", "tenant_id"),
    ("audit_log", "tenant_id"),
    ("job_postings", "tenant_id"),
    ("job_posting_signal_snapshots", "tenant_id"),
    ("sessions", "tenant_id"),
    ("pipeline_templates", "tenant_id"),
    ("pipeline_template_stages", "tenant_id"),
    ("job_pipeline_instances", "tenant_id"),
    ("job_pipeline_stages", "tenant_id"),
    ("stage_question_banks", "tenant_id"),
    ("stage_questions", "tenant_id"),
]


def _nullif_expr(col: str) -> str:
    return (
        f"{col} = NULLIF(current_setting('app.current_tenant', true), '')::uuid"
    )


def _raw_expr(col: str) -> str:
    return f"{col} = current_setting('app.current_tenant', true)::uuid"


def upgrade() -> None:
    # Full-command tenant_isolation on the tables listed above
    for table, col in TENANT_ISOLATION_TABLES:
        op.execute(f'DROP POLICY IF EXISTS "tenant_isolation" ON public.{table}')
        op.execute(
            f"""
            CREATE POLICY "tenant_isolation" ON public.{table}
                USING ({_nullif_expr(col)})
                WITH CHECK ({_nullif_expr(col)})
            """
        )

    # audit_log also carries a dedicated FOR INSERT policy from migration 0008
    op.execute('DROP POLICY IF EXISTS "tenant_isolation_insert" ON public.audit_log')
    op.execute(
        f"""
        CREATE POLICY "tenant_isolation_insert" ON public.audit_log
            FOR INSERT
            WITH CHECK ({_nullif_expr('tenant_id')})
        """
    )

    # roles uses a bespoke visibility policy that OR's `tenant_id IS NULL`
    # (system roles) with the tenant filter. Patch the tenant filter side.
    op.execute('DROP POLICY IF EXISTS "roles_visibility" ON public.roles')
    op.execute(
        f"""
        CREATE POLICY "roles_visibility" ON public.roles
            FOR SELECT USING (
                tenant_id IS NULL
                OR {_nullif_expr('tenant_id')}
            )
        """
    )


def downgrade() -> None:
    for table, col in TENANT_ISOLATION_TABLES:
        op.execute(f'DROP POLICY IF EXISTS "tenant_isolation" ON public.{table}')
        op.execute(
            f"""
            CREATE POLICY "tenant_isolation" ON public.{table}
                USING ({_raw_expr(col)})
                WITH CHECK ({_raw_expr(col)})
            """
        )

    op.execute('DROP POLICY IF EXISTS "tenant_isolation_insert" ON public.audit_log')
    op.execute(
        f"""
        CREATE POLICY "tenant_isolation_insert" ON public.audit_log
            FOR INSERT
            WITH CHECK ({_raw_expr('tenant_id')})
        """
    )

    op.execute('DROP POLICY IF EXISTS "roles_visibility" ON public.roles')
    op.execute(
        f"""
        CREATE POLICY "roles_visibility" ON public.roles
            FOR SELECT USING (
                tenant_id IS NULL
                OR {_raw_expr('tenant_id')}
            )
        """
    )
