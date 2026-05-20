'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ProctoringKind } from '@/lib/api/candidate-session'

export interface FullscreenGuardArgs {
  armed: boolean
  graceSeconds: number
  onViolation: (kind: ProctoringKind) => void
}

export interface FullscreenGuardState {
  showOverlay: boolean
  secondsLeft: number
  returnToFullscreen: () => void
}

export function useFullscreenGuard({
  armed,
  graceSeconds,
  onViolation,
}: FullscreenGuardArgs): FullscreenGuardState {
  const [showOverlay, setShowOverlay] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState(graceSeconds)
  const hasEntered = useRef(false)
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
        onViolation('fullscreen_abandoned') // hard — controller terminates
      }
    }, 1000)
  }, [graceSeconds, clearCountdown, onViolation])

  const returnToFullscreen = useCallback(() => {
    // Must run inside a user-gesture handler (the overlay button click).
    void document.documentElement.requestFullscreen?.().catch(() => {})
  }, [])

  useEffect(() => {
    if (!armed) return
    const onFsChange = () => {
      if (document.fullscreenElement) {
        const wasGrace = graceActive.current
        clearCountdown()
        if (!hasEntered.current) {
          hasEntered.current = true // initial entry — never a violation
          return
        }
        if (wasGrace) onViolation('fullscreen_exit') // soft — returned in time
      } else {
        startGrace()
      }
    }
    document.addEventListener('fullscreenchange', onFsChange)
    // If we armed and aren't in fullscreen (the start-gesture request was
    // denied), prompt the candidate to click in — without penalty.
    if (!document.fullscreenElement) startGrace()
    else hasEntered.current = true
    return () => {
      document.removeEventListener('fullscreenchange', onFsChange)
      clearCountdown()
    }
  }, [armed, startGrace, clearCountdown, onViolation])

  return { showOverlay, secondsLeft, returnToFullscreen }
}
