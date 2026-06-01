'use client'

import type { ReportRead } from '@/lib/api/reports'
import { verdictMeta, TONE_BG, TONE_INK } from './report-format'

// Re-exported for back-compat: the pure helper + seek-api type live in the theater model.
// `PlaybackSeekApi` is consumed by the theater (TheaterStage / useTheaterState).
export { activeSegmentIndex } from './theater/timeline-model'
export interface PlaybackSeekApi {
  seekToMs: (ms: number) => void
}

const CARD = 'rounded-xl border bg-white p-3.5'

/**
 * Report-page playback ENTRY: a poster with a Play button. Clicking it calls
 * `onOpen` so the parent (ReportView) opens the Review Theater. The theater is
 * owned by ReportView so the proctoring panel can also open it at a flag.
 */
export function SessionPlayback({
  report,
  onOpen,
}: {
  report: ReportRead
  onOpen: () => void
}) {
  const v = verdictMeta(report.verdict)
  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <button
        type="button"
        onClick={onOpen}
        aria-label="Play session recording — open review theater"
        className="relative flex w-full items-center justify-center rounded-lg"
        style={{
          aspectRatio: '16 / 9',
          background: 'radial-gradient(110% 90% at 50% 18%, #fbf4ee, #e7eef3 60%, #dfe9ee)',
          border: '1px solid var(--px-hairline)',
        }}
      >
        <span
          className="grid h-14 w-14 place-items-center rounded-full text-[20px] text-white shadow-lg"
          style={{ background: 'var(--px-accent)' }}
          aria-hidden="true"
        >
          ▶
        </span>
        <span
          className="absolute right-2.5 top-2.5 rounded-full px-2.5 py-1 text-[11px] font-bold"
          style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}
        >
          {v.label}
        </span>
        <span className="absolute bottom-2.5 left-2.5 text-[11px] font-semibold" style={{ color: 'var(--px-fg-3)' }}>
          Review session →
        </span>
      </button>
    </div>
  )
}
