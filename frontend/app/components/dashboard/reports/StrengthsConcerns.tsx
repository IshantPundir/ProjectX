import type { ConcernOut, StrengthOut } from '@/lib/api/reports'
import { severityMeta, TONE_BG, TONE_INK } from './report-format'

export function StrengthsConcerns({ strengths, concerns }: { strengths: StrengthOut[]; concerns: ConcernOut[] }) {
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Strengths and concerns">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-ok)' }}>
            Strengths {strengths.length}
          </h2>
          <ul className="space-y-2">
            {strengths.map((s, i) => (
              <li key={i}>
                <div className="text-[11.5px] font-semibold" style={{ color: 'var(--px-fg)' }}>{s.title}</div>
                <p className="text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{s.detail}</p>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-danger)' }}>
            Concerns {concerns.length}
          </h2>
          <ul className="space-y-2">
            {concerns.map((c, i) => {
              const sev = severityMeta(c.severity)
              return (
                <li key={i}>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11.5px] font-semibold" style={{ color: 'var(--px-fg)' }}>{c.title}</span>
                    <span className="rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                      style={{ background: TONE_BG[sev.tone], color: TONE_INK[sev.tone] }}>{sev.label}</span>
                  </div>
                  <p className="text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{c.detail}</p>
                </li>
              )
            })}
          </ul>
        </div>
      </div>
    </section>
  )
}
