'use client'

import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import type { ProctoringKind } from '@/lib/api/candidate-session'
import { VIOLATION_LABEL } from './violation-kinds'

const NOTICE_AUTO_MS = 6000 // notices self-dismiss so a live interview is never blocked

/**
 * Modal notice for a SOFT proctoring violation — styled like the grace overlays
 * (FullscreenGraceOverlay/FocusGraceOverlay) so the warning is unmissable. The
 * scrim is visual only; LiveKit audio + the agent keep running underneath. Hard
 * violations do NOT use this (they terminate → ProctoringEndedScreen).
 */
export function ViolationNoticeOverlay({
  kind,
  softCount,
  limit,
  onAcknowledge,
}: {
  kind: ProctoringKind
  softCount: number
  limit: number
  onAcknowledge: () => void
}) {
  useEffect(() => {
    const t = window.setTimeout(onAcknowledge, NOTICE_AUTO_MS)
    return () => window.clearTimeout(t)
  }, [onAcknowledge])

  return (
    <div
      role="alertdialog"
      aria-live="assertive"
      aria-label="Proctoring warning"
      className="fixed inset-0 z-[70] grid place-items-center bg-black/60 backdrop-blur-xl"
    >
      <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10 text-center">
        <h2 className="font-serif text-2xl text-px-fg">Please keep to the interview rules</h2>
        <p className="mt-3 text-sm text-px-fg-3">
          We noticed <span className="font-semibold text-px-caution">{VIOLATION_LABEL[kind]}</span>. This is{' '}
          <span className="font-mono font-bold text-px-caution">
            warning {softCount} of {limit}
          </span>
          . Repeated warnings will end your interview.
        </p>
        <Button
          size="lg"
          onClick={onAcknowledge}
          className="mt-8 w-64 rounded-full font-mono text-xs font-bold uppercase tracking-wider"
        >
          I understand
        </Button>
      </div>
    </div>
  )
}
