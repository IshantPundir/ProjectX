import type { KnockoutResultOut, SignalScorecard } from '@/lib/api/reports'
import { EvidenceQuote } from './EvidenceQuote'
import {
  knockoutStatusLabel, knockoutStatusTone, scoreToTen, signalStateLabel,
  signalStateTone, TONE_BG, TONE_FILL, TONE_INK,
} from './report-format'

interface Props {
  knockouts: KnockoutResultOut[]
  signals: SignalScorecard[]
}

/**
 * Knockouts first (they gate the verdict), then the weighted signals.
 * EVERY knockout shows its reason + evidence inline — that is the
 * mechanism that lets a recruiter catch a miscalibrated rubric (there is
 * deliberately no heuristic guessing which one is wrong).
 */
export function SignalScorecards({ knockouts, signals }: Props) {
  const nonKnockoutSignals = signals.filter((s) => !s.knockout)
  return (
    <div>
      {knockouts.map((k) => {
        const tone = knockoutStatusTone(k.status)
        return (
          <div
            key={k.signal}
            className="mb-2 rounded-lg p-2.5"
            style={{ border: `1px solid ${TONE_INK[tone]}33`, background: TONE_BG[tone] }}
          >
            <div className="flex items-center gap-2">
              <span className="rounded px-1.5 py-px text-[9px] font-semibold" style={{ background: 'var(--px-fg)', color: '#fff' }}>
                KNOCKOUT
              </span>
              <span className="rounded px-1.5 py-px text-[9px] font-semibold"
                style={{ background: TONE_FILL[tone], color: tone === 'danger' ? '#fff' : TONE_INK[tone] }}>
                {knockoutStatusLabel(k.status).toUpperCase()}
              </span>
              <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{k.signal}</span>
            </div>
            <div className="mt-1.5 text-[10.5px]" style={{ color: TONE_INK[tone] }}>Reason: {k.reason}</div>
            {k.evidence.map((e, i) => (
              <EvidenceQuote key={i} evidence={e} toneVar={TONE_INK[tone]} />
            ))}
          </div>
        )
      })}

      {nonKnockoutSignals.map((s) => {
        const tone = signalStateTone(s.state)
        const ten = scoreToTen(s.score)
        return (
          <div key={s.value} className="mb-1.5 rounded-lg border p-2.5" style={{ borderColor: 'var(--px-hairline)' }}>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 flex-none rounded-full" style={{ background: TONE_FILL[tone] }} aria-hidden="true" />
              <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{s.value}</span>
              <span className="rounded px-1.5 py-px text-[9px] font-semibold"
                style={{ background: TONE_FILL[tone], color: tone === 'danger' ? '#fff' : TONE_INK[tone] }}>
                {signalStateLabel(s.state)}
              </span>
              {s.state === 'not_assessed' ? (
                <span className="ml-auto text-[9px]" style={{ color: 'var(--px-fg-4)' }}>
                  opportunity {s.opportunity ?? 'none'}
                </span>
              ) : (
                <>
                  <div className="ml-auto h-1.5 w-16 overflow-hidden rounded-full" style={{ background: 'var(--px-surface-3)' }}>
                    <span className="block h-full rounded-full" style={{ width: `${s.score ?? 0}%`, background: TONE_FILL[tone] }} />
                  </div>
                  <span className="w-8 text-right text-[10px]" style={{ color: 'var(--px-fg-3)' }}>{ten}</span>
                </>
              )}
            </div>
            {s.evidence.map((e, i) => (
              <EvidenceQuote key={i} evidence={e} />
            ))}
          </div>
        )
      })}
    </div>
  )
}
