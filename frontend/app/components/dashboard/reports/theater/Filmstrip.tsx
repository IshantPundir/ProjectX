'use client'

import type { CSSProperties } from 'react'

import { formatTimestamp, statusBadgeMeta, TONE_FILL, TONE_INK } from '../report-format'
import type { TimelineMarker } from './timeline-model'
import './theater.css'

export function Filmstrip({
  markers,
  activeQuestionId,
  onSelect,
}: {
  markers: TimelineMarker[]
  activeQuestionId: string | null
  onSelect: (questionId: string) => void
}) {
  return (
    <div className="theater-scroll flex gap-2.5 overflow-x-auto pb-1.5 pt-1" aria-label="Question timeline">
      {markers.map((m) => {
        const badge = statusBadgeMeta(m.statusBadge)
        const active = m.questionId === activeQuestionId
        const seekable = m.askedAtMs != null
        // The card's whole surface is tinted by status tone via --tf (a pastel
        // of the tone's fill, mixed in CSS). Lively, but the color still means
        // pass / partial / fail.
        const cardStyle = { '--tf': TONE_FILL[m.tone] } as CSSProperties
        return (
          <button
            key={m.questionId}
            type="button"
            data-active={active ? 'true' : 'false'}
            data-seekable={seekable ? 'true' : 'false'}
            onClick={() => onSelect(m.questionId)}
            aria-label={`Q${m.seq} ${m.title} — ${badge.label}${seekable ? '' : ' (no timestamp)'}`}
            className="theater-tl-card flex w-[236px] flex-none items-center gap-3 rounded-full p-1.5 pr-5 text-left"
            style={cardStyle}
          >
            <div
              className="relative h-14 w-14 flex-none overflow-hidden rounded-full"
              style={{ background: 'color-mix(in srgb, var(--tf) 55%, #fff)' }}
            >
              {m.thumbnailUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={m.thumbnailUrl} alt={`Q${m.seq} ${m.title}`} className="h-full w-full object-cover" />
              ) : (
                <span
                  className="absolute inset-0 grid place-items-center text-[15px] font-extrabold"
                  aria-hidden="true"
                  style={{ color: TONE_INK[m.tone] }}
                >
                  Q{m.seq}
                </span>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] font-extrabold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
                  Q{m.seq}
                </span>
                {seekable && (
                  <span className="text-[9px] font-bold tabular-nums" style={{ color: 'var(--px-fg-4)' }}>
                    · {formatTimestamp(m.askedAtMs as number)}
                  </span>
                )}
              </div>
              <div className="truncate text-[12.5px] font-bold leading-snug" style={{ color: 'var(--px-fg)' }} title={m.title}>
                {m.title}
              </div>
              <div className="mt-0.5 flex items-center gap-1">
                <span className="h-1.5 w-1.5 flex-none rounded-full" style={{ background: TONE_FILL[m.tone] }} />
                <span className="truncate text-[9.5px] font-bold" style={{ color: TONE_INK[m.tone] }}>
                  {badge.label}
                </span>
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}
