// Fetches the current Supabase access token, refreshing if necessary.
// Used by every TanStack Query hook and typed API client.
//
// No in-memory cache: @supabase/ssr already caches the session in
// cookies and auto-refreshes on expiry — re-calling getSession() is
// cheap, and a second cache risks serving a stale token.
//
// No server round-trip: validation is out-of-band via `SessionGuard`
// (components/dashboard/SessionGuard.tsx), which calls `getUser()` on
// mount, on `visibilitychange` when the tab is revealed after being
// hidden ≥5min, and on `onAuthStateChange('SIGNED_OUT')`. Invariant:
// any token this function returns has been validated inside the
// current SessionGuard window. Keeping the hot path on `getSession()`
// preserves sub-millisecond token reads for every API call.

import { createClient } from '@/lib/supabase/client'

export async function getFreshSupabaseToken(): Promise<string> {
  const supabase = createClient()
  const { data, error } = await supabase.auth.getSession()
  if (error || !data.session) {
    throw new Error('No active Supabase session')
  }
  return data.session.access_token
}
