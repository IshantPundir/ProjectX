import type { ReportRead, ScoreOut } from '@/lib/api/reports'
import { ScoreGauge } from './ScoreGauge'
import { VerdictBand } from './VerdictBand'
import { confidenceLabel, tierTone, verdictMeta } from './report-format'

const DIMS: { key: string; label: string }[] = [
  { key: 'technical', label: 'Technical' },
  { key: 'behavioral', label: 'Behavioral' },
  { key: 'communication', label: 'Communication' },
]

function caption(s: ScoreOut | undefined): string | undefined {
  if (!s) return undefined
  if (s.score === null) return 'not assessed'
  return `cov ${s.coverage.toFixed(2)} · ${confidenceLabel(s.confidence).toLowerCase()}`
}

export function ScoresCard({ report }: { report: ReportRead }) {
  const overall = report.scores.overall
  const verdictTone = verdictMeta(report.verdict).tone
  const dims = DIMS.filter(({ key }) => report.scores[key]?.score != null)
  return (
    <section className="rounded-xl border bg-white p-4" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Scores">
      <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>AI recommendation</h2>
      <VerdictBand verdict={report.verdict} />
      <p className="mt-1 text-[12.5px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>{report.decision.headline}</p>

      <div className="my-3 flex justify-center">
        <ScoreGauge score={overall?.score ?? null} label="Overall" size={118} toneOverride={verdictTone} />
      </div>

      {dims.length > 0 && (
        <div className="flex flex-wrap justify-center gap-3">
          {dims.map(({ key, label }) => {
            const d = report.scores[key]
            return <ScoreGauge key={key} score={d?.score ?? null} label={label} size={58}
              toneOverride={d ? tierTone(d.tone) : undefined} caption={caption(d)} />
          })}
        </div>
      )}

      <div className="mt-3 flex gap-2 border-t pt-2.5" style={{ borderColor: 'var(--px-hairline)' }}>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Coverage</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{(overall?.coverage ?? 0).toFixed(2)}</div>
        </div>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Confidence</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{confidenceLabel(overall?.confidence ?? 'low')}</div>
        </div>
      </div>
    </section>
  )
}
