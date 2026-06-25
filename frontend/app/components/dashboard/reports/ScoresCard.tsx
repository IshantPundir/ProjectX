import type { ReportRead, ScoreOut } from '@/lib/api/reports'
import { CompetencyRadar } from './CompetencyRadar'
import { ScoreGauge } from './ScoreGauge'
import { VerdictBand } from './VerdictBand'
import { confidenceLabel, formatTen, tierTone, verdictMeta } from './report-format'

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
    <section className="rounded-xl border bg-white p-4 px-card" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Scores">
      <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>AI recommendation</h2>
      <VerdictBand verdict={report.verdict} />
      <p className="mt-1 text-[12.5px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>{report.decision.headline}</p>

      <div className="my-3 flex flex-col items-center">
        <ScoreGauge score={overall?.score ?? null} label="Overall" size={118} toneOverride={verdictTone} />
        {overall?.session_score != null && (overall.holistic_delta ?? 0) !== 0 && (
          <div className="mt-1 text-[10px]" style={{ color: 'var(--px-fg-4)' }}
               title="Deterministic session score, plus a bounded holistic adjustment. See methodology.">
            Session score {formatTen(overall.session_score)}
            {' · holistic '}{(overall.holistic_delta as number) > 0 ? '+' : ''}
            {(overall.holistic_delta as number).toFixed(1)}
          </div>
        )}
      </div>

      {dims.length > 0 && (
        <div className="flex flex-wrap justify-center gap-x-5 gap-y-3">
          {dims.map(({ key, label }) => {
            const d = report.scores[key]
            return <ScoreGauge key={key} score={d?.score ?? null} label={label} size={88}
              toneOverride={d ? tierTone(d.tone) : undefined} caption={caption(d)} />
          })}
        </div>
      )}

      {report.signal_assessments.length > 0 && (
        <div className="mt-3 border-t pt-3" style={{ borderColor: 'var(--px-hairline)' }}>
          <div className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
            Competency breakdown
          </div>
          <CompetencyRadar assessments={report.signal_assessments} />
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
