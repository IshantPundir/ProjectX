import type { QuestionOut } from '@/lib/api/reports'
import { statusBadgeMeta, TONE_BG, TONE_INK, tierTone } from './report-format'

export function QuestionByQuestion({ questions }: { questions: QuestionOut[] }) {
  return (
    <section className="rounded-xl border bg-white p-4" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Question by question">
      <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>Question by question</h2>
      <ol className="space-y-3">
        {questions.map((q) => {
          const meta = statusBadgeMeta(q.status_badge)
          const tone = tierTone(q.status_tone)
          const badgeTone = tone === 'neutral' ? meta.tone : tone
          return (
            <li key={q.question_id} className="border-t pt-3 first:border-t-0 first:pt-0" style={{ borderColor: 'var(--px-hairline)' }}>
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[11px] font-bold" style={{ color: 'var(--px-fg-4)' }}>
                  {q.seq}. {q.title}
                </span>
                <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                  style={{ background: TONE_BG[badgeTone], color: TONE_INK[badgeTone] }}>{meta.label}</span>
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
            </li>
          )
        })}
      </ol>
    </section>
  )
}
