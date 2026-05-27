import type { DecisionOut } from '@/lib/api/reports'

export function WhyContrast({ decision }: { decision: DecisionOut }) {
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Why this verdict">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Why this verdict</h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-lg p-3" style={{ background: 'var(--px-ok-bg)' }}>
          <div className="mb-1 text-[11.5px] font-bold" style={{ color: 'var(--px-ok)' }}>{decision.why_positive.title}</div>
          <p className="text-[11.5px]" style={{ color: 'var(--px-fg-2)' }}>{decision.why_positive.body}</p>
        </div>
        <div className="rounded-lg p-3" style={{ background: 'var(--px-caution-bg)' }}>
          <div className="mb-1 text-[11.5px] font-bold" style={{ color: 'var(--px-caution)' }}>{decision.why_negative.title}</div>
          <p className="text-[11.5px]" style={{ color: 'var(--px-fg-2)' }}>{decision.why_negative.body}</p>
        </div>
      </div>
    </section>
  )
}
