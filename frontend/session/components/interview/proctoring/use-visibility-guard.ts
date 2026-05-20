'use client'

import { useEffect } from 'react'
import type { ProctoringKind } from '@/lib/api/candidate-session'

export interface GuardArgs {
  armed: boolean
  onViolation: (kind: ProctoringKind) => void
}

export function useVisibilityGuard({ armed, onViolation }: GuardArgs): void {
  useEffect(() => {
    if (!armed) return
    const onVis = () => {
      if (document.visibilityState === 'hidden') onViolation('tab_switch')
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [armed, onViolation])
}
