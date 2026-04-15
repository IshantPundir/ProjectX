"""create nexus_app role for non-BYPASSRLS runtime queries

Revision ID: 0010_create_nexus_app_role
Revises: 0009_phase1_rls_full_command
Create Date: 2026-04-15

Creates a dedicated PostgreSQL role `nexus_app` that the FastAPI backend
uses for all per-request queries. Every `get_tenant_db` and `get_bypass_db`
session runs `SET LOCAL ROLE nexus_app` at the top of its transaction
(if `DB_RUNTIME_ROLE` is configured).

The motivation is that in Supabase local (and likely Supabase Cloud) the
default `postgres` role has `rolbypassrls=true`. A role with that attribute
ignores ALL row-level security policies, regardless of what the policies
say. Without this migration + the corresponding database.py change, every
tenant_isolation policy in the schema is a no-op: tenant isolation is
enforced only by application code being correct in its WHERE clauses.

`nexus_app` is created with:
  - NOLOGIN        — can't be connected to directly; only reached via
                     SET LOCAL ROLE from a privileged session
  - NOBYPASSRLS    — row-level security policies are enforced on its
                     queries (this is the whole point)
  - NOSUPERUSER    — obvious
  - NOCREATEDB / NOCREATEROLE — least-privilege

Schema privileges: SELECT/INSERT/UPDATE/DELETE on every current table in
`public`, plus ALTER DEFAULT PRIVILEGES so future tables inherit the same
grants. It does NOT get CREATE on the schema — only postgres (the migration
runner) can add tables.

Membership: postgres is granted nexus_app so that running `SET LOCAL ROLE
nexus_app` inside a postgres session succeeds. Without the GRANT, PG
rejects the role switch with "permission denied to set role".

PRODUCTION WARNING: this migration is safe because it's additive only. Any
deployment that already has migrations applied through 0009 can apply 0010
without restarting the app. The app only starts using nexus_app once the
DB_RUNTIME_ROLE env var is set AND the app is restarted.
"""

from alembic import op

revision = "0010_create_nexus_app_role"
down_revision = "0009_phase1_rls_full_command"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
                CREATE ROLE nexus_app NOLOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;
            END IF;
        END
        $$;
        """
    )

    op.execute("GRANT nexus_app TO postgres")

    op.execute("GRANT USAGE ON SCHEMA public TO nexus_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nexus_app"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nexus_app")

    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexus_app
        """
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO nexus_app
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM nexus_app
        """
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE USAGE, SELECT ON SEQUENCES FROM nexus_app
        """
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM nexus_app"
    )
    op.execute(
        "REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM nexus_app"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM nexus_app")
    op.execute("REVOKE nexus_app FROM postgres")
    op.execute("DROP ROLE IF EXISTS nexus_app")
