'use client'

import { NUDGE_LABEL, type VisionNudgeKind } from './nudge-kinds'

const TITLE: Record<VisionNudgeKind, string> = {
  multiple_faces: 'Multiple people detected',
  face_not_visible: 'We can’t see you',
  looking_away_sustained: 'Please look at the screen',
}

/**
 * Candidate-facing, NON-blocking proctoring warning (a real-time deterrent).
 * Auto-dismisses when the parent stops passing a warning (the condition cleared).
 * NEVER terminates the session — vision is advisory (D1). Shown to real
 * candidates whenever proctoring is enabled; distinct from the dev-only
 * VisionDebugOverlay. pointer-events-none so it never blocks the interview.
 */
export function GazeWarningOverlay({ kind }: { kind: VisionNudgeKind }) {
  return (
    <div
      role="alert"
      aria-live="assertive"
      data-testid="gaze-warning-overlay"
      className="pointer-events-none fixed inset-x-0 top-6 z-[75] flex justify-center px-4"
    >
      <div className="flex items-center gap-3 rounded-xl border border-amber-400/60 bg-amber-950/85 px-5 py-3 text-amber-100 shadow-lg backdrop-blur-md">
        <span aria-hidden className="text-lg leading-none">⚠</span>
        <div>
          <div className="text-sm font-semibold">{TITLE[kind]}</div>
          <div className="text-xs text-amber-200/90">{NUDGE_LABEL[kind]}.</div>
        </div>
      </div>
    </div>
  )
}
