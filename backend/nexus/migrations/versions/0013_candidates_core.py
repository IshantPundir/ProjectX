"""Candidates core: candidates, candidate_job_assignments, candidate_stage_progress + RLS + permission seed.

Revision ID: 0013_candidates_core
Revises: 0012_rename_service_bypass
Create Date: 2026-04-19

Adds the three core tables for Phase 3B candidate management:

  - candidates                   — tenant-scoped candidate record (PII + resume ref)
  - candidate_job_assignments    — candidate ↔ job_posting link with current stage + status
  - candidate_stage_progress     — append-only per-stage transition log

All three tables get the canonical RLS policy pair (`tenant_isolation` with
USING + WITH CHECK, and `service_bypass`) and are granted full DML to the
`nexus_app` runtime role. The tenant filter wraps `current_setting` in
`NULLIF(..., '')::uuid` to avoid the empty-string cast crash on pooled
connections (see CLAUDE.md § RLS Pattern).

Also seeds the `candidates.manage` permission onto Admin + Recruiter system
roles, and defensively upserts `candidates.view` onto Admin, Recruiter, and
Hiring Manager. Permissions are stored as a JSONB array on `public.roles.permissions`.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers
revision = "0013_candidates_core"
down_revision = "0012_rename_service_bypass"
branch_labels = None
depends_on = None


_TENANT_FILTER = (
    "tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"
)


def _apply_canonical_rls(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.{table}
          USING ({_TENANT_FILTER})
          WITH CHECK ({_TENANT_FILTER})
    """)
    op.execute(f"""
        CREATE POLICY service_bypass ON public.{table}
          USING (current_setting('app.bypass_rls', true) = 'true')
    """)
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO nexus_app"
    )


def upgrade() -> None:
    # candidates
    op.execute("""
        CREATE TABLE public.candidates (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            email           TEXT NOT NULL,
            phone           TEXT,
            location        TEXT,
            current_title   TEXT,
            linkedin_url    TEXT,
            resume_s3_key   TEXT,
            resume_uploaded_at TIMESTAMPTZ,
            notes           TEXT,
            source          TEXT NOT NULL,
            external_id     TEXT,
            source_metadata JSONB,
            created_by      UUID NOT NULL REFERENCES public.users(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            pii_redacted_at TIMESTAMPTZ,
            pii_redacted_by UUID REFERENCES public.users(id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX candidates_tenant_email_active_idx
          ON public.candidates (tenant_id, email)
          WHERE pii_redacted_at IS NULL
    """)
    op.execute(
        "CREATE INDEX candidates_tenant_created_idx "
        "ON public.candidates (tenant_id, created_at DESC)"
    )
    op.execute("""
        CREATE TRIGGER candidates_set_updated_at
          BEFORE UPDATE ON public.candidates
          FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
    """)
    _apply_canonical_rls("candidates")

    # candidate_job_assignments
    op.execute("""
        CREATE TABLE public.candidate_job_assignments (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
            candidate_id       UUID NOT NULL REFERENCES public.candidates(id) ON DELETE CASCADE,
            job_posting_id     UUID NOT NULL REFERENCES public.job_postings(id) ON DELETE CASCADE,
            current_stage_id   UUID NOT NULL REFERENCES public.job_pipeline_stages(id),
            status             TEXT NOT NULL DEFAULT 'active',
            status_changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_by        UUID NOT NULL REFERENCES public.users(id),
            assigned_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT candidate_job_assignments_status_check
              CHECK (status IN ('active','archived','hired','rejected','withdrawn')),
            CONSTRAINT candidate_job_assignments_unique_candidate_job
              UNIQUE (candidate_id, job_posting_id)
        )
    """)
    op.execute("""
        CREATE INDEX candidate_job_assignments_tenant_job_status_idx
          ON public.candidate_job_assignments (tenant_id, job_posting_id, status)
    """)
    op.execute(
        "CREATE INDEX candidate_job_assignments_candidate_idx "
        "ON public.candidate_job_assignments (candidate_id)"
    )
    op.execute("""
        CREATE TRIGGER candidate_job_assignments_set_updated_at
          BEFORE UPDATE ON public.candidate_job_assignments
          FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
    """)
    _apply_canonical_rls("candidate_job_assignments")

    # candidate_stage_progress
    op.execute("""
        CREATE TABLE public.candidate_stage_progress (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
            assignment_id  UUID NOT NULL REFERENCES public.candidate_job_assignments(id) ON DELETE CASCADE,
            stage_id       UUID NOT NULL REFERENCES public.job_pipeline_stages(id),
            entered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            exited_at      TIMESTAMPTZ,
            outcome        TEXT,
            moved_by       UUID REFERENCES public.users(id),
            override       BOOLEAN NOT NULL DEFAULT FALSE,
            reason         TEXT,
            CONSTRAINT candidate_stage_progress_outcome_check
              CHECK (outcome IN ('advanced','rejected','withdrawn') OR outcome IS NULL)
        )
    """)
    op.execute("""
        CREATE INDEX candidate_stage_progress_current_idx
          ON public.candidate_stage_progress (tenant_id, stage_id)
          WHERE exited_at IS NULL
    """)
    op.execute("""
        CREATE INDEX candidate_stage_progress_assignment_idx
          ON public.candidate_stage_progress (assignment_id, entered_at DESC)
    """)
    _apply_canonical_rls("candidate_stage_progress")

    # Seed candidates.view + candidates.manage permissions onto system roles.
    # Permissions are stored as a JSONB array on public.roles.permissions.
    # `candidates.view` already ships on Admin / Recruiter / Hiring Manager from
    # the initial schema seed, but we defensively upsert here so the migration is
    # idempotent against cluster variants.
    op.execute("""
        UPDATE public.roles
        SET permissions = permissions || '"candidates.manage"'::jsonb
        WHERE tenant_id IS NULL
          AND name IN ('Admin', 'Recruiter')
          AND NOT (permissions @> '["candidates.manage"]'::jsonb)
    """)
    op.execute("""
        UPDATE public.roles
        SET permissions = permissions || '"candidates.view"'::jsonb
        WHERE tenant_id IS NULL
          AND name IN ('Admin', 'Recruiter', 'Hiring Manager')
          AND NOT (permissions @> '["candidates.view"]'::jsonb)
    """)


def downgrade() -> None:
    # Downgrade removes candidates.manage (the permission this migration introduces).
    # candidates.view predates this migration, so we leave it in place.
    op.execute("""
        UPDATE public.roles
        SET permissions = permissions - 'candidates.manage'
        WHERE tenant_id IS NULL
          AND name IN ('Admin', 'Recruiter')
    """)
    op.execute("DROP TABLE IF EXISTS public.candidate_stage_progress CASCADE")
    op.execute("DROP TABLE IF EXISTS public.candidate_job_assignments CASCADE")
    op.execute("DROP TABLE IF EXISTS public.candidates CASCADE")
