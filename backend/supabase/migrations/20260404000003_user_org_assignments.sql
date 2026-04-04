-- Junction table: users can be assigned to multiple organizational units.
-- users.org_unit_id remains the "primary" org unit (set at invite time).
-- This table tracks additional assignments.

CREATE TABLE public.user_org_assignments (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID        NOT NULL REFERENCES public.users(id),
  org_unit_id UUID        NOT NULL REFERENCES public.organizational_units(id),
  assigned_by UUID        NOT NULL REFERENCES public.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT unique_user_org UNIQUE (user_id, org_unit_id)
);

CREATE INDEX uoa_user_id_idx    ON public.user_org_assignments (user_id);
CREATE INDEX uoa_org_unit_id_idx ON public.user_org_assignments (org_unit_id);

ALTER TABLE public.user_org_assignments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.user_org_assignments
  USING (
    user_id IN (
      SELECT id FROM public.users
      WHERE tenant_id = (SELECT current_setting('app.current_tenant', true)::UUID)
    )
  );

CREATE POLICY "service_bypass" ON public.user_org_assignments FOR ALL
  USING      ((SELECT current_setting('app.bypass_rls', true)) = 'true')
  WITH CHECK ((SELECT current_setting('app.bypass_rls', true)) = 'true');
