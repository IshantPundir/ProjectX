'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ProctoringKind } from '@/lib/api/candidate-session'

export interface FocusGuardArgs {
  armed: boolean
  graceSeconds: number
  onViolation: (kind: ProctoringKind) => void
}

export interface FocusGuardState {
  showOverlay: boolean
  secondsLeft: number
}

/**
 * Window-focus proctoring, modelled on the fullscreen guard: losing window
 * focus no longer ends the session outright. On blur we open a grace overlay
 * and count down; if the candidate returns (window regains focus) in time it is
 * a soft `focus_loss` warning, and only running the clock out reports the hard
 * `focus_abandoned` kind that terminates. Tab switches are still owned by the
 * visibility guard (`tab_switch`, hard) — we defer when the page is hidden.
 */
export function useFocusGuard({
  armed,
  graceSeconds,
  onViolation,
}: FocusGuardArgs): FocusGuardState {
  const [showOverlay, setShowOverlay] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState(graceSeconds)
  const graceActive = useRef(false)
  const timer = useRef<number | null>(null)

  const clearCountdown = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current)
      timer.current = null
    }
    graceActive.current = false
    setShowOverlay(false)
  }, [])

  const startGrace = useCallback(() => {
    if (graceActive.current) return
    graceActive.current = true
    setShowOverlay(true)
    setSecondsLeft(graceSeconds)
    let left = graceSeconds
    timer.current = window.setInterval(() => {
      left -= 1
      setSecondsLeft(left)
      if (left <= 0) {
        clearCountdown()
        onViolation('focus_abandoned') // hard — controller terminates
      }
    }, 1000)
  }, [graceSeconds, clearCountdown, onViolation])

  useEffect(() => {
    if (!armed) return
    const onBlur = () => {
      // A tab switch fires both blur and visibilitychange→hidden; let the
      // visibility guard own that case so we record one violation, not two.
      if (document.visibilityState === 'hidden') return
      startGrace()
    }
    const onFocus = () => {
      if (!graceActive.current) return // spurious focus with no grace running
      clearCountdown()
      onViolation('focus_loss') // soft — returned in time
    }
    window.addEventListener('blur', onBlur)
    window.addEventListener('focus', onFocus)
    return () => {
      window.removeEventListener('blur', onBlur)
      window.removeEventListener('focus', onFocus)
      clearCountdown()
    }
  }, [armed, startGrace, clearCountdown, onViolation])

  return { showOverlay, secondsLeft }
}
