'use client'

import type { ReactNode } from 'react'

import { DevtoolsBlockedOverlay } from '@/components/DevtoolsShield'
import { useDevtoolsLockout } from '@/hooks/use-devtools-lockout'
import { FullscreenLockGate } from './FullscreenLockGate'

/**
 * Full lockdown for the candidate pre-check: the fullscreen lock
 * (FullscreenLockGate) plus a stronger size-delta devtools detector on top of
 * the site-wide DevtoolsShield. Either overlay blocks the steps until the
 * candidate complies. The devtools overlay is a sibling of (not inside) the
 * fullscreen gate so it shows regardless of fullscreen state, and sits at a
 * higher z-index.
 */
export function PreCheckLockGate({ children }: { children: ReactNode }) {
  const devtoolsOpen = useDevtoolsLockout(true)
  return (
    <>
      <FullscreenLockGate>{children}</FullscreenLockGate>
      {devtoolsOpen && <DevtoolsBlockedOverlay />}
    </>
  )
}
