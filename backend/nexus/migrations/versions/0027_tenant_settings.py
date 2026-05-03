"""Phase 5 — tenant_settings table + sessions.knockout_failures column.

Two additive operations:

1. ``tenant_settings`` (NEW): per-tenant configuration carrying
   ``engine_knockout_policy`` (record_only | close_polite, default
   record_only) and ``engine_agent_name`` (nullable; null means use
   ``settings.engine_agent_name`` env fallback). PK = tenant_id, FK
   ``clients(id) ON DELETE CASCADE`` matches migration 0023's hard-delete
   discipline. Canonical RLS policy pair with NULLIF.

   No backfill — lazy-default pattern: ``get_tenant_settings`` returns
   the default ``TenantSettings(...)`` when no row exists, so existing
   tenants need not have a row inserted. When the future recruiter UI
   to edit settings ships, the first edit creates the row via UPSERT.

2. ``sessions.knockout_failures`` (NEW column): JSONB array, default
   ``'[]'::jsonb``, NOT NULL. Stores the engine's ``KnockoutFailure``
   list in queryable form for Phase 3D analytics + EEOC fairness review
   (``WHERE knockout_failures != '[]'`` is a one-line filter). PG11+
   metadata-only column add — no table rewrite.

Revision ID: 0027_tenant_settings
Revises: 0026_question_kind_column
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0027_tenant_settings"
down_revision: str | None = "0026_question_kind_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "engine_knockout_policy",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'record_only'"),
        ),
        sa.Column("engine_agent_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "engine_knockout_policy IN ('record_only', 'close_polite')",
            name="ck_tenant_settings_engine_knockout_policy",
        ),
    )

    # Enable RLS + canonical policy pair (with NULLIF discipline).
    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY "tenant_isolation" ON tenant_settings
          USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY "service_bypass" ON tenant_settings
          USING (current_setting('app.bypass_rls', true) = 'true');
        """
    )

    # Grant the nexus_app runtime role explicit DML on the new table
    # (matches the discipline from migration 0010_create_nexus_app_role).
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_settings TO nexus_app;")

    # sessions.knockout_failures — additive column add. PG11+ metadata-only.
    op.add_column(
        "sessions",
        sa.Column(
            "knockout_failures",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "knockout_failures")
    op.execute('DROP POLICY IF EXISTS "service_bypass" ON tenant_settings;')
    op.execute('DROP POLICY IF EXISTS "tenant_isolation" ON tenant_settings;')
    op.drop_table("tenant_settings")
