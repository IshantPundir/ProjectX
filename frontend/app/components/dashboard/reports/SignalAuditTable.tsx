import type { SignalAssessmentOut } from '@/lib/api/reports'
import { formatTen, scoreBandTone, TONE_FILL } from './report-format'

export function SignalAuditTable({ assessments }: { assessments: SignalAssessmentOut[] }) {
  if (!assessments.length) return null
  return (
    <details className="rounded-xl border bg-white p-4 px-card" style={{ borderColor: 'var(--px-hairline)' }}>
      <summary className="cursor-pointer text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
        Audit detail — signal by signal ({assessments.length})
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead>
            <tr style={{ color: 'var(--px-fg-4)' }} className="text-left">
              <th className="py-1 pr-2 font-semibold">Signal</th>
              <th className="py-1 pr-2 font-semibold">Must-have</th>
              <th className="py-1 pr-2 font-semibold">Provenance</th>
              <th className="py-1 pr-2 font-semibold">Grade</th>
              <th className="py-1 pr-2 font-semibold">Score</th>
              <th className="py-1 font-semibold">Note</th>
            </tr>
          </thead>
          <tbody>
            {assessments.map((a) => (
              <tr key={a.signal} className="border-t align-top px-arow" style={{ borderColor: 'var(--px-hairline)' }}>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-2)' }}>{a.signal}</td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>{a.knockout ? 'yes' : '—'}</td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>
                  {a.provenance}{a.overridden ? ' *' : ''}
                </td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>
                  {a.level === 'thin' ? (
                    <span className="rounded px-1 text-[9px] font-semibold"
                          style={{ background: 'var(--px-caution-bg)', color: 'var(--px-caution)' }}
                          title="Correct vocabulary but no demonstrated depth — possible bluff.">
                      thin
                    </span>
                  ) : (a.level ?? '—')}
                  {a.cross_credit_applied && (
                    <span className="ml-1 rounded px-1 text-[9px] font-semibold"
                          style={{ background: 'var(--px-ok-bg)', color: 'var(--px-ok)' }}>
                      cross-credited
                    </span>
                  )}
                  {a.level_basis && (
                    <span className="mt-0.5 block text-[10px] leading-snug" style={{ color: 'var(--px-fg-4)' }}>
                      {a.level_basis}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>
                  <span className="tabular-nums">{formatTen(a.score) ?? '—'}</span>
                  {/* slim horizontal mini-bar: width = score × 10%, tinted by band tone */}
                  <div className="mt-0.5 h-[3px] w-full overflow-hidden rounded-full" style={{ background: 'var(--px-hairline)' }}>
                    <div
                      data-testid="score-mini-bar"
                      className="h-full rounded-full"
                      style={{
                        width: a.score != null ? `${a.score * 10}%` : '0%',
                        background: TONE_FILL[scoreBandTone(a.score)],
                      }}
                    />
                  </div>
                </td>
                <td className="py-1" style={{ color: 'var(--px-fg-4)' }}>{a.override_reason ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1.5 text-[10px]" style={{ color: 'var(--px-fg-4)' }}>* re-checked and adjusted by the post-session scorer.</p>
      </div>
    </details>
  )
}
