'use client'

import { useEffect, useRef } from 'react'
import type { ProctoringKind } from '@/lib/api/candidate-session'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'

export interface DisplayGuardArgs {
  armed: boolean
  onViolation: (kind: ProctoringKind) => void
}

/**
 * In-session second-screen guard. Fires `multiple_displays` (soft) when a
 * multi-display setup is present at arm time or appears mid-interview. Re-arms
 * when the candidate drops back to a single display. `null` (API unsupported)
 * is treated as single-display — the camera/gaze plane is the backstop there.
 */
export function useDisplayGuard({ armed, onViolation }: DisplayGuardArgs): void {
  const fired = useRef(false)
  const onViolationRef = useRef(onViolation)
  useEffect(() => { onViolationRef.current = onViolation })

  useEffect(() => {
    if (!armed) return
    const check = () => {
      if (isMultiDisplay() === true) {
        if (!fired.current) {
          fired.current = true
          onViolationRef.current('multiple_displays')
        }
      } else {
        fired.current = false // re-arm once the extra display is gone
      }
    }
    check() // catch an already-extended setup at arm time
    return subscribeDisplayChange(check)
  }, [armed])
}
