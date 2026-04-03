-- ============================================================================
-- ProjectX Custom Access Token Hook — PRODUCTION DEPLOYMENT
-- ============================================================================
-- This file is a copy of backend/supabase/migrations/20260403000001_auth_hook.sql
-- for manual execution against hosted Supabase Postgres.
--
-- HOW TO DEPLOY:
--   1. Connect to hosted Supabase Postgres (Database → Connection string → psql)
--   2. Run this entire file: \i scripts/supabase_hook.sql
--   3. Register the hook in Supabase Dashboard:
--      Authentication → Hooks → Custom Access Token Hook
--      → select public.projectx_custom_access_token_hook
--
-- DO NOT run this against local Supabase — it runs automatically via migrations.
-- ============================================================================

-- Migration: Custom Access Token Hook for Supabase Auth
-- This function runs before every JWT issuance. It injects tenant_id,
-- app_role, and is_projectx_admin into the JWT claims.
--
-- CRITICAL CONSTRAINTS:
--   - 2-second hard timeout — must complete quickly
--   - READ-ONLY — never INSERT/UPDATE in this function
--   - Must preserve all required JWT claims in output
--   - EXCEPTION handler returns safe defaults — never blocks login

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

  -- Check for ProjectX admin flag (already in claims.app_metadata)
  is_px_admin := COALESCE(
    (claims -> 'app_metadata' ->> 'is_projectx_admin')::BOOLEAN,
    FALSE
  );

  IF is_px_admin THEN
    claims := jsonb_set(claims, '{is_projectx_admin}', 'true');
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{app_role}', '""');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 1: existing public.users row (covers both first login and token_refresh)
  SELECT id, tenant_id, role
    INTO user_row
    FROM public.users
   WHERE auth_user_id = user_uid
     AND is_active = TRUE
   LIMIT 1;

  IF FOUND THEN
    claims := jsonb_set(claims, '{tenant_id}', to_jsonb(user_row.tenant_id::TEXT));
    claims := jsonb_set(claims, '{app_role}',  to_jsonb(user_row.role));
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Skip invite lookup on token_refresh — no new invite is being claimed
  IF auth_method = 'token_refresh' THEN
    claims := jsonb_set(claims, '{tenant_id}', '""');
    claims := jsonb_set(claims, '{app_role}', '""');
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 2: pending invite exists for this email
  SELECT id, tenant_id, role
    INTO invite_row
    FROM public.user_invites
   WHERE email      = user_email
     AND status     = 'pending'
     AND expires_at > NOW()
   ORDER BY created_at DESC
   LIMIT 1;

  IF FOUND THEN
    claims := jsonb_set(claims, '{tenant_id}', to_jsonb(invite_row.tenant_id::TEXT));
    claims := jsonb_set(claims, '{app_role}',  to_jsonb(invite_row.role));
    claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
    RETURN jsonb_build_object('claims', claims);
  END IF;

  -- Case 3: no user, no invite — return empty claims
  claims := jsonb_set(claims, '{tenant_id}', '""');
  claims := jsonb_set(claims, '{app_role}',  '""');
  claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
  RETURN jsonb_build_object('claims', claims);

EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'projectx_hook error: % %', SQLERRM, SQLSTATE;
  claims := jsonb_set(claims, '{tenant_id}', '""');
  claims := jsonb_set(claims, '{app_role}', '""');
  claims := jsonb_set(claims, '{is_projectx_admin}', 'false');
  RETURN jsonb_build_object('claims', claims);
END;
$$;

-- Grants: allow Supabase Auth service to execute the hook
GRANT EXECUTE ON FUNCTION public.projectx_custom_access_token_hook TO supabase_auth_admin;
GRANT USAGE ON SCHEMA public TO supabase_auth_admin;
GRANT SELECT ON public.users TO supabase_auth_admin;
GRANT SELECT ON public.user_invites TO supabase_auth_admin;

-- Revoke from other roles to prevent Data API exposure
REVOKE EXECUTE ON FUNCTION public.projectx_custom_access_token_hook FROM authenticated, anon, public;
