import type { CSSProperties } from 'react'

import type { QuestionOut } from '@/lib/api/reports'
import { StarRating } from './StarRating'
import { statusBadgeMeta, TONE_BG, TONE_INK, tierTone } from './report-format'

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

export function QuestionByQuestion({ questions }: { questions: QuestionOut[] }) {
  return (
    <section className="rounded-xl border bg-white p-4 px-card" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Question by question">
      <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>Question by question</h2>
      <ol className="space-y-3">
        {questions.map((q, i) => {
          const meta = statusBadgeMeta(q.status_badge)
          const tone = tierTone(q.status_tone)
          const badgeTone = tone === 'neutral' ? meta.tone : tone
          const listenForHits = q.listen_for_hits ?? []
          const redFlagsTripped = q.red_flags_tripped ?? []
          const probesAvailable = q.probes_available ?? 0
          const hasScore = q.score != null
          const scoreLabel = hasScore
            ? `${(q.score! / 2).toFixed(1)} / 5`
            : null

          return (
            <li key={q.question_id} className="group border-t px-2 pt-3 first:border-t-0 first:pt-0 px-qrow px-reveal" style={{ borderColor: 'var(--px-hairline)', '--px-stagger': i } as CSSProperties}>
              {/* Header row: seq dot + question text + star rating */}
              <div className="mb-2 flex gap-3 items-start">
                {/* Sequence dot */}
                <div
                  className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-lg text-[12px] font-extrabold"
                  style={{ background: 'var(--px-accent-tint)', color: 'var(--px-accent)' }}
                >
                  {q.seq}
                </div>
                {/* Question text (full — never truncated; never q.title) */}
                <p className="flex-1 text-[14px] font-semibold leading-snug" style={{ color: 'var(--px-fg)' }}>
                  {q.question_text}
                </p>
                {/* Star rating or "Not assessed" */}
                <div className="flex shrink-0 flex-col items-center gap-0.5 pl-2">
                  {hasScore ? (
                    <>
                      <StarRating valueTen={q.score!} size={16} />
                      <span className="text-[11px] font-extrabold" style={{ color: 'var(--px-fg-3)' }}>
                        {scoreLabel}
                      </span>
                    </>
                  ) : (
                    <span
                      className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                      style={{ background: 'var(--px-surface-2)', color: 'var(--px-fg-4)' }}
                    >
                      Not assessed
                    </span>
                  )}
                </div>
              </div>

              {/* Chips row: difficulty + status badge (+ probe count) */}
              <div className="mb-1.5 ml-[38px] flex flex-wrap items-center gap-1.5">
                {q.difficulty && (
                  <span
                    className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide px-chip"
                    style={{
                      background: DIFFICULTY_BG[q.difficulty] ?? 'var(--px-surface-2)',
                      color: DIFFICULTY_INK[q.difficulty] ?? 'var(--px-fg-4)',
                    }}
                  >
                    {q.difficulty}
                  </span>
                )}
                <span
                  className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide px-chip"
                  style={{ background: TONE_BG[badgeTone], color: TONE_INK[badgeTone] }}
                >
                  {meta.label}
                </span>
                {probesAvailable > 0 && (
                  <span className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
                    {q.probes_used ?? 0}/{probesAvailable} probes
                  </span>
                )}
              </div>

              {/* Candidate quote */}
              {q.candidate_quote && (
                <blockquote className="ml-[38px] mt-1 border-l-2 pl-2 text-[13px] italic" style={{ borderColor: 'var(--px-hairline-strong)', color: 'var(--px-fg-3)' }}>
                  &ldquo;{q.candidate_quote}&rdquo;
                </blockquote>
              )}

              {/* Our read */}
              {q.our_read && (
                <p className="ml-[38px] mt-1.5 text-[12.5px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>
                  <span className="font-semibold" style={{ color: 'var(--px-fg-3)' }}>Our read: </span>{q.our_read}
                </p>
              )}

              {/* Listen-for hits + red-flag pills */}
              {(listenForHits.length > 0 || redFlagsTripped.length > 0) && (
                <div className="ml-[38px] mt-1.5 flex flex-wrap gap-1.5">
                  {listenForHits.map((hit) => (
                    <span
                      key={hit}
                      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
                      style={{ background: 'var(--px-ok-bg)', color: 'var(--px-ok)' }}
                    >
                      <span aria-hidden>✓</span>{hit}
                    </span>
                  ))}
                  {redFlagsTripped.map((flag) => (
                    <span
                      key={flag}
                      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
                      style={{ background: 'var(--px-danger-bg)', color: 'var(--px-danger)' }}
                    >
                      <span aria-hidden>!</span>{flag}
                    </span>
                  ))}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
