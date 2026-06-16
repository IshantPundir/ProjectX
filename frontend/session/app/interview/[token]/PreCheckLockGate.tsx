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
 *
 * `enforceFullscreen` is false on the intro step — the candidate reads it
 * without being blocked, and the intro's "I'm ready" CTA enters fullscreen as
 * part of that same click. The verify/ready steps enforce fullscreen (re-prompt
 * on exit). Devtools detection applies regardless.
 */
export function PreCheckLockGate({
  children,
  enforceFullscreen = true,
}: {
  children: ReactNode
  enforceFullscreen?: boolean
}) {
  const devtoolsOpen = useDevtoolsLockout(true)
  return (
    <>
      {enforceFullscreen ? <FullscreenLockGate>{children}</FullscreenLockGate> : children}
      {devtoolsOpen && <DevtoolsBlockedOverlay />}
    </>
  )
}
