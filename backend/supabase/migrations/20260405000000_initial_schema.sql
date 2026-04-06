-- =============================================================
-- ProjectX Initial Schema (clean slate)
-- Tables: clients, users, organizational_units, roles,
--         user_role_assignments, user_invites
-- Includes: RLS policies, auth hook, system role seeds
-- =============================================================

-- ─── 1. clients ──────────────────────────────────────────────
CREATE TABLE public.clients (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    domain      TEXT DEFAULT '',
    industry    TEXT DEFAULT '',
    size        TEXT DEFAULT '',
    logo_url    TEXT,
    plan        TEXT NOT NULL DEFAULT 'trial'
                    CHECK (plan IN ('trial', 'pro', 'enterprise')),
    onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
    workspace_mode TEXT NOT NULL DEFAULT 'enterprise'
                       CHECK (workspace_mode IN ('enterprise', 'agency')),
    super_admin_id UUID,                     -- FK added after users table
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_read" ON public.clients
    FOR SELECT USING (id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.clients
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 2. users ────────────────────────────────────────────────
CREATE TABLE public.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_user_id    UUID NOT NULL UNIQUE,
    tenant_id       UUID NOT NULL REFERENCES public.clients(id),
    email           TEXT NOT NULL,
    full_name       TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX users_tenant_id_idx ON public.users (tenant_id);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.users
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.users
    USING (current_setting('app.bypass_rls', true) = 'true');

-- Now add the deferred FK from clients → users
ALTER TABLE public.clients
    ADD CONSTRAINT clients_super_admin_id_fk
    FOREIGN KEY (super_admin_id) REFERENCES public.users(id)
    DEFERRABLE INITIALLY DEFERRED;

-- ─── 3. organizational_units ─────────────────────────────────
CREATE TABLE public.organizational_units (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES public.clients(id),
    parent_unit_id  UUID REFERENCES public.organizational_units(id),
    name            TEXT NOT NULL,
    unit_type       TEXT NOT NULL
                        CHECK (unit_type IN ('company', 'division', 'client_account', 'region', 'team')),
    is_root         BOOLEAN NOT NULL DEFAULT FALSE,
    company_profile JSONB,
    created_by      UUID REFERENCES public.users(id),
    deletable_by    UUID REFERENCES public.users(id),
    admin_delete_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX org_units_client_id_idx ON public.organizational_units (client_id);
CREATE INDEX org_units_parent_unit_id_idx ON public.organizational_units (parent_unit_id);
CREATE INDEX org_units_created_by_idx ON public.organizational_units (created_by);

ALTER TABLE public.organizational_units ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.organizational_units
    FOR SELECT USING (client_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.organizational_units
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 4. roles ────────────────────────────────────────────────
CREATE TABLE public.roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES public.clients(id),
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_system   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT roles_unique_name UNIQUE NULLS NOT DISTINCT (tenant_id, name)
);

CREATE INDEX roles_is_system_idx ON public.roles (is_system);
CREATE INDEX roles_tenant_id_idx ON public.roles (tenant_id);

ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "roles_visibility" ON public.roles
    FOR SELECT USING (
        tenant_id IS NULL
        OR tenant_id = current_setting('app.current_tenant', true)::UUID
    );
CREATE POLICY "service_bypass" ON public.roles
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 5. user_role_assignments ────────────────────────────────
CREATE TABLE public.user_role_assignments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id),
    org_unit_id UUID NOT NULL REFERENCES public.organizational_units(id),
    role_id     UUID NOT NULL REFERENCES public.roles(id),
    tenant_id   UUID NOT NULL REFERENCES public.clients(id),
    assigned_by UUID REFERENCES public.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT unique_user_unit_role UNIQUE (user_id, org_unit_id, role_id)
);

CREATE INDEX ura_user_id_idx ON public.user_role_assignments (user_id);
CREATE INDEX ura_org_unit_id_idx ON public.user_role_assignments (org_unit_id);
CREATE INDEX ura_tenant_id_idx ON public.user_role_assignments (tenant_id);

ALTER TABLE public.user_role_assignments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.user_role_assignments
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.user_role_assignments
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 6. user_invites ────────────────────────────────────────
CREATE TABLE public.user_invites (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES public.clients(id),
    invited_by          UUID REFERENCES public.users(id),
    projectx_admin_id   TEXT,
    email               TEXT NOT NULL,
    token_hash          TEXT NOT NULL UNIQUE,
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'accepted', 'superseded', 'expired', 'revoked')),
    superseded_by       UUID REFERENCES public.user_invites(id),
    expires_at          TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '72 hours',
    accepted_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT invite_origin_xor CHECK (
        (invited_by IS NOT NULL AND projectx_admin_id IS NULL) OR
        (invited_by IS NULL AND projectx_admin_id IS NOT NULL)
    )
);

