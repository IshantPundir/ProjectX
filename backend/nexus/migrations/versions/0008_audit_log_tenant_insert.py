"""audit_log: allow INSERT from tenant-scoped sessions

Revision ID: 0008_audit_log_tenant_insert
Revises: 0007_add_coverage_notes
Create Date: 2026-04-15

The original audit_log RLS (supabase migration 20260405000001) declared
tenant_isolation as FOR SELECT USING (...) which does NOT permit INSERT
from a tenant-scoped session. service_bypass (FOR ALL, no WITH CHECK)
uses its USING as the default INSERT check, which is false for tenant
sessions. Result: every log_event() call from tenant-scoped paths was
silently rejected, and log_event's try/except swallowed the errors —
the audit trail was losing every tenant-scoped mutation.

Fix: add a dedicated FOR INSERT WITH CHECK policy that permits writes
when tenant_id matches the session's app.current_tenant.
"""

from alembic import op

revision = "0008_audit_log_tenant_insert"
down_revision = "0007_add_coverage_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE POLICY "tenant_isolation_insert" ON audit_log
          FOR INSERT
          WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS "tenant_isolation_insert" ON audit_log')
