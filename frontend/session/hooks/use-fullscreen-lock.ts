'use client'

import { useCallback, useEffect, useState } from 'react'

export interface FullscreenLock {
  /** True only when the document is in fullscreen AND visible. */
  locked: boolean
  /** Request fullscreen. MUST be called from a user-gesture handler. */
  enterFullscreen: () => void
}

function computeLocked(): boolean {
  if (typeof document === 'undefined') return false
  return document.fullscreenElement != null && document.visibilityState === 'visible'
}

/**
 * Tracks whether the page is "locked in" -- i.e. fullscreen and visible. Recomputes
 * on fullscreenchange / visibilitychange / window focus+blur, so exiting
 * fullscreen, minimizing, or switching tabs flips `locked` to false. SSR/jsdom
 * safe (starts false -- the gate shows until proven fullscreen).
 */
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

  const enterFullscreen = useCallback(() => {
    void document.documentElement.requestFullscreen?.().catch(() => {})
  }, [])

  return { locked, enterFullscreen }
}
