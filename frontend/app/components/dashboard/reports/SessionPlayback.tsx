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
  // A real recording frame for the poster — first question that has one.
  const poster = report.questions.find((q) => q.thumbnail_url)?.thumbnail_url ?? null
  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <button
        type="button"
        onClick={onOpen}
        aria-label="Play session recording — open review theater"
        className="report-playposter group relative flex w-full items-center justify-center overflow-hidden rounded-lg"
        style={{
          aspectRatio: '16 / 9',
          background: 'radial-gradient(110% 90% at 50% 18%, #fbf4ee, #e7eef3 60%, #dfe9ee)',
          border: '1px solid var(--px-hairline)',
        }}
      >
        {poster && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={poster} alt="" aria-hidden="true" className="absolute inset-0 h-full w-full object-cover" />
        )}
        {/* legibility scrim — darker when there's a photo behind the chrome */}
        <span
          className="absolute inset-0"
          aria-hidden="true"
          style={{
            background: poster
              ? 'linear-gradient(180deg, rgba(8,16,24,0.34) 0%, rgba(8,16,24,0.04) 32%, rgba(8,16,24,0.10) 70%, rgba(8,16,24,0.46) 100%)'
              : 'transparent',
          }}
        />
        <span className="report-playbtn relative grid h-14 w-14 place-items-center rounded-full text-[20px] text-white" aria-hidden="true">
          ▶
        </span>
        <span
          className="absolute right-2.5 top-2.5 rounded-full px-2.5 py-1 text-[11px] font-bold"
          style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}
        >
          {v.label}
        </span>
        <span
          className="absolute bottom-2.5 left-2.5 text-[11px] font-semibold"
          style={{ color: poster ? '#fff' : 'var(--px-fg-3)', textShadow: poster ? '0 1px 6px rgba(0,0,0,0.5)' : 'none' }}
        >
          Review session →
        </span>
      </button>
    </div>
  )
}
