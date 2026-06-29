'use client'

import type { ReportRead } from '@/components/recordings/api/reports'
import { useSessionRecording } from '@/components/recordings/hooks/use-session-recording'
import { verdictMeta, TONE_BG, TONE_INK } from './report-format'
import { pickPosterUrl } from './theater/timeline-model'

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
  // Poster the static entry with the same mid-interview question frame the
  // ReviewTheater <video> uses, so opening the theater isn't a visual jump.
  // duration_seconds → durationMs mirrors ReviewTheater; the recording query is
  // shared (TanStack dedupes on the session-recording key). When the duration
  // isn't known yet, pickPosterUrl returns null → the gradient fallback shows.
  const { data: rec } = useSessionRecording(report.session_id ?? '')
  const durationMs = (rec?.duration_seconds ?? 0) * 1000
  // Prefer the candidate's reference photo (captured on the camera step) as the
  // session poster; fall back to a mid-interview question frame for older sessions.
  const poster = report.reference_photo_url ?? pickPosterUrl(report.questions, durationMs)
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
