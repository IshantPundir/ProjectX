'use client'

import type { DecisionOut, QuestionOut } from '@/lib/api/reports'
import { formatTimestamp, statusBadgeMeta, TONE_BG, TONE_INK } from '../report-format'
import { GlassBackdrop } from './GlassBackdrop'
import type { FlagMarker } from './timeline-model'

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

export type MomentSelection =
  | { type: 'question'; question: QuestionOut }
  | { type: 'flag'; flag: FlagMarker }
  | null

export function ThisMomentPanel({
  selection,
  decision,
  onJump,
}: {
  selection: MomentSelection
  decision: DecisionOut
  onJump: (ms: number) => void
}) {
  return (
    <div className="theater-glass flex max-h-full flex-col overflow-y-auto rounded-2xl p-4">
      <GlassBackdrop />
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-extrabold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: 'var(--px-accent)' }} />
        This moment
      </div>

      {selection === null && (
        <div className="space-y-3 overflow-y-auto">
          <p className="text-[13px] font-semibold" style={{ color: 'var(--px-fg)' }}>{decision.headline}</p>
          <div>
            <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-ok)' }}>{decision.why_positive.title}</div>
            <p className="mt-0.5 text-[12px]" style={{ color: 'var(--px-fg-3)' }}>{decision.why_positive.body}</p>
          </div>
          <div>
            <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-danger)' }}>{decision.why_negative.title}</div>
            <p className="mt-0.5 text-[12px]" style={{ color: 'var(--px-fg-3)' }}>{decision.why_negative.body}</p>
          </div>
        </div>
      )}

      {selection?.type === 'question' && (
        <div className="flex flex-1 flex-col overflow-y-auto">
          <div className="mb-2 flex items-center gap-2">
            <span
              className="rounded-md px-2 py-0.5 text-[10px] font-extrabold"
              style={{ background: TONE_BG[statusBadgeMeta(selection.question.status_badge).tone], color: TONE_INK[statusBadgeMeta(selection.question.status_badge).tone] }}
            >
              {statusBadgeMeta(selection.question.status_badge).label}
            </span>
            <span className="text-[13px] font-bold">Q{selection.question.seq} · {selection.question.title}</span>
          </div>
          <p className="mb-2 text-[11.5px]" style={{ color: 'var(--px-fg-3)', whiteSpace: 'pre-wrap' }}>{selection.question.question_text}</p>
          {selection.question.candidate_quote && (
            <p className="mb-2 border-l-2 pl-2 text-[12px] italic" style={{ borderColor: 'var(--px-caution)', color: 'var(--px-fg)', whiteSpace: 'pre-wrap' }}>
              {selection.question.candidate_quote}
            </p>
          )}
          {selection.question.our_read && (
            <>
              <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Our read</div>
              <p className="text-[12px]" style={{ color: 'var(--px-fg-3)', whiteSpace: 'pre-wrap' }}>{selection.question.our_read}</p>
            </>
          )}
          {selection.question.asked_at_ms != null && (
            <button
              type="button"
              onClick={() => onJump(selection.question.asked_at_ms as number)}
              className="mt-auto pt-2 text-left text-[11.5px] font-bold"
              style={{ color: 'var(--px-accent)' }}
            >
              ▶ Jump to {formatTimestamp(selection.question.asked_at_ms)}
            </button>
          )}
        </div>
      )}

      {selection?.type === 'flag' && (
        <div className="flex flex-1 flex-col overflow-y-auto">
          <div className="mb-2 text-[13px] font-bold" style={{ color: 'var(--px-danger)' }}>
            {KIND_LABEL[selection.flag.kind] ?? selection.flag.kind}
          </div>
          {selection.flag.thumbnailUrl && (
            <img src={selection.flag.thumbnailUrl} alt="Flagged moment" className="mb-2 w-full rounded-lg object-cover" />
          )}
          <p className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
            {formatTimestamp(selection.flag.startMs)}–{formatTimestamp(selection.flag.endMs)} · {Math.round(selection.flag.confidence * 100)}% confidence
          </p>
          <button
            type="button"
            onClick={() => onJump(selection.flag.startMs)}
            className="mt-auto pt-2 text-left text-[11.5px] font-bold"
            style={{ color: 'var(--px-accent)' }}
          >
            ▶ Jump to {formatTimestamp(selection.flag.startMs)}
          </button>
        </div>
      )}
    </div>
  )
}
