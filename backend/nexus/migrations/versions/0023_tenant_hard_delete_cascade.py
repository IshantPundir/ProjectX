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

### Why these 14 and not more ###

Two near-misses are intentionally NOT touched, because the cascade
unwinds correctly without them. Future engineers must understand both
before "tidying up" by adding more CASCADE clauses or re-pointing FKs.

(a) **`users(id)` FKs in tenant-scoped tables get no change.**
    PostgreSQL's default `ON DELETE` action is `NO ACTION` (not
    `RESTRICT`), and `NO ACTION` is checked at end-of-statement. Within
    a single cascading `DELETE FROM clients` statement, by the time
    `NO ACTION` fires its check, every row that would have referenced
    the cascade-deleted users is also gone — because every such row's
    `tenant_id` FK is now `CASCADE` (per item 2 above). The constraint
    trivially passes. So the 14-FK list above is complete and
    sufficient; no `users(id)` FK needs touching. Reference: PostgreSQL
    17 docs on `ON DELETE` actions and `NO ACTION` end-of-statement
    timing (`ddl-constraints.md`, `sql-createtable.md`).

(b) **`clients.super_admin_id` is intentionally untouched.** That FK
    is `DEFERRABLE INITIALLY DEFERRED`. The check fires at commit, by
    which time both the `clients` row and the referenced `users` row
    are gone, so the constraint trivially passes. No CASCADE clause
    needed.

### Note for reviewers — bundled model alignment ###

The accompanying `app/models.py` edits in this commit are not limited
to the AuditLog FK removal that this migration motivates. They also
align the ORM with three earlier migrations whose model edits had
drifted from the DB schema, so that `Base.metadata.create_all()`
(used in tests / fresh local boots) produces a schema matching
production:

  - **0020** dropped `clients.workspace_mode` from the DB; the model
    still had the column. This commit removes it from the model.
  - **0021** added `clients.blocked_at` to the DB; the model didn't
    have it. This commit adds it.
  - **0022** replaced the `users.auth_user_id UNIQUE` constraint with
    a partial unique index (`WHERE auth_user_id IS NOT NULL`); the
    model still had `unique=True`. This commit moves the uniqueness
    declaration into `__table_args__` as an `Index` with
    `postgresql_where`.

These alignment edits are correctness fixes against existing
migrations, not new schema changes — they ship with this commit
purely so the model reflects the on-disk schema after 0023 lands.

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
