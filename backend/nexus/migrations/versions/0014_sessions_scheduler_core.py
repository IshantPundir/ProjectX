"""Sessions upgrade + candidate_session_tokens + stages.otp_required_default.

Revision ID: 0014_sessions_scheduler_core
Revises: 0013_candidates_core
Create Date: 2026-04-20

Phase 3C.1 schema foundation:
- job_pipeline_stages gains `otp_required_default BOOLEAN NOT NULL DEFAULT FALSE`
- The Phase 2A sessions stub is truncated and re-shaped for the full state machine
- candidate_session_tokens is new: single-use JWT tracking + audit.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_sessions_scheduler_core"
down_revision = "0013_candidates_core"
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
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO nexus_app")


def upgrade() -> None:
    # 1. New default column on pipeline stages
    op.execute("""
        ALTER TABLE public.job_pipeline_stages
          ADD COLUMN otp_required_default BOOLEAN NOT NULL DEFAULT FALSE
    """)

    # 2. Sessions upgrade — the Phase 2A stub was never written to in prod.
    #    Drop the RLS policies first (recreated at the end), truncate rows
    #    (safe per spec), then reshape columns.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.sessions")
    op.execute("DROP POLICY IF EXISTS service_bypass ON public.sessions")
    op.execute("DROP POLICY IF EXISTS service_role_bypass ON public.sessions")
    op.execute("TRUNCATE TABLE public.sessions")

    # Drop stub columns that do not survive the reshape
    op.execute("ALTER TABLE public.sessions DROP COLUMN IF EXISTS candidate_id")
    op.execute("ALTER TABLE public.sessions DROP COLUMN IF EXISTS status")

    # Add the new columns (all nullable first so the ALTER succeeds with zero rows;
    # NOT NULL constraints applied after defaults fill in).
    op.execute("""
        ALTER TABLE public.sessions
          ADD COLUMN assignment_id     UUID,
          ADD COLUMN stage_id          UUID,
          ADD COLUMN state             TEXT NOT NULL DEFAULT 'created',
          ADD COLUMN state_changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          ADD COLUMN updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          ADD COLUMN consent_recorded_at TIMESTAMPTZ,
          ADD COLUMN otp_required      BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN otp_hash          TEXT,
          ADD COLUMN otp_issued_at     TIMESTAMPTZ,
          ADD COLUMN otp_attempts      INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN otp_verified_at   TIMESTAMPTZ,
          ADD COLUMN scheduled_for     TIMESTAMPTZ,
          ADD COLUMN livekit_room_name TEXT,
          ADD COLUMN recording_s3_key  TEXT,
          ADD COLUMN created_by        UUID
    """)

    # Foreign keys + not-null tightening
    op.execute("""
        ALTER TABLE public.sessions
          ADD CONSTRAINT sessions_assignment_fk
            FOREIGN KEY (assignment_id) REFERENCES public.candidate_job_assignments(id)
            ON DELETE CASCADE,
          ADD CONSTRAINT sessions_stage_fk
            FOREIGN KEY (stage_id) REFERENCES public.job_pipeline_stages(id),
          ADD CONSTRAINT sessions_created_by_fk
            FOREIGN KEY (created_by) REFERENCES public.users(id),
          ADD CONSTRAINT sessions_state_check
            CHECK (state IN ('created','pre_check','consented','active','completed','cancelled','error')),
          ALTER COLUMN assignment_id SET NOT NULL,
          ALTER COLUMN stage_id SET NOT NULL,
          ALTER COLUMN created_by SET NOT NULL
    """)

    # Drop the old job_posting_id column if it still exists — stub had it
    op.execute("ALTER TABLE public.sessions DROP COLUMN IF EXISTS job_posting_id")

    op.execute("""
        CREATE INDEX sessions_tenant_assignment_state_idx
          ON public.sessions (tenant_id, assignment_id, state)
    """)
    op.execute("""
        CREATE INDEX sessions_pending_invites_idx
          ON public.sessions (tenant_id, state, state_changed_at DESC)
          WHERE state IN ('created','pre_check','consented')
    """)
    op.execute("""
        CREATE TRIGGER sessions_set_updated_at
          BEFORE UPDATE ON public.sessions
          FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
    """)
    _apply_canonical_rls("sessions")

    # 3. candidate_session_tokens — new
    op.execute("""
        CREATE TABLE public.candidate_session_tokens (
            jti            UUID PRIMARY KEY,
            tenant_id      UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
            session_id     UUID NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
            issued_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at     TIMESTAMPTZ NOT NULL,
            used_at        TIMESTAMPTZ,
            used_ip        INET,
            used_user_agent TEXT,
            superseded_at  TIMESTAMPTZ,
            superseded_by  UUID REFERENCES public.candidate_session_tokens(jti)
        )
    """)
    op.execute("""
        CREATE INDEX candidate_session_tokens_tenant_session_idx
          ON public.candidate_session_tokens (tenant_id, session_id)
    """)
    op.execute("""
        CREATE INDEX candidate_session_tokens_reap_idx
          ON public.candidate_session_tokens (tenant_id, expires_at)
          WHERE used_at IS NULL AND superseded_at IS NULL
    """)
    _apply_canonical_rls("candidate_session_tokens")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.candidate_session_tokens CASCADE")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.sessions")
    op.execute("DROP POLICY IF EXISTS service_bypass ON public.sessions")
    op.execute("DROP TRIGGER IF EXISTS sessions_set_updated_at ON public.sessions")
    op.execute("DROP INDEX IF EXISTS public.sessions_tenant_assignment_state_idx")
    op.execute("DROP INDEX IF EXISTS public.sessions_pending_invites_idx")
    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_state_check")
    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_created_by_fk")
    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_stage_fk")
    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_assignment_fk")
    # Drop all new columns we added
    op.execute("""
        ALTER TABLE public.sessions
          DROP COLUMN IF EXISTS created_by,
          DROP COLUMN IF EXISTS recording_s3_key,
          DROP COLUMN IF EXISTS livekit_room_name,
          DROP COLUMN IF EXISTS scheduled_for,
          DROP COLUMN IF EXISTS otp_verified_at,
          DROP COLUMN IF EXISTS otp_attempts,
          DROP COLUMN IF EXISTS otp_issued_at,
          DROP COLUMN IF EXISTS otp_hash,
          DROP COLUMN IF EXISTS otp_required,
          DROP COLUMN IF EXISTS consent_recorded_at,
          DROP COLUMN IF EXISTS state_changed_at,
          DROP COLUMN IF EXISTS updated_at,
          DROP COLUMN IF EXISTS state,
          DROP COLUMN IF EXISTS stage_id,
          DROP COLUMN IF EXISTS assignment_id
    """)
    # Restore the 2A stub shape minimally
    op.execute("""
        ALTER TABLE public.sessions
          ADD COLUMN IF NOT EXISTS job_posting_id UUID NOT NULL REFERENCES public.job_postings(id),
          ADD COLUMN IF NOT EXISTS candidate_id UUID,
          ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'scheduled'
    """)
    # Recreate the basic stub RLS policies
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.sessions
          USING ({_TENANT_FILTER})
          WITH CHECK ({_TENANT_FILTER})
    """)
    op.execute("""
        CREATE POLICY service_bypass ON public.sessions
          USING (current_setting('app.bypass_rls', true) = 'true')
    """)

    op.execute("""
        ALTER TABLE public.job_pipeline_stages
          DROP COLUMN IF EXISTS otp_required_default
    """)
