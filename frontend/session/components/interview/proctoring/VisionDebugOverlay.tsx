'use client'

import type { VisionSignals } from './vision/types'

/**
 * DEV-ONLY tracking readout + approximate gaze pointer (spec §5.3). Pure render
 * of signals — mounted only when env.NEXT_PUBLIC_PROCTORING_DEBUG is true
 * (gating happens in ProctoringGuard, NOT here). MUST NEVER ship enabled
 * (pre-prod action item, spec §10). The gaze dot is head-pose-derived and
 * UNCALIBRATED — it shows direction, not an exact look-at point.
 */
export function VisionDebugOverlay({ signals }: { signals: VisionSignals }) {
  const p = signals.pose
  const gp = signals.gazePoint
  const trail = signals.gazeTrail
  return (
    <>
      {gp && (
        <div data-testid="vision-gaze-layer" className="pointer-events-none fixed inset-0 z-[70]">
          {trail.map((t, i) => (
            <span
              key={i}
              className="absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-400"
              style={{
                left: `${t.x * 100}%`,
                top: `${t.y * 100}%`,
                opacity: ((i + 1) / trail.length) * 0.45,
              }}
            />
          ))}
          <span
            data-testid="vision-gaze-dot"
            className="absolute size-5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-emerald-300 bg-emerald-400/30 shadow-[0_0_14px_2px_rgba(52,211,153,0.7)]"
            style={{ left: `${gp.x * 100}%`, top: `${gp.y * 100}%` }}
          />
        </div>
      )}
      <div
        data-testid="vision-debug-overlay"
        className="pointer-events-none fixed bottom-2 right-2 z-[80] rounded-md bg-black/75 px-3 py-2 font-mono text-[11px] leading-tight text-green-300 backdrop-blur-sm"
      >
        <div>faces: {signals.faceCount}</div>
        <div>zone: {signals.gazeZone ?? '—'}</div>
        <div>gaze: {gp ? `${gp.x.toFixed(2)}, ${gp.y.toFixed(2)}` : '—'}</div>
        <div>
          yaw {p ? p.yaw.toFixed(1) : '—'} / pitch {p ? p.pitch.toFixed(1) : '—'} / roll{' '}
          {p ? p.roll.toFixed(1) : '—'}
        </div>
        <div>
          ear: {signals.earValue?.toFixed(2) ?? '—'} {signals.blinking ? '(blink)' : ''}
        </div>
        <div>quality: {signals.quality}</div>
        <div>fps: {signals.fps.toFixed(0)}</div>
      </div>
    </>
  )
}
