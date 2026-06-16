'use client'

import { useCallback, useEffect, useState } from 'react'

export interface FullscreenLock {
  /** True only when the document is in fullscreen AND visible. */
  locked: boolean
  /** Request fullscreen. MUST be called from a user-gesture handler. */
  enterFullscreen: () => void
}

/**
 * Whether this browser can actually enter element fullscreen. iOS Safari (every
 * iPhone browser is WebKit) does NOT support `Element.requestFullscreen`, and an
 * iframe without `allow="fullscreen"` reports `fullscreenEnabled === false`. In
 * those environments we must never block — the candidate would have no way to
 * satisfy the gate and could never take the interview.
 */
function fullscreenSupported(): boolean {
  if (typeof document === 'undefined') return false
  return (
    document.fullscreenEnabled === true &&
    typeof document.documentElement.requestFullscreen === 'function'
  )
}

function computeLocked(): boolean {
  if (typeof document === 'undefined') return false
  // Degrade to "locked" (gate hidden) wherever fullscreen can't be entered, so a
  // candidate is never trapped behind a gate they cannot dismiss (e.g. iPhone).
  if (!fullscreenSupported()) return true
  return document.fullscreenElement != null && document.visibilityState === 'visible'
}

/**
 * Tracks whether the page is "locked in" -- i.e. fullscreen and visible (or in a
 * browser that can't do element fullscreen, where we never block). Recomputes on
 * fullscreenchange / visibilitychange / window focus+blur, so exiting fullscreen,
 * minimizing, or switching tabs flips `locked` to false. SSR/jsdom safe (starts
 * false -- the gate shows until proven fullscreen/unsupported).
 */

/**
 * Request app fullscreen. MUST be called synchronously inside a user-gesture
 * handler (a click/tap) or the browser rejects it. Used by both the lock gate's
 * button and the intro's "I'm ready" CTA (so that single click also goes
 * fullscreen). No-op + safe where the API is unavailable.
 */
export function requestAppFullscreen(): void {
  if (typeof document === 'undefined') return
  void document.documentElement.requestFullscreen?.().catch(() => {})
}

export function useFullscreenLock(): FullscreenLock {
  const [locked, setLocked] = useState(false)

  useEffect(() => {
    const update = () => setLocked(computeLocked())
    update()
    document.addEventListener('fullscreenchange', update)
    document.addEventListener('visibilitychange', update)
    window.addEventListener('focus', update)
    window.addEventListener('blur', update)
    return () => {
      document.removeEventListener('fullscreenchange', update)
      document.removeEventListener('visibilitychange', update)
      window.removeEventListener('focus', update)
      window.removeEventListener('blur', update)
    }
  }, [])

  const enterFullscreen = useCallback(() => requestAppFullscreen(), [])

  return { locked, enterFullscreen }
}
