import type { CSSProperties } from 'react'

import type { QuestionOut } from '@/lib/api/reports'
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
          return (
            <li key={q.question_id} className="group border-t px-2 pt-3 first:border-t-0 first:pt-0 px-qrow px-reveal" style={{ borderColor: 'var(--px-hairline)', '--px-stagger': i } as CSSProperties}>
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[11px] font-bold" style={{ color: 'var(--px-fg-4)' }}>
                  {q.seq}. {q.title}
                </span>
                <div className="flex shrink-0 items-center gap-1.5">
                  {q.difficulty && (
                    <span className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide px-chip"
                      style={{
                        background: DIFFICULTY_BG[q.difficulty] ?? 'var(--px-surface-2)',
                        color: DIFFICULTY_INK[q.difficulty] ?? 'var(--px-fg-4)',
                      }}>
                      {q.difficulty}
                    </span>
                  )}
                  <span className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide px-chip"
                    style={{ background: TONE_BG[badgeTone], color: TONE_INK[badgeTone] }}>{meta.label}</span>
                </div>
              </div>
              <p className="text-[13.5px] font-medium" style={{ color: 'var(--px-fg)' }}>{q.question_text}</p>
              {q.candidate_quote && (
                <blockquote className="mt-1 border-l-2 pl-2 text-[13px] italic" style={{ borderColor: 'var(--px-hairline-strong)', color: 'var(--px-fg-3)' }}>
                  &ldquo;{q.candidate_quote}&rdquo;
                </blockquote>
              )}
              {q.our_read && (
                <p className="mt-1.5 text-[12.5px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>
                  <span className="font-semibold" style={{ color: 'var(--px-fg-3)' }}>Our read: </span>{q.our_read}
                </p>
              )}
              {listenForHits.length > 0 && (
                <div className="mt-1.5">
                  <p className="text-[11px] font-semibold" style={{ color: 'var(--px-ok)' }}>Listen for ✓</p>
                  <ul className="mt-0.5 space-y-0.5">
                    {listenForHits.map((hit) => (
                      <li key={hit} className="text-[12px] leading-snug" style={{ color: 'var(--px-fg-2)' }}>{hit}</li>
                    ))}
                  </ul>
                </div>
              )}
              {redFlagsTripped.length > 0 && (
                <div className="mt-1.5">
                  <p className="text-[11px] font-semibold" style={{ color: 'var(--px-danger)' }}>Red flags ⚠</p>
                  <ul className="mt-0.5 space-y-0.5">
                    {redFlagsTripped.map((flag) => (
                      <li key={flag} className="text-[12px] leading-snug" style={{ color: 'var(--px-fg-2)' }}>{flag}</li>
                    ))}
                  </ul>
                </div>
              )}
              {probesAvailable > 0 && (
                <p className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
                  {q.probes_used ?? 0}/{probesAvailable} probes
                </p>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
