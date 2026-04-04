-- ============================================================
-- Phase 6: Auth Hook v2 — now injects is_admin + org_unit_id
-- Replaces the v1 hook. Same structure, 2 new claims.
-- ============================================================

CREATE OR REPLACE FUNCTION public.projectx_custom_access_token_hook(event JSONB)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
  claims          JSONB;
  user_uid        UUID;
  user_email      TEXT;
  user_row        RECORD;
  invite_row      RECORD;
  is_px_admin     BOOLEAN;
  auth_method     TEXT;
BEGIN
  claims      := event -> 'claims';
  user_uid    := (event ->> 'user_id')::UUID;
  user_email  := claims ->> 'email';
  auth_method := event ->> 'authentication_method';

  -- ProjectX admin early return
  is_px_admin := COALESCE(
    (claims -> 'app_metadata' ->> 'is_projectx_admin')::BOOLEAN,
    FALSE
  );

  IF is_px_admin THEN
    claims := jsonb_set(claims, '{is_projectx_admin}', 'true');
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{app_role}', '""');
    claims := jsonb_set(claims, '{is_admin}', 'false');
    claims := jsonb_set(claims, '{org_unit_id}', 'null');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 1: existing public.users row
  SELECT id, tenant_id, role, is_admin, org_unit_id
    INTO user_row
    FROM public.users
   WHERE auth_user_id = user_uid
     AND is_active = TRUE
   LIMIT 1;

  IF FOUND THEN
    claims := jsonb_set(claims, '{tenant_id}', to_jsonb(user_row.tenant_id::TEXT));
    claims := jsonb_set(claims, '{app_role}',
      CASE WHEN user_row.role IS NOT NULL
           THEN to_jsonb(user_row.role)
           ELSE '""'::jsonb END);
    claims := jsonb_set(claims, '{is_admin}',  to_jsonb(user_row.is_admin));
    claims := jsonb_set(claims, '{org_unit_id}',
      CASE WHEN user_row.org_unit_id IS NOT NULL
           THEN to_jsonb(user_row.org_unit_id::TEXT)
           ELSE 'null'::jsonb END);
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Skip invite lookup on token_refresh
  IF auth_method = 'token_refresh' THEN
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{app_role}', '""');
    claims := jsonb_set(claims, '{is_admin}', 'false');
    claims := jsonb_set(claims, '{org_unit_id}', 'null');
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 2: pending invite by email
  SELECT id, tenant_id, role, is_admin, org_unit_id
    INTO invite_row
    FROM public.user_invites
   WHERE email      = user_email
     AND status     = 'pending'
     AND expires_at > NOW()
   ORDER BY created_at DESC
   LIMIT 1;

  IF FOUND THEN
    claims := jsonb_set(claims, '{tenant_id}', to_jsonb(invite_row.tenant_id::TEXT));
    claims := jsonb_set(claims, '{app_role}',
      CASE WHEN invite_row.role IS NOT NULL
           THEN to_jsonb(invite_row.role)
           ELSE '""'::jsonb END);
    claims := jsonb_set(claims, '{is_admin}',  to_jsonb(invite_row.is_admin));
    claims := jsonb_set(claims, '{org_unit_id}',
      CASE WHEN invite_row.org_unit_id IS NOT NULL
           THEN to_jsonb(invite_row.org_unit_id::TEXT)
           ELSE 'null'::jsonb END);
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- No match — empty claims
  claims := jsonb_set(claims, '{tenant_id}', '""');
  claims := jsonb_set(claims, '{app_role}', '""');
  claims := jsonb_set(claims, '{is_admin}', 'false');
  claims := jsonb_set(claims, '{org_unit_id}', 'null');
  claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
  RETURN jsonb_build_object('claims', claims);

EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'projectx_hook v2 error: % %', SQLERRM, SQLSTATE;
  claims := jsonb_set(claims, '{tenant_id}', '""');
  claims := jsonb_set(claims, '{app_role}', '""');
  claims := jsonb_set(claims, '{is_admin}', 'false');
  claims := jsonb_set(claims, '{org_unit_id}', 'null');
  claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
  RETURN jsonb_build_object('claims', claims);
END;
$$;

-- Re-grant (function was recreated)
GRANT EXECUTE ON FUNCTION public.projectx_custom_access_token_hook TO supabase_auth_admin;
GRANT USAGE ON SCHEMA public TO supabase_auth_admin;
GRANT SELECT ON public.users TO supabase_auth_admin;
GRANT SELECT ON public.user_invites TO supabase_auth_admin;
GRANT SELECT ON public.organizational_units TO supabase_auth_admin;
REVOKE EXECUTE ON FUNCTION public.projectx_custom_access_token_hook FROM authenticated, anon, public;

-- RLS policy for supabase_auth_admin to read org_units (defensive)
CREATE POLICY "auth_hook_read" ON public.organizational_units
  FOR SELECT
  TO supabase_auth_admin
  USING (true);
