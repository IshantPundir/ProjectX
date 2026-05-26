import type { SummaryOut } from '@/lib/api/reports'

export function ReportSummary({ summary }: { summary: SummaryOut }) {
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Summary</h2>
      <p className="mb-2 text-[12.5px] font-semibold" style={{ color: 'var(--px-fg)' }}>{summary.headline}</p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase" style={{ color: 'var(--px-ok)' }}>Strengths</div>
          <ul className="list-disc pl-4 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>
            {summary.strengths.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase" style={{ color: 'var(--px-danger)' }}>Gaps</div>
          <ul className="list-disc pl-4 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>
            {summary.gaps.map((g, i) => <li key={i}>{g}</li>)}
          </ul>
        </div>
      </div>
      {summary.rationale && (
        <p className="mt-2 whitespace-pre-wrap text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{summary.rationale}</p>
      )}
    </section>
  )
}
