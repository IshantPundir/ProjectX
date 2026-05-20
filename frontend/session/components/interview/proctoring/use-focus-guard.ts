'use client'

import { useEffect } from 'react'
import type { GuardArgs } from './use-visibility-guard'

export function useFocusGuard({ armed, onViolation }: GuardArgs): void {
  useEffect(() => {
    if (!armed) return
    const onBlur = () => {
      // A tab switch fires both blur and visibilitychange→hidden; let the
      // visibility guard own that case so we record one violation, not two.
      if (document.visibilityState === 'hidden') return
      onViolation('focus_loss')
    }
    window.addEventListener('blur', onBlur)
    return () => window.removeEventListener('blur', onBlur)
  }, [armed, onViolation])
}
