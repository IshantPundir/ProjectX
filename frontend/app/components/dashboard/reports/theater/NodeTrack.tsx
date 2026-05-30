'use client'

import { formatTimestamp, TONE_FILL } from '../report-format'
import type { TimelineMarker } from './timeline-model'
import './theater.css'

export function NodeTrack({
  markers,
  playheadPct,
  activeQuestionId,
  onSeekMs,
}: {
  markers: TimelineMarker[]
  playheadPct: number
  activeQuestionId: string | null
  onSeekMs: (ms: number) => void
}) {
  return (
    <div className="relative mx-1 mt-2 h-2 rounded" style={{ background: 'rgba(255,255,255,0.14)' }}>
      {/* played portion */}
      <div
        className="absolute left-0 top-0 bottom-0 rounded"
        style={{ width: `${Math.min(100, Math.max(0, playheadPct))}%`, background: 'var(--px-accent)', opacity: 0.6 }}
      />
      {markers.map((m) =>
        m.positionPct == null || m.askedAtMs == null ? null : (
          <button
            key={m.questionId}
            type="button"
            data-active={m.questionId === activeQuestionId ? 'true' : 'false'}
            onClick={() => onSeekMs(m.askedAtMs as number)}
            aria-label={`Q${m.seq} jump to ${formatTimestamp(m.askedAtMs)}`}
            className="theater-node absolute top-1/2 h-[14px] w-[14px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white"
            style={{ left: `${m.positionPct}%`, border: `3px solid ${TONE_FILL[m.tone]}` }}
          />
        ),
      )}
    </div>
  )
}
