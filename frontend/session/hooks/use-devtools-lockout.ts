'use client'

import { useEffect, useRef, useState } from 'react'

const SIZE_DELTA_THRESHOLD = 160
const POLL_MS = 1000
const DEBUGGER_PAUSE_MS = 100

/**
 * Best-effort devtools detection for the candidate pre-check — the same
 * technique the live session uses (`use-devtools-guard.ts`), which the site-wide
 * DevtoolsShield deliberately omits to avoid false-positives on the public
 * landing page. Combines:
 *   1. a window-size-delta heuristic — the baseline (browser chrome + the
 *      candidate's initial zoom) is captured at mount, so a *later* docking of
 *      devtools shows up as a delta jump past the threshold; and
 *   2. a `debugger`-timing trap — catches an already-open / undocked console in
 *      development.
 * Returns `detected`, which clears when devtools is closed again.
 *
 * HONEST LIMITS: the `debugger` trap is stripped from production builds, and
 * undocked devtools opened *before* load can evade the size heuristic — client
 * detection is a deterrent, never a guarantee (the authoritative proctoring
 * signal is server-side + the recording + human review). Zooming *after* load
 * can false-positive; accepted on this locked-down surface.
 */
export function useDevtoolsLockout(enabled: boolean): boolean {
  const [detected, setDetected] = useState(false)
  const baseW = useRef(0)
  const baseH = useRef(0)

  useEffect(() => {
    if (!enabled) return
    baseW.current = window.outerWidth - window.innerWidth
    baseH.current = window.outerHeight - window.innerHeight

    const sizeOpened = () =>
      window.outerWidth - window.innerWidth - baseW.current > SIZE_DELTA_THRESHOLD ||
      window.outerHeight - window.innerHeight - baseH.current > SIZE_DELTA_THRESHOLD

    const check = () => {
      const t0 = performance.now()
      // eslint-disable-next-line no-debugger
      debugger
      const paused = performance.now() - t0 > DEBUGGER_PAUSE_MS
      setDetected(paused || sizeOpened())
    }

    // First tick is the interval (not a synchronous setState in the effect body).
    const interval = window.setInterval(check, POLL_MS)
    const onResize = () => setDetected((d) => d || sizeOpened())
    window.addEventListener('resize', onResize)

    return () => {
      window.clearInterval(interval)
      window.removeEventListener('resize', onResize)
    }
  }, [enabled])

  return detected
}
