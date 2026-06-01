'use client'

import type { ReportRead } from '@/lib/api/reports'
import { ScoreGauge } from '../ScoreGauge'
import { tierTone, verdictMeta } from '../report-format'
import { GlassBackdrop } from './GlassBackdrop'

const DIMS: { key: string; label: string }[] = [
  { key: 'overall', label: 'Overall' },
  { key: 'technical', label: 'Technical' },
  { key: 'behavioral', label: 'Behavioral' },
  { key: 'communication', label: 'Comms' },
]

const RING_R = 42
const RING_C = 2 * Math.PI * RING_R

// More time off-screen is worse, so the ring fills as the % climbs and warms
// from green → amber → red. The number shown is the real off-screen percentage.
function offScreenTone(pct: number): { fill: string; ink: string } {
  if (pct < 0.15) return { fill: 'var(--px-ok-fill)', ink: 'var(--px-ok)' }
  if (pct < 0.35) return { fill: 'var(--px-caution-fill)', ink: 'var(--px-caution)' }
  return { fill: 'var(--px-danger-fill)', ink: 'var(--px-danger)' }
}

function OffScreenGauge({ pct, size = 42 }: { pct: number; size?: number }) {
  const frac = Math.min(1, Math.max(0, pct))
  const tone = offScreenTone(pct)
  const label = `${Math.round(pct * 100)}%`
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={`Off-screen ${label}`}>
      <circle cx="50" cy="50" r={RING_R} fill="none" stroke="var(--px-surface-3)" strokeWidth={10} />
      <circle cx="50" cy="50" r={RING_R} fill="none" stroke={tone.fill} strokeWidth={10}
        strokeLinecap="round" strokeDasharray={RING_C} strokeDashoffset={RING_C * (1 - frac)}
        transform="rotate(-90 50 50)" />
      <text x="50" y="58" textAnchor="middle" style={{ fontSize: 22, fontWeight: 800, fill: 'var(--px-fg)' }}>
        {label}
      </text>
    </svg>
  )
}

export function ScoreRail({
  report,
  candidateName,
  subtitle,
  offScreenPct,
}: {
  report: ReportRead
  candidateName: string
  subtitle: string
  /** Fraction of the session the candidate was off-screen (0–1), or null when
   *  proctoring isn't available. */
  offScreenPct: number | null
}) {
  const v = verdictMeta(report.verdict)
  // Only gauges that were actually scored — skip empty dimensions entirely.
  const dims = DIMS.filter(({ key }) => report.scores[key]?.score != null)
  return (
    <div className="theater-glass flex max-h-full w-[212px] flex-col rounded-3xl p-4">
      <GlassBackdrop />
      {/* candidate identity — co-located with their assessment */}
      <div className="min-w-0">
        <div className="truncate text-[15px] font-bold" style={{ color: 'var(--px-fg)' }}>
          {candidateName || 'Candidate'}
        </div>
        {subtitle && (
          <div className="truncate text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{subtitle}</div>
        )}
      </div>
      <div className="my-3 h-px w-full flex-none" style={{ background: 'var(--px-hairline-strong)' }} />
      <div className="theater-scroll flex min-h-0 flex-col gap-2.5 overflow-y-auto">
        {dims.map(({ key, label }) => {
          const s = report.scores[key]
          return (
            <div key={key} className="flex items-center gap-3">
              <ScoreGauge score={s.score} label={label} size={42} hideLabel
                toneOverride={key === 'overall' ? v.tone : tierTone(s.tone)} />
              <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg-2)' }}>{label}</span>
            </div>
          )
        })}
        {offScreenPct != null && (
          <div className="mt-0.5 flex items-center gap-3 border-t pt-2.5" style={{ borderColor: 'var(--px-hairline)' }}>
            <OffScreenGauge pct={offScreenPct} />
            <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg-2)' }}>Off-screen</span>
          </div>
        )}
      </div>
    </div>
  )
}
