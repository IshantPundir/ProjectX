-- ============================================================
-- Phase 6 Migration: Hierarchical Permission System
-- 1. Rename public.companies → public.clients
-- 2. Create public.organizational_units
-- 3. Add permission columns to public.users
-- 4. Add permission columns to public.user_invites
-- 5. Update role CHECK constraint on public.users
-- ============================================================

-- Step 1: Rename companies → clients
ALTER TABLE public.companies RENAME TO clients;
ALTER TRIGGER companies_updated_at ON public.clients RENAME TO clients_updated_at;
ALTER POLICY "tenant_read" ON public.clients RENAME TO "client_tenant_read";
ALTER POLICY "service_write" ON public.clients RENAME TO "client_service_write";

-- Step 2: Create organizational_units
CREATE TABLE public.organizational_units (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id      UUID        NOT NULL REFERENCES public.clients(id),
  parent_unit_id UUID        REFERENCES public.organizational_units(id),
  name           TEXT        NOT NULL,
  unit_type      TEXT        NOT NULL
                 CHECK (unit_type IN (
                   'client_account', 'department', 'team', 'branch', 'region'
                 )),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ou_client_id_idx     ON public.organizational_units (client_id);
CREATE INDEX ou_parent_unit_id_idx ON public.organizational_units (parent_unit_id);

CREATE TRIGGER organizational_units_updated_at
  BEFORE UPDATE ON public.organizational_units
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.organizational_units ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.organizational_units
  USING (client_id = (SELECT current_setting('app.current_tenant', true)::UUID));

CREATE POLICY "service_bypass" ON public.organizational_units FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');

-- Step 3: Add permission columns to public.users
ALTER TABLE public.users
  ADD COLUMN parent_user_id UUID REFERENCES public.users(id),
  ADD COLUMN org_unit_id    UUID REFERENCES public.organizational_units(id),
  ADD COLUMN permissions    JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN is_admin       BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX users_org_unit_id_idx  ON public.users (org_unit_id);
CREATE INDEX users_parent_user_idx  ON public.users (parent_user_id);

-- Update role CHECK to include 'Admin'
ALTER TABLE public.users DROP CONSTRAINT users_role_check;
ALTER TABLE public.users ADD CONSTRAINT users_role_check
  CHECK (role IN (
    'Company Admin', 'Admin', 'Recruiter',
    'Hiring Manager', 'Interviewer', 'Observer'
  ));

-- Backfill: existing Company Admin users get full permissions
UPDATE public.users
  SET is_admin = TRUE,
      permissions = '["candidates.advance","candidates.evaluate","candidates.view","interviews.conduct","interviews.schedule","jobs.create","jobs.manage","org_units.create","org_units.manage","reports.export","reports.view","settings.client","settings.integrations","users.deactivate","users.invite_admins","users.invite_users"]'::jsonb
  WHERE role = 'Company Admin';

-- Update role CHECK on user_invites to include 'Admin'
ALTER TABLE public.user_invites DROP CONSTRAINT user_invites_role_check;
ALTER TABLE public.user_invites ADD CONSTRAINT user_invites_role_check
  CHECK (role IN (
    'Company Admin', 'Admin', 'Recruiter',
    'Hiring Manager', 'Interviewer', 'Observer'
  ));

-- Step 4: Add permission columns to public.user_invites
ALTER TABLE public.user_invites
  ADD COLUMN is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN permissions   JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN org_unit_id   UUID REFERENCES public.organizational_units(id);

-- Backfill: existing Company Admin invites get full permissions
UPDATE public.user_invites
  SET is_admin = TRUE,
      permissions = '["candidates.advance","candidates.evaluate","candidates.view","interviews.conduct","interviews.schedule","jobs.create","jobs.manage","org_units.create","org_units.manage","reports.export","reports.view","settings.client","settings.integrations","users.deactivate","users.invite_admins","users.invite_users"]'::jsonb
  WHERE role = 'Company Admin';
