// Fetches the current Supabase access token, refreshing if necessary.
// Used by useJobStatusStream and the typed jobs API client.
//
// No in-memory caching layer. @supabase/ssr already caches the session
// in cookies and auto-refreshes on expiry — re-calling getSession() is
// cheap, and adding a second cache would risk serving a stale token.

import { createClient } from '@/lib/supabase/client'

export async function getFreshSupabaseToken(): Promise<string> {
  const supabase = createClient()
  const { data, error } = await supabase.auth.getSession()
  if (error || !data.session) {
    throw new Error('No active Supabase session')
  }
  return data.session.access_token
}
