"""drop audit_log FKs + convert tenant_id FKs to ON DELETE CASCADE

The hard-delete operation issues `DELETE FROM clients WHERE id = ?` and
relies on Postgres to cascade through every tenant-scoped table. This
migration:

  1. Drops `audit_log_tenant_id_fkey` and `audit_log_actor_id_fkey` so
     audit history outlives the rows it references.
  2. Replaces the default-NO-ACTION FKs on 14 tenant-scoped tables with
     `ON DELETE CASCADE` so the cascade actually unwinds. The 5 newer
     Phase-3 tables already have CASCADE and are not touched.

Pattern per FK: ALTER TABLE DROP CONSTRAINT, ALTER TABLE ADD CONSTRAINT
with the same name and column, with `ON DELETE CASCADE` appended.

Revision ID: 0023_tenant_hard_delete_cascade
Revises: 0022_users_partial_unique_auth
Create Date: 2026-04-26
"""

from alembic import op


revision = "0023_tenant_hard_delete_cascade"
down_revision = "0022_users_partial_unique_auth"
branch_labels = None
depends_on = None


# (table, constraint name, referencing column)
_FKS_TO_CASCADE: list[tuple[str, str, str]] = [
    ("users", "users_tenant_id_fkey", "tenant_id"),
    ("user_invites", "user_invites_tenant_id_fkey", "tenant_id"),
    ("user_role_assignments", "user_role_assignments_tenant_id_fkey", "tenant_id"),
    # organizational_units uses `client_id`, not `tenant_id` — older naming.
    ("organizational_units", "organizational_units_client_id_fkey", "client_id"),
    ("roles", "roles_tenant_id_fkey", "tenant_id"),
    ("job_postings", "job_postings_tenant_id_fkey", "tenant_id"),
    ("job_posting_signal_snapshots", "job_posting_signal_snapshots_tenant_id_fkey", "tenant_id"),
    ("sessions", "sessions_tenant_id_fkey", "tenant_id"),
    ("pipeline_templates", "pipeline_templates_tenant_id_fkey", "tenant_id"),
    ("pipeline_template_stages", "pipeline_template_stages_tenant_id_fkey", "tenant_id"),
    ("job_pipeline_instances", "job_pipeline_instances_tenant_id_fkey", "tenant_id"),
    ("job_pipeline_stages", "job_pipeline_stages_tenant_id_fkey", "tenant_id"),
    # Non-standard naming — older convention.
    ("stage_question_banks", "fk_stage_question_banks_tenant", "tenant_id"),
    ("stage_questions", "fk_stage_questions_tenant", "tenant_id"),
]


def upgrade() -> None:
    # 1. Drop audit_log FKs.
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT IF EXISTS audit_log_tenant_id_fkey")
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT IF EXISTS audit_log_actor_id_fkey")

    # 2. Convert tenant FKs to CASCADE.
    for table, constraint, col in _FKS_TO_CASCADE:
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(
            f"ALTER TABLE public.{table} "
            f"ADD CONSTRAINT {constraint} FOREIGN KEY ({col}) "
            f"REFERENCES public.clients(id) ON DELETE CASCADE"
        )


def downgrade() -> None:
    # Reverse 2: restore the no-cascade FKs.
    for table, constraint, col in _FKS_TO_CASCADE:
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(
            f"ALTER TABLE public.{table} "
            f"ADD CONSTRAINT {constraint} FOREIGN KEY ({col}) "
            f"REFERENCES public.clients(id)"
        )

    # Reverse 1: restore audit_log FKs.
    op.execute(
        "ALTER TABLE public.audit_log "
        "ADD CONSTRAINT audit_log_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES public.clients(id)"
    )
    op.execute(
        "ALTER TABLE public.audit_log "
        "ADD CONSTRAINT audit_log_actor_id_fkey "
        "FOREIGN KEY (actor_id) REFERENCES public.users(id)"
    )
