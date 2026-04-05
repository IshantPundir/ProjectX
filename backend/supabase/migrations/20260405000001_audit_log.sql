-- Audit log — append-only trail for all tenant-scoped mutations.

CREATE TABLE public.audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES public.clients(id),
    actor_id     UUID REFERENCES public.users(id),
    actor_email  TEXT,
    action       TEXT NOT NULL,
    resource     TEXT NOT NULL,
    resource_id  UUID,
    payload      JSONB,
    ip_address   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_tenant_id_idx ON public.audit_log (tenant_id);
CREATE INDEX audit_log_tenant_action_idx ON public.audit_log (tenant_id, action);
CREATE INDEX audit_log_tenant_created_at_idx ON public.audit_log (tenant_id, created_at DESC);
CREATE INDEX audit_log_resource_idx ON public.audit_log (tenant_id, resource, resource_id);

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.audit_log
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.audit_log
    USING (current_setting('app.bypass_rls', true) = 'true');

COMMENT ON TABLE public.audit_log IS
'Append-only audit trail for all tenant-scoped mutations.
Never update or delete rows. actor_id may be NULL for
system-initiated actions. payload contains before/after state
or relevant context, schema varies per action string.';
