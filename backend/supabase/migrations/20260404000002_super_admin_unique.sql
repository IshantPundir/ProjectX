-- Only one Super Admin per tenant — hard DB constraint.
-- A Super Admin is: role='Company Admin', is_admin=TRUE, parent_user_id IS NULL.
-- The provisioning service already enforces this in practice;
-- this index makes it impossible to violate even via raw SQL.

CREATE UNIQUE INDEX one_super_admin_per_tenant
  ON public.users (tenant_id)
  WHERE role = 'Company Admin' AND is_admin = TRUE AND parent_user_id IS NULL;
