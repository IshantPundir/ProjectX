'use client'

import { useEffect, useRef } from 'react'
import type { GuardArgs } from './use-visibility-guard'

const SIZE_DELTA_THRESHOLD = 160
const POLL_MS = 1000
const DEBUGGER_PAUSE_MS = 100

export function useDevtoolsGuard({ armed, onViolation }: GuardArgs): void {
  const baseW = useRef(0)
  const baseH = useRef(0)
  const fired = useRef(false)

  useEffect(() => {
    if (!armed) return
    fired.current = false
    // Baseline captured at arm time excludes the browser's own chrome
    // (toolbars), so we detect devtools docking *after* the session starts
    // as a delta increase rather than a fixed (always-positive) gap.
    baseW.current = window.outerWidth - window.innerWidth
    baseH.current = window.outerHeight - window.innerHeight

    const sizeOpened = () =>
      window.outerWidth - window.innerWidth - baseW.current > SIZE_DELTA_THRESHOLD ||
      window.outerHeight - window.innerHeight - baseH.current > SIZE_DELTA_THRESHOLD

    const fire = () => {
      if (fired.current) return
      fired.current = true
      onViolation('devtools')
    }

    const onResize = () => {
      if (sizeOpened()) fire()
    }
    window.addEventListener('resize', onResize)

    const interval = window.setInterval(() => {
      if (fired.current) return
      const t0 = performance.now()
      // Catches an already-open / undocked console. Only pauses the main
      // thread when devtools is actually open — i.e. the instant we terminate.
      // eslint-disable-next-line no-debugger
      debugger
      if (performance.now() - t0 > DEBUGGER_PAUSE_MS) {
        fire()
        return
      }
      if (sizeOpened()) fire()
    }, POLL_MS)

    return () => {
      window.removeEventListener('resize', onResize)
      window.clearInterval(interval)
    }
  }, [armed, onViolation])
}
