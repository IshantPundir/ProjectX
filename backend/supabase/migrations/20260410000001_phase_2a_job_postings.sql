-- =============================================================
-- Phase 2A — job_postings, job_posting_signal_snapshots,
--            sessions stub, set_updated_at() trigger function
-- =============================================================

-- ------------------------------------------------------------
-- Reusable updated_at trigger function.
-- Phase 1 never created one, so Phase 1 tables' updated_at columns
-- are frozen at creation time. Retrofitting Phase 1 tables is a
-- separate cross-cutting cleanup (see Deferred Hardening #10).
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ------------------------------------------------------------
-- job_postings
-- State machine values in 2A:
--   draft, signals_extracting, signals_extraction_failed, signals_extracted
-- ------------------------------------------------------------

CREATE TABLE job_postings (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL REFERENCES clients(id),
    org_unit_id               UUID NOT NULL REFERENCES organizational_units(id),
    title                     TEXT NOT NULL,
    description_raw           TEXT NOT NULL,
    project_scope_raw         TEXT,
    description_enriched      TEXT,
    enriched_manually_edited  BOOLEAN NOT NULL DEFAULT FALSE,
    status                    TEXT NOT NULL DEFAULT 'draft',
    status_error              TEXT,
    source                    TEXT NOT NULL DEFAULT 'native',
    external_id               TEXT,
    target_headcount          INTEGER,
    deadline                  DATE,
    created_by                UUID NOT NULL REFERENCES users(id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_job_postings_tenant_org_unit ON job_postings (tenant_id, org_unit_id);
CREATE INDEX idx_job_postings_status          ON job_postings (tenant_id, status);
CREATE INDEX idx_job_postings_created_at      ON job_postings (tenant_id, created_at DESC);

-- updated_at trigger — fires BEFORE UPDATE, stamps NOW() on every modification
CREATE TRIGGER set_job_postings_updated_at
    BEFORE UPDATE ON job_postings
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE job_postings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON job_postings
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON job_postings
  USING (current_setting('app.bypass_rls', true) = 'true');

-- ------------------------------------------------------------
-- job_posting_signal_snapshots
-- ------------------------------------------------------------

CREATE TABLE job_posting_signal_snapshots (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES clients(id),
    job_posting_id        UUID NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    version               INTEGER NOT NULL,
    required_skills       JSONB NOT NULL,
    preferred_skills      JSONB NOT NULL,
    must_haves            JSONB NOT NULL,
    good_to_haves         JSONB NOT NULL,
    min_experience_years  INTEGER NOT NULL,
    seniority_level       TEXT NOT NULL,
    role_summary          TEXT NOT NULL,
    confirmed_by          UUID REFERENCES users(id),
    confirmed_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (job_posting_id, version)
);

CREATE INDEX idx_signal_snapshots_job_posting
    ON job_posting_signal_snapshots (job_posting_id, version DESC);

ALTER TABLE job_posting_signal_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON job_posting_signal_snapshots
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON job_posting_signal_snapshots
  USING (current_setting('app.bypass_rls', true) = 'true');

-- ------------------------------------------------------------
-- sessions stub — defined now so Phase 3 FKs have a parent.
-- candidate_id column exists but NO FK constraint until Phase 3 creates
-- the candidates table.
-- ------------------------------------------------------------

CREATE TABLE sessions (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL REFERENCES clients(id),
    job_posting_id            UUID NOT NULL REFERENCES job_postings(id),
    candidate_id              UUID,  -- FK deferred to Phase 3
    status                    TEXT NOT NULL DEFAULT 'scheduled',
    started_at                TIMESTAMPTZ,
    completed_at              TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON sessions
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON sessions
  USING (current_setting('app.bypass_rls', true) = 'true');
