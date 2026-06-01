'use client'

import { formatTimestamp, statusBadgeMeta, TONE_BG, TONE_INK } from '../report-format'
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
    <div className="theater-strip flex gap-2 overflow-x-auto pb-1" aria-label="Question timeline">
      {markers.map((m) => {
        const badge = statusBadgeMeta(m.statusBadge)
        const active = m.questionId === activeQuestionId
        const seekable = m.askedAtMs != null
        return (
          <button
            key={m.questionId}
            type="button"
            data-active={active ? 'true' : 'false'}
            data-seekable={seekable ? 'true' : 'false'}
            onClick={() => onSelect(m.questionId)}
            aria-label={`Q${m.seq} ${m.title} — ${badge.label}${seekable ? '' : ' (no timestamp)'}`}
            className="theater-card theater-glass flex w-[168px] flex-none flex-col overflow-hidden rounded-xl text-left"
          >
            <div className="relative h-[44px] w-full" style={{ background: TONE_BG[m.tone] }}>
              {m.thumbnailUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={m.thumbnailUrl} alt={`Q${m.seq} ${m.title}`} className="h-full w-full object-cover" />
              ) : (
                <span
                  className="absolute inset-0 grid place-items-center text-[13px] font-extrabold"
                  aria-hidden="true"
                  style={{ color: TONE_INK[m.tone] }}
                >
                  Q{m.seq}
                </span>
              )}
              {seekable && (
                <span
                  className="absolute bottom-1 right-1 rounded px-1 py-0.5 text-[8.5px] font-bold text-white"
                  style={{ background: 'rgba(8,12,16,0.6)' }}
                >
                  {formatTimestamp(m.askedAtMs as number)}
                </span>
              )}
            </div>
            <div className="px-2 py-1.5">
              <div className="text-[8px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
                Q{m.seq}
              </div>
              <div className="truncate text-[11px] font-semibold" style={{ color: 'var(--px-fg)' }} title={m.title}>
                {m.title}
              </div>
              <div className="mt-0.5 text-[9px] font-bold" style={{ color: TONE_INK[m.tone] }}>
                {badge.label}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}
