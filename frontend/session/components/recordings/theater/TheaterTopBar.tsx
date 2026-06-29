'use client'

import { BrandLogo } from '@/components/recordings/px'
import type { ReportRead } from '@/components/recordings/api/reports'
import { ScoreGauge } from '../ScoreGauge'
import { TONE_BG, TONE_INK, tierTone, verdictMeta } from '../report-format'
import { GlassBackdrop } from './GlassBackdrop'

// Dimension gauges shown inline in the top bar. `label` doubles as the gauge's
// aria label ("Overall score 8.5 out of 10"); `short` is the tiny visible caption.
const DIMS: { key: string; label: string; short: string }[] = [
  { key: 'overall', label: 'Overall', short: 'Overall' },
  { key: 'technical', label: 'Technical', short: 'Tech' },
  { key: 'behavioral', label: 'Behavioral', short: 'Behav' },
  { key: 'communication', label: 'Comms', short: 'Comms' },
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

function OffScreenGauge({ pct, size = 40 }: { pct: number; size?: number }) {
  const frac = Math.min(1, Math.max(0, pct))
  const tone = offScreenTone(pct)
  const label = `${Math.round(pct * 100)}%`
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={`Off-screen ${label}`}>
      <circle cx="50" cy="50" r={RING_R} fill="none" stroke="var(--px-surface-3)" strokeWidth={10} />
      <circle cx="50" cy="50" r={RING_R} fill="none" stroke={tone.fill} strokeWidth={10}
        strokeLinecap="round" strokeDasharray={RING_C} strokeDashoffset={RING_C * (1 - frac)}
        transform="rotate(-90 50 50)" />
      <text x="50" y="58" textAnchor="middle" style={{ fontSize: 24, fontWeight: 800, fill: 'var(--px-fg)' }}>
        {label}
      </text>
    </svg>
  )
}

function GaugeCell({ children, caption }: { children: React.ReactNode; caption: string }) {
  return (
    <div className="flex flex-col items-center gap-1">
      {children}
      <span className="text-[10px] font-extrabold uppercase tracking-wide" style={{ color: 'var(--px-fg-2)' }}>
        {caption}
      </span>
    </div>
  )
}

// Top chrome for the full-session theater: candidate identity on the left, and a
// glass pill on the right carrying the inline score gauges + verdict + close.
// (The old left ScoreRail is gone — its gauges live here now.)
export function TheaterTopBar({
  report,
  candidateName,
  subtitle,
  integrityCaption,
  integrityPending,
  offScreenPct,
  onClose,
  showClose = true,
}: {
  report: ReportRead
  candidateName: string
  subtitle: string
  /** Full proctoring integrity summary (e.g. "⚠ HIGH RISK · 30% off-screen ·
   *  41 down-glances"), or null until proctoring has finished analyzing. */
  integrityCaption: string | null
  /** Proctoring analysis still in flight — show an "analyzing" hint instead. */
  integrityPending: boolean
  /** Fraction of the session the candidate was off-screen (0–1), or null when
   *  proctoring isn't available. */
  offScreenPct: number | null
  onClose: () => void
  // The public recordings page hides the close ✕ — there is nowhere to close to.
  showClose?: boolean
}) {
  const v = verdictMeta(report.verdict)
  // Only gauges that were actually scored — skip empty dimensions entirely.
  const dims = DIMS.filter(({ key }) => report.scores[key]?.score != null)
  return (
    <div className="pointer-events-none">
      {/* one merged glass bar: identity (left) · gauges (center) · verdict + close (right).
          grid-cols-[1fr_auto_1fr] keeps the center column truly centered in the bar
          regardless of how wide the side clusters are. rounded-2xl matches the glass
          frost clip radius (PANEL_RADIUS = 16 in GlassBackdrop). */}
      <div className="theater-glass pointer-events-auto grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-2xl px-3.5 py-2">
        <GlassBackdrop />

        {/* left: brand + candidate identity */}
        <div className="flex min-w-0 items-center gap-2.5 justify-self-start">
          <BrandLogo height={16} className="theater-watermark flex-none" />
          <div className="h-7 w-px flex-none" style={{ background: 'var(--px-hairline-strong)' }} />
          <div className="min-w-0">
            <div className="truncate text-[13px] font-bold leading-tight" style={{ color: 'var(--px-fg)' }}>
              {candidateName || 'Candidate'}
            </div>
            {subtitle && (
              <div className="truncate text-[10.5px] font-semibold leading-tight" style={{ color: 'var(--px-fg-3)' }}>
                {subtitle}
              </div>
            )}
          </div>
        </div>

        {/* center: inline score gauges */}
        <div className="flex items-center gap-2.5 justify-self-center">
          {dims.map(({ key, label, short }) => {
            const s = report.scores[key]
            return (
              <GaugeCell key={key} caption={short}>
                <ScoreGauge score={s.score} label={label} size={40} hideLabel
                  toneOverride={key === 'overall' ? v.tone : tierTone(s.tone)} />
              </GaugeCell>
            )
          })}
          {offScreenPct != null && (
            <GaugeCell caption="Off">
              <OffScreenGauge pct={offScreenPct} />
            </GaugeCell>
          )}
        </div>

        {/* right: integrity summary + verdict + close */}
        <div className="flex min-w-0 items-center gap-2 justify-self-end">
          {integrityPending ? (
            <span className="theater-tl-pending whitespace-nowrap">
              <span className="theater-spinner-sm" aria-hidden="true" />
              Analyzing integrity…
            </span>
          ) : integrityCaption ? (
            <span className="theater-tl-integrity max-w-full truncate">{integrityCaption}</span>
          ) : null}
          <span className="whitespace-nowrap rounded-full px-2.5 py-0.5 text-[10.5px] font-bold"
            style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}>
            {v.label}
          </span>
          {showClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="grid h-6 w-6 flex-none place-items-center rounded-full border text-[12px]"
              style={{ borderColor: 'var(--px-hairline-strong)', color: 'var(--px-fg-3)' }}
            >
              ✕
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
