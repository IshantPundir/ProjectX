import type { CSSProperties } from 'react'

import { ADVANCE_BAND, bandZones, formatTen, scoreBandTone, TONE_FILL, type Tone } from './report-format'
import './report.css'

export type ScoreBarVariant = 'hero' | 'row' | 'compact'

interface ScoreBarProps {
  /** 0–10 score; null → "not assessed". */
  score: number | null
  label: string
  variant?: ScoreBarVariant
  /** Override the tier-derived fill tone (e.g. color Overall by verdict). */
  toneOverride?: Tone
  /** Render a ★ must-have marker before the label. */
  mustHave?: boolean
  /** Signal was never reached in the interview → muted dashed track. */
  notReached?: boolean
  /** Render the faint reject/borderline/advance zones (default: true unless compact). */
  showBands?: boolean
  /** Secondary line under the bar. */
  caption?: string
}

export function ScoreBar({
  score, label, variant = 'row', toneOverride,
  mustHave = false, notReached = false, showBands, caption,
}: ScoreBarProps): React.ReactElement {
  const assessed = score !== null && score !== undefined && !notReached
  const ten = formatTen(score)
  const tone = toneOverride ?? scoreBandTone(score)
  const cleared = assessed && (score as number) >= ADVANCE_BAND
  const { rejectPct, advancePct } = bandZones()
  const fillPct = assessed ? Math.max(0, Math.min(100, ((score as number) / 10) * 100)) : 0
  const bands = (showBands ?? variant !== 'compact') && assessed

  const stateLabel = !assessed
    ? notReached ? 'not reached' : 'not assessed'
    : cleared ? 'above hiring bar' : 'below hiring bar'
  const aria = assessed
    ? `${label} score ${ten} out of 10, ${stateLabel}`
    : `${label} ${stateLabel}`

  const trackH = variant === 'hero' ? 'h-3.5' : variant === 'compact' ? 'h-2' : 'h-2.5'
  const labelSize = variant === 'hero' ? 'text-[13px]' : 'text-[12px]'
  const valueSize = variant === 'hero' ? 'text-[20px]' : 'text-[13px]'

  return (
    <div role="img" aria-label={aria} className="px-scorebar w-full">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className={`${labelSize} truncate font-semibold`} style={{ color: 'var(--px-fg-2)' }}>
          {mustHave && <span aria-hidden className="mr-1" style={{ color: 'var(--px-accent)' }}>★</span>}
          {label}
        </span>
        <span className={`${valueSize} whitespace-nowrap font-bold tabular-nums`}
          style={{ color: assessed ? 'var(--px-fg)' : 'var(--px-fg-4)' }}>
          {assessed ? ten : 'n/a'}
          {assessed && (
            <span aria-hidden className="ml-1" style={{ color: cleared ? 'var(--px-ok)' : 'var(--px-caution)' }}>
              {cleared ? '✓' : '⚠'}
            </span>
          )}
        </span>
      </div>

      <div className={`relative ${trackH} w-full overflow-hidden rounded-full`} style={{ background: 'var(--px-surface-3)' }}>
        {bands && (
          <>
            <div className="px-band-reject absolute inset-y-0 left-0" style={{ width: `${rejectPct}%` }} aria-hidden />
            <div className="px-band-borderline absolute inset-y-0" style={{ left: `${rejectPct}%`, width: `${advancePct - rejectPct}%` }} aria-hidden />
            <div className="px-band-advance absolute inset-y-0" style={{ left: `${advancePct}%`, right: 0 }} aria-hidden />
          </>
        )}
        {assessed ? (
          <div className="px-scorebar-fill absolute inset-y-0 left-0 rounded-full"
            style={{ '--px-bar-fill': `${fillPct}%`, background: TONE_FILL[tone] } as CSSProperties} aria-hidden />
        ) : (
          <div className="absolute inset-0 rounded-full" style={{ border: '1px dashed var(--px-fg-4)', opacity: 0.5 }} aria-hidden />
        )}
        {bands && (
          <div className="px-bar-marker absolute inset-y-0" style={{ left: `${advancePct}%` }} aria-hidden />
        )}
      </div>

      {caption && <div className="mt-0.5 text-[10px]" style={{ color: 'var(--px-fg-4)' }}>{caption}</div>}
    </div>
  )
}
