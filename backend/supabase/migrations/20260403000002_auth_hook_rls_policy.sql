-- Migration: Add RLS policy for supabase_auth_admin
--
-- The auth hook runs as supabase_auth_admin, which is NOT a superuser.
-- RLS is enforced for this role. Without an explicit policy, the hook's
-- SELECT queries on public.users and public.user_invites return 0 rows
-- (the tenant_isolation policy requires app.current_tenant which is not
-- set in the hook context, and service_bypass requires app.bypass_rls
-- which is also not set).
--
-- This policy allows supabase_auth_admin to SELECT any row — needed
-- for the hook's user and invite lookups. It grants read-only access
-- to this specific role only.

CREATE POLICY "auth_hook_read" ON public.users
  FOR SELECT
  TO supabase_auth_admin
  USING (true);

CREATE POLICY "auth_hook_read" ON public.user_invites
  FOR SELECT
  TO supabase_auth_admin
  USING (true);