CREATE INDEX invites_tenant_id_idx ON public.user_invites (tenant_id);
CREATE INDEX invites_email_status_idx ON public.user_invites (email, status);

ALTER TABLE public.user_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON public.user_invites
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::UUID);
CREATE POLICY "service_bypass" ON public.user_invites
    USING (current_setting('app.bypass_rls', true) = 'true');

-- ─── 7. Seed system roles ───────────────────────────────────
INSERT INTO public.roles (tenant_id, name, description, permissions, is_system) VALUES
(NULL, 'Admin', 'Full control of an organizational unit',
 '["users.invite_admins","users.invite_users","users.deactivate","org_units.create","org_units.manage","jobs.create","jobs.manage","candidates.view","candidates.evaluate","candidates.advance","interviews.schedule","interviews.conduct","reports.view","reports.export","settings.client","settings.integrations"]'::jsonb,
 TRUE),
(NULL, 'Recruiter', 'Manages job pipelines and candidate flow',
 '["jobs.create","jobs.manage","candidates.view","candidates.advance","interviews.schedule","reports.view"]'::jsonb,
 TRUE),
(NULL, 'Hiring Manager', 'Reviews and evaluates candidates',
 '["candidates.view","candidates.evaluate","candidates.advance","reports.view","reports.export"]'::jsonb,
 TRUE),
(NULL, 'Interviewer', 'Conducts live interviews',
 '["interviews.conduct","candidates.view","candidates.evaluate"]'::jsonb,
 TRUE),
(NULL, 'Observer', 'Read-only access to candidates and reports',
 '["candidates.view","reports.view"]'::jsonb,
 TRUE);

-- ─── 8. Auth hook ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.projectx_custom_access_token_hook(event JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path = public
STABLE
AS $$
DECLARE
    claims      JSONB;
    user_meta   JSONB;
    v_tenant_id UUID;
    v_email     TEXT;
    auth_method TEXT;
BEGIN
    claims    := event->'claims';
    user_meta := event->'user_metadata';

    -- 1. ProjectX admin early return
    IF (claims->'app_metadata'->>'is_projectx_admin')::BOOLEAN IS TRUE THEN
        claims := jsonb_set(claims, '{tenant_id}', '""');
        claims := jsonb_set(claims, '{is_projectx_admin}', 'true');
        event  := jsonb_set(event, '{claims}', claims);
        RETURN event;
    END IF;

    -- 2. Look up existing user
    SELECT u.tenant_id INTO v_tenant_id
    FROM public.users u
    WHERE u.auth_user_id = (claims->>'sub')::UUID
      AND u.is_active = TRUE
    LIMIT 1;

    IF v_tenant_id IS NOT NULL THEN
        claims := jsonb_set(claims, '{tenant_id}', to_jsonb(v_tenant_id::TEXT));
        claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
        event  := jsonb_set(event, '{claims}', claims);
        RETURN event;
    END IF;

    -- 3. Skip invite lookup on token refresh
    auth_method := event->'authentication_method'->>'method';
    IF auth_method = 'token_refresh' THEN
        claims := jsonb_set(claims, '{tenant_id}', '""');
        claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
        event  := jsonb_set(event, '{claims}', claims);
        RETURN event;
    END IF;

    -- 4. Look up pending invite by email
    v_email := claims->>'email';
    IF v_email IS NOT NULL AND v_email != '' THEN
        SELECT i.tenant_id INTO v_tenant_id
        FROM public.user_invites i
        WHERE i.email = v_email
          AND i.status = 'pending'
          AND i.expires_at > NOW()
        ORDER BY i.created_at DESC
        LIMIT 1;

        IF v_tenant_id IS NOT NULL THEN
            claims := jsonb_set(claims, '{tenant_id}', to_jsonb(v_tenant_id::TEXT));
            claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
            event  := jsonb_set(event, '{claims}', claims);
            RETURN event;
        END IF;
    END IF;

    -- 5. No match — safe defaults
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    event  := jsonb_set(event, '{claims}', claims);
    RETURN event;

EXCEPTION WHEN OTHERS THEN
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    event  := jsonb_set(event, '{claims}', claims);
    RETURN event;
END;
$$;

-- Auth hook grants
GRANT USAGE ON SCHEMA public TO supabase_auth_admin;
GRANT EXECUTE ON FUNCTION public.projectx_custom_access_token_hook TO supabase_auth_admin;
GRANT SELECT ON public.users TO supabase_auth_admin;
GRANT SELECT ON public.user_invites TO supabase_auth_admin;

-- RLS policies for supabase_auth_admin
CREATE POLICY "auth_hook_users_read" ON public.users
    FOR SELECT TO supabase_auth_admin USING (TRUE);
CREATE POLICY "auth_hook_invites_read" ON public.user_invites
    FOR SELECT TO supabase_auth_admin USING (TRUE);
