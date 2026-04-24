/**
 * Global auth-error sink. Any 401 bubbling out of a TanStack Query
 * fetcher or mutation ends up here. Single responsibility: sign out,
 * toast once, redirect to /login.
 *
 * `AppRouter` is a minimal shape of next/navigation's AppRouter — we
 * depend on `push` only.
 */
import { toast } from 'sonner'

import { ApiError } from '@/lib/api/client'
import { createClient } from '@/lib/supabase/client'

export interface AppRouter {
  push: (href: string) => void
}

let _redirectInFlight = false

/**
 * Test-only: reset the dedup lock so each test starts from a clean state.
 * Do not call this in production code.
 */
export function _resetRedirectLockForTest(): void {
  _redirectInFlight = false
}

/** Returns true if the error matched and was handled. */
export async function handleAuthError(
  err: unknown,
  router: AppRouter,
): Promise<boolean> {
  const isApi401 = err instanceof ApiError && err.status === 401
  const isTokenMissing =
    err instanceof Error && err.message === 'No active Supabase session'
  if (!isApi401 && !isTokenMissing) {
    return false
  }

  // Deduplicate across concurrent query/mutation failures.
  if (_redirectInFlight) {
    return true
  }
  _redirectInFlight = true
  try {
    const supabase = createClient()
    await supabase.auth.signOut()
  } catch {
    // signOut failures shouldn't block the redirect.
  }
  toast.error('Session expired. Please log in again.')
  router.push('/login')
  // Reset the lock on next tick so legitimate re-login triggers the
  // handler again if something goes wrong post-redirect.
  setTimeout(() => {
    _redirectInFlight = false
  }, 500)
  return true
}
