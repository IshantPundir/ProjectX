'use client'

import type { CSSProperties } from 'react'

import { formatTimestamp, statusBadgeMeta, TONE_FILL, TONE_INK } from '../report-format'
import type { TimelineMarker } from './timeline-model'

/**
 * Vertical rail of compact question pills on the right of the full-session
 * theater. Markers arrive pre-filtered + ask-ordered (see buildRailMarkers).
 * The pill matching the current playhead (`activeQuestionId`) scales up + is
 * ringed, so the emphasis tracks the timeline as the recording plays.
 */
export function QuestionRail({
  markers,
  activeQuestionId,
  onSelect,
}: {
  markers: TimelineMarker[]
  activeQuestionId: string | null
  onSelect: (questionId: string) => void
}) {
  return (
    <div
      className="theater-scroll flex max-h-full flex-col items-end gap-2 overflow-y-auto py-1 pl-6 pr-0.5"
      aria-label="Question timeline"
    >
      {markers.map((m) => {
        const badge = statusBadgeMeta(m.statusBadge)
        const active = m.questionId === activeQuestionId
        const seekable = m.askedAtMs != null
        const cardStyle = { '--tf': TONE_FILL[m.tone] } as CSSProperties
        return (
          <button
            key={m.questionId}
            type="button"
            data-active={active ? 'true' : 'false'}
            data-seekable={seekable ? 'true' : 'false'}
            onClick={() => onSelect(m.questionId)}
            aria-label={`Q${m.seq} ${m.title} — ${badge.label}${seekable ? '' : ' (no timestamp)'}`}
            className="theater-qrail-card flex w-[210px] flex-none items-center gap-2.5 rounded-full p-1.5 pr-3.5 text-left"
            style={cardStyle}
          >
            <span
              className="grid h-8 w-8 flex-none place-items-center rounded-full text-[12px] font-extrabold text-white"
              style={{ background: TONE_FILL[m.tone] }}
              aria-hidden="true"
            >
              {m.seq}
            </span>
            <div className="min-w-0 flex-1">
              <div
                className="truncate text-[11.5px] font-bold leading-snug"
                style={{ color: 'var(--px-fg)' }}
                title={m.title}
              >
                {m.title}
              </div>
              <div className="mt-0.5 flex items-center gap-1">
                <span className="h-1.5 w-1.5 flex-none rounded-full" style={{ background: TONE_FILL[m.tone] }} />
                <span className="truncate text-[9px] font-bold" style={{ color: TONE_INK[m.tone] }}>
                  {badge.label}
                </span>
                {seekable && (
                  <span className="flex-none text-[9px] font-bold tabular-nums" style={{ color: 'var(--px-fg-4)' }}>
                    · {formatTimestamp(m.askedAtMs as number)}
                  </span>
                )}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}
