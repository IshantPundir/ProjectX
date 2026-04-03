-- Migration: Create auth foundation tables
-- Tables: companies, users, user_invites
-- All tables have RLS enabled with tenant isolation + service bypass policies.
-- Source of truth: Notion spec — Auth + Client + User onboarding

-- Step 1: Shared trigger function for updated_at
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

-- Step 2: companies (tenant root — no FKs)
CREATE TABLE public.companies (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT        NOT NULL,
  domain        TEXT,
  industry      TEXT,
  size          TEXT,
  culture_brief TEXT,
  logo_url      TEXT,
  plan          TEXT        NOT NULL DEFAULT 'trial'
                CHECK (plan IN ('trial', 'pro', 'enterprise')),
  onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at    TIMESTAMPTZ
);

CREATE TRIGGER companies_updated_at
  BEFORE UPDATE ON public.companies
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.companies ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_read" ON public.companies FOR SELECT
  USING (id = (SELECT current_setting('app.current_tenant', true)::UUID));

CREATE POLICY "service_write" ON public.companies FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');

-- Step 3: users (one row per client team member)
CREATE TABLE public.users (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_user_id      UUID        NOT NULL UNIQUE,
  tenant_id         UUID        NOT NULL REFERENCES public.companies(id),
  email             TEXT        NOT NULL,
  full_name         TEXT,
  role              TEXT        NOT NULL
                    CHECK (role IN (
                      'Company Admin', 'Recruiter',
                      'Hiring Manager', 'Interviewer', 'Observer'
                    )),
  is_active         BOOLEAN     NOT NULL DEFAULT TRUE,
  notification_prefs JSONB      NOT NULL DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at        TIMESTAMPTZ
);

CREATE INDEX users_tenant_id_idx    ON public.users (tenant_id);
CREATE INDEX users_auth_user_id_idx ON public.users (auth_user_id);

CREATE TRIGGER users_updated_at
  BEFORE UPDATE ON public.users
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.users
  USING (tenant_id = (SELECT current_setting('app.current_tenant', true)::UUID));

CREATE POLICY "service_bypass" ON public.users FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');

-- Step 4: user_invites (invite tracking for both pipelines)
CREATE TABLE public.user_invites (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID        NOT NULL REFERENCES public.companies(id),

  -- Origin: exactly one of these must be set (XOR enforced at DB level)
  invited_by        UUID        REFERENCES public.users(id),
  projectx_admin_id TEXT,

  CONSTRAINT invite_has_exactly_one_origin CHECK (
    (invited_by IS NOT NULL) <> (projectx_admin_id IS NOT NULL)
  ),

  email             TEXT        NOT NULL,
  role              TEXT        NOT NULL
                    CHECK (role IN (
                      'Company Admin', 'Recruiter',
                      'Hiring Manager', 'Interviewer', 'Observer'
                    )),
  token_hash        TEXT        NOT NULL UNIQUE,
  status            TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                      'pending', 'accepted', 'superseded', 'expired', 'revoked'
                    )),
  superseded_by     UUID        REFERENCES public.user_invites(id),
  expires_at        TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '72 hours',
  accepted_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX user_invites_tenant_idx ON public.user_invites (tenant_id);
CREATE INDEX user_invites_token_idx  ON public.user_invites (token_hash);
CREATE INDEX user_invites_email_idx  ON public.user_invites (email, status);

ALTER TABLE public.user_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.user_invites
  USING (tenant_id = (SELECT current_setting('app.current_tenant', true)::UUID));

CREATE POLICY "service_bypass" ON public.user_invites FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');
