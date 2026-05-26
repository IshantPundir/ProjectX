import type { DimensionScoreOut, ReportRead } from '@/lib/api/reports'
import { ScoreGauge } from './ScoreGauge'
import { VerdictBand } from './VerdictBand'
import { confidenceLabel, verdictMeta } from './report-format'

const DIMENSION_ORDER: { key: string; label: string }[] = [
  { key: 'technical', label: 'Technical' },
  { key: 'behavioral', label: 'Behavioral' },
  { key: 'communication', label: 'Communication' },
]

function dimCaption(d: DimensionScoreOut | undefined): string | undefined {
  if (!d) return undefined
  if (d.score === null) return 'not assessed'
  return `cov ${d.coverage.toFixed(2)} · ${confidenceLabel(d.confidence).toLowerCase()}`
}

export function AiRecommendationCard({ report }: { report: ReportRead }) {
  const tone = verdictMeta(report.verdict).tone
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="AI recommendation">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>
        AI recommendation
      </h2>
      <VerdictBand verdict={report.verdict} />
      <p className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>{report.verdict_reason}</p>

      <div className="my-3 flex justify-center">
        <ScoreGauge score={report.overall_score} label="Overall" size={118} toneOverride={tone} />
      </div>

      <div className="grid grid-cols-3 gap-1.5">
        {DIMENSION_ORDER.map(({ key, label }) => {
          const d = report.dimension_scores[key]
          return <ScoreGauge key={key} score={d?.score ?? null} label={label} size={58} caption={dimCaption(d)} />
        })}
      </div>

      <div className="mt-3 flex gap-2 border-t pt-2.5" style={{ borderColor: 'var(--px-hairline)' }}>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Coverage</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{report.overall_coverage.toFixed(2)}</div>
        </div>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Confidence</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{confidenceLabel(report.overall_confidence)}</div>
        </div>
      </div>
    </section>
  )
}
