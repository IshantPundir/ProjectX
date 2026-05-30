'use client'

import type { ReportRead, RiskBand } from '@/lib/api/reports'
import { ScoreGauge } from '../ScoreGauge'
import { TONE_BG, TONE_INK, tierTone, verdictMeta } from '../report-format'

export function TheaterTopBar({
  report,
  candidateName,
  subtitle,
  riskBand,
  onClose,
}: {
  report: ReportRead
  candidateName: string
  subtitle: string
  riskBand: RiskBand | null
  onClose: () => void
}) {
  const v = verdictMeta(report.verdict)
  const dims: { key: string; label: string }[] = [
    { key: 'overall', label: 'Overall' },
    { key: 'technical', label: 'Technical' },
    { key: 'communication', label: 'Comms' },
  ]
  return (
    <div className="theater-glass m-3 mb-0 flex items-center gap-4 rounded-2xl px-4 py-2">
      <div className="min-w-0">
        <div className="truncate text-[13.5px] font-bold" style={{ color: 'var(--px-fg)' }}>
          {candidateName || 'Candidate'}
        </div>
        {subtitle && <div className="truncate text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{subtitle}</div>}
      </div>
      <div className="h-8 w-px" style={{ background: 'var(--px-hairline)' }} />
      <div className="flex items-center gap-3">
        {dims.map((d) => {
          const s = report.scores[d.key]
          if (!s) return null
          return <ScoreGauge key={d.key} score={s.score} label={d.label} size={40}
            toneOverride={d.key === 'overall' ? v.tone : tierTone(s.tone)} />
        })}
      </div>
      <div className="flex-1" />
      {riskBand === 'high' && (
        <span className="whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-bold"
          style={{ background: TONE_BG.danger, color: TONE_INK.danger }}>
          ⚠ High integrity risk
        </span>
      )}
      <span className="whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-bold"
        style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}>
        {v.label}
      </span>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="grid h-7 w-7 place-items-center rounded-lg border text-[13px]"
        style={{ borderColor: 'var(--px-hairline)', color: 'var(--px-fg-3)' }}
      >
        ✕
      </button>
    </div>
  )
}
