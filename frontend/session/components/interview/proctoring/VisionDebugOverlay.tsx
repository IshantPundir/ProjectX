'use client'

import type { VisionSignals } from './vision/types'

/**
 * DEV-ONLY tracking readout (spec §5.3). Mounted only when
 * env.NEXT_PUBLIC_PROCTORING_DEBUG is true — gating happens in
 * ProctoringGuard, NOT here, so this stays a pure render of signals.
 * MUST NEVER ship enabled (pre-prod action item, spec §10).
 */
export function VisionDebugOverlay({ signals }: { signals: VisionSignals }) {
  const p = signals.pose
  return (
    <div
      data-testid="vision-debug-overlay"
      className="pointer-events-none fixed bottom-2 right-2 z-[80] rounded-md bg-black/75 px-3 py-2 font-mono text-[11px] leading-tight text-green-300 backdrop-blur-sm"
    >
      <div>faces: {signals.faceCount}</div>
      <div>zone: {signals.gazeZone ?? '—'}</div>
      <div>
        yaw {p ? p.yaw.toFixed(1) : '—'} / pitch {p ? p.pitch.toFixed(1) : '—'} / roll{' '}
        {p ? p.roll.toFixed(1) : '—'}
      </div>
      <div>ear: {signals.earValue?.toFixed(2) ?? '—'} {signals.blinking ? '(blink)' : ''}</div>
      <div>quality: {signals.quality}</div>
      <div>fps: {signals.fps.toFixed(0)}</div>
    </div>
  )
}
