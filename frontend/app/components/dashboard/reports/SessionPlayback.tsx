'use client'

import { useState } from 'react'

import type { ReportRead } from '@/lib/api/reports'
import { verdictMeta, TONE_BG, TONE_INK } from './report-format'
import { ReviewTheater } from './theater/ReviewTheater'

// Re-exported for back-compat: the pure helper + seek-api type now live in the theater model.
// `PlaybackSeekApi` is still consumed by ReportView (its ProctoringIntegrityPanel seek ref).
export { activeSegmentIndex } from './theater/timeline-model'
export interface PlaybackSeekApi {
  seekToMs: (ms: number) => void
}

const CARD = 'rounded-xl border bg-white p-3.5'

/**
 * Report-page session playback ENTRY: a poster with a Play button. Clicking it
 * opens the immersive Review Theater (glass popup + scannable session timeline).
 * The theater owns its own video + seek.
 */
export function SessionPlayback({
  report,
  candidateName,
  subtitle,
}: {
  report: ReportRead
  candidateName: string
  subtitle: string
}) {
  const [open, setOpen] = useState(false)
  const v = verdictMeta(report.verdict)

  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <button
        type="button"
        onClick={() => setOpen(true)}
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
      <VerbalContentOnlyBadge />
      {open && (
        <ReviewTheater
          open={open}
          report={report}
          candidateName={candidateName}
          subtitle={subtitle}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  )
}

export function VerbalContentOnlyBadge() {
  return (
    <div className="mt-2.5 flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[11px]"
      style={{ color: 'var(--px-ai)', background: 'var(--px-ai-bg)', borderColor: 'var(--px-ai-line)' }}>
      🛈&nbsp;Verbal-content-only — scored on what the candidate said. No facial, affect, or appearance analysis.
    </div>
  )
}
