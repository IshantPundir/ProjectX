'use client'

import type { DecisionOut, QuestionOut } from '@/components/recordings/api/reports'
import { formatTimestamp, statusBadgeMeta, TONE_BG, TONE_INK } from '../report-format'
import { StarRating } from '../StarRating'
import { GlassBackdrop } from './GlassBackdrop'

const DIFFICULTY_BG: Record<string, string> = {
  easy: 'var(--px-ok-bg)',
  medium: 'var(--px-caution-bg)',
  hard: 'var(--px-danger-bg)',
}
const DIFFICULTY_INK: Record<string, string> = {
  easy: 'var(--px-ok)',
  medium: 'var(--px-caution)',
  hard: 'var(--px-danger)',
}

// Engine per-question closure → human label. Unknown/null values are omitted.
const CLOSURE_LABEL: Record<string, string> = {
  satisfied: 'Satisfied',
  tapped_out: 'Tapped out',
  absent: 'Absent',
  truncated: 'Truncated',
}

// Rubric-anchored grade (level) → human label.
const LEVEL_LABEL: Record<string, string> = {
  strong: 'Strong',
  solid: 'Solid',
  thin: 'Thin',
  absent: 'Absent',
  not_reached: 'Not reached',
}

// The detail panel is questions-only. Proctoring violations are surfaced via a
// hover card on the scrubber, never here.
export type MomentSelection =
  | { type: 'question'; question: QuestionOut }
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
    // Outer stays a non-scrolling clip container so the GlassBackdrop (absolute,
    // z-index:-1) paints; scrolling moves to the inner wrapper. (A scroll
    // container on the same element as theater-glass suppressed the backdrop.)
    <div className="theater-glass flex max-h-full w-full flex-col rounded-2xl p-4">
      <GlassBackdrop />
      <div className="theater-scroll flex min-h-0 flex-1 flex-col overflow-y-auto">
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

      {selection?.type === 'question' && (() => {
        const q = selection.question
        const meta = statusBadgeMeta(q.status_badge)
        const hasScore = q.score != null
        const listenForHits = q.listen_for_hits ?? []
        const redFlagsTripped = q.red_flags_tripped ?? []
        const probesAvailable = q.probes_available ?? 0
        const closureLabel = q.closure ? CLOSURE_LABEL[q.closure] ?? null : null
        const levelLabel = q.level ? LEVEL_LABEL[q.level] ?? null : null
        return (
          <div className="flex flex-1 flex-col overflow-y-auto">
            <div className="mb-2 flex items-center gap-2">
              <span
                className="flex-none rounded-md px-2 py-0.5 text-[10px] font-extrabold"
                style={{ background: TONE_BG[meta.tone], color: TONE_INK[meta.tone] }}
              >
                {meta.label}
              </span>
              <span className="text-[13px] font-bold">Q{q.seq} · {q.title}</span>
            </div>

            {/* stars / score, difficulty, probe count */}
            <div className="mb-2 flex flex-wrap items-center gap-2">
              {hasScore ? (
                <span className="flex items-center gap-1.5">
                  <StarRating valueTen={q.score as number} size={14} />
                  <span className="text-[11px] font-extrabold" style={{ color: 'var(--px-fg-3)' }}>
                    {((q.score as number) / 2).toFixed(1)} / 5
                  </span>
                </span>
              ) : (
                <span
                  className="rounded px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wide"
                  style={{ background: 'var(--px-surface-2)', color: 'var(--px-fg-4)' }}
                >
                  Not assessed
                </span>
              )}
              {q.difficulty && (
                <span
                  className="rounded px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wide"
                  style={{ background: DIFFICULTY_BG[q.difficulty] ?? 'var(--px-surface-2)', color: DIFFICULTY_INK[q.difficulty] ?? 'var(--px-fg-4)' }}
                >
                  {q.difficulty}
                </span>
              )}
              {probesAvailable > 0 && (
                <span className="text-[10.5px]" style={{ color: 'var(--px-fg-4)' }}>
                  {q.probes_used ?? 0}/{probesAvailable} probes
                </span>
              )}
            </div>

            <p className="mb-2 text-[11.5px]" style={{ color: 'var(--px-fg-3)', whiteSpace: 'pre-wrap' }}>{q.question_text}</p>

            {q.candidate_quote && (
              <p className="mb-2 border-l-2 pl-2 text-[12px] italic" style={{ borderColor: 'var(--px-caution)', color: 'var(--px-fg)', whiteSpace: 'pre-wrap' }}>
                {q.candidate_quote}
              </p>
            )}

            {(closureLabel || levelLabel) && (
              <div className="mb-2">
                <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Agent verdict</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                  {closureLabel && (
                    <span
                      className="rounded-md px-2 py-0.5 text-[10px] font-extrabold"
                      style={{ background: TONE_BG.neutral, color: TONE_INK.neutral }}
                    >
                      {closureLabel}
                    </span>
                  )}
                  {levelLabel && (
                    <span className="text-[12px] font-bold" style={{ color: 'var(--px-fg-3)' }}>{levelLabel}</span>
                  )}
                </div>
              </div>
            )}

            {(listenForHits.length > 0 || redFlagsTripped.length > 0) && (
              <div className="mb-2">
                <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Observations</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {listenForHits.map((hit) => (
                    <span
                      key={hit}
                      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
                      style={{ background: 'var(--px-ok-bg)', color: 'var(--px-ok)' }}
                    >
                      <span aria-hidden>✓</span>{hit}
                    </span>
                  ))}
                  {redFlagsTripped.map((flag) => (
                    <span
                      key={flag}
                      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
                      style={{ background: 'var(--px-danger-bg)', color: 'var(--px-danger)' }}
                    >
                      <span aria-hidden>!</span>{flag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {q.our_read && (
              <>
                <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Our read</div>
                <p className="text-[12px]" style={{ color: 'var(--px-fg-3)', whiteSpace: 'pre-wrap' }}>{q.our_read}</p>
              </>
            )}

            {q.asked_at_ms != null && (
              <button
                type="button"
                onClick={() => onJump(q.asked_at_ms as number)}
                className="mt-auto pt-2 text-left text-[11.5px] font-bold"
                style={{ color: 'var(--px-accent)' }}
              >
                ▶ Jump to {formatTimestamp(q.asked_at_ms)}
              </button>
            )}
          </div>
        )
      })()}
      </div>
    </div>
  )
}
