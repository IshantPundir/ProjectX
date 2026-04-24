'use client'

/**
 * Client-side session validation. Mounted inside DashboardProviders.
 *
 * Responsibilities:
 *  - On mount: call supabase.auth.getUser() once. getUser() contacts
 *    the auth server (NOT just the cookie). If the server says the
 *    session is invalid/expired, push to /login.
 *  - On `visibilitychange` → `visible` after having been hidden for
 *    5+ minutes: re-validate via getUser(). This catches the "user
 *    walked away and their session was revoked" case.
 *  - Subscribe to supabase.auth.onAuthStateChange; on SIGNED_OUT →
 *    push to /login (covers cross-tab sign-out).
 *
 * This is the OOB validator that lets `getFreshSupabaseToken()` stay
 * fast on the hot path (cookie read only). Invariant: by the time
 * getFreshSupabaseToken() returns, SessionGuard has validated within
 * the last visibility window or a fresh onAuthStateChange event.
 */
import { useRouter } from 'next/navigation'
import { useEffect, useRef } from 'react'

import { createClient } from '@/lib/supabase/client'

const REVALIDATE_AFTER_HIDDEN_MS = 5 * 60 * 1000  // 5 minutes

export function SessionGuard() {
  const router = useRouter()
  const hiddenSinceRef = useRef<number | null>(null)

  useEffect(() => {
    const supabase = createClient()

    const validate = async () => {
      const { data, error } = await supabase.auth.getUser()
      if (error || !data.user) {
        router.push('/login')
      }
    }

    // Initial validation.
    void validate()

    // Cross-tab / onAuthStateChange.
    const { data: authSub } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_OUT') {
        router.push('/login')
      }
    })

    // Visibility-based re-validation.
    const handleVisibility = () => {
      if (document.visibilityState === 'hidden') {
        hiddenSinceRef.current = Date.now()
        return
      }
      if (document.visibilityState === 'visible') {
        const hiddenSince = hiddenSinceRef.current
        hiddenSinceRef.current = null
        if (hiddenSince && Date.now() - hiddenSince >= REVALIDATE_AFTER_HIDDEN_MS) {
          void validate()
        }
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)

    return () => {
      authSub.subscription.unsubscribe()
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [router])

  return null
}
