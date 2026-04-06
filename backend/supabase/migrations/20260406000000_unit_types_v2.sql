-- One root unit (parent_unit_id IS NULL) per tenant.
CREATE UNIQUE INDEX one_root_per_tenant
  ON public.organizational_units (client_id)
  WHERE parent_unit_id IS NULL;
