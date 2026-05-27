import type { CSSProperties } from 'react'

import { scoreToTen, scoreBandTone, TONE_FILL, type Tone } from './report-format'
import './report.css'

interface ScoreGaugeProps {
  /** 0–100 domain (the report's native scale). null → "not assessed". */
  score: number | null
  label: string
  /** Diameter in px. Default 58 (dimension gauges); pass ~118 for Overall. */
  size?: number
  /** Override the tier-derived ring tone (e.g. color Overall by verdict). */
  toneOverride?: Tone
  /** Secondary caption under the gauge (e.g. "cov 0.66 · medium"). */
  caption?: string
}

const R = 42
const C = 2 * Math.PI * R // ≈ 263.9

export function ScoreGauge({ score, label, size = 58, toneOverride, caption }: ScoreGaugeProps) {
  const assessed = score !== null && score !== undefined
  const ten = scoreToTen(score)
  const tone = toneOverride ?? scoreBandTone(score)
  const finalOffset = assessed ? C * (1 - (score as number) / 100) : C
  const stroke = size >= 90 ? 9 : 10
  const numFont = size >= 90 ? 22 : 26 // viewBox units (100×100)
  const aria = assessed ? `${label} score ${ten} out of 10` : `${label} not assessed`
  const ringStyle = { '--px-gauge-final-offset': String(finalOffset) } as CSSProperties

  return (
    <div className="flex flex-col items-center text-center px-gauge">
      <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={aria}>
        <circle
          cx="50" cy="50" r={R} fill="none"
          stroke="var(--px-surface-3)" strokeWidth={stroke}
          {...(!assessed ? { strokeDasharray: '3 4' } : {})}
        />
        {assessed && (
          <circle
            className="px-gauge-ring"
            cx="50" cy="50" r={R} fill="none"
            stroke={TONE_FILL[tone]} strokeWidth={stroke} strokeLinecap="round"
            strokeDasharray={C} strokeDashoffset={C}
            transform="rotate(-90 50 50)"
            style={ringStyle}
          />
        )}
        {assessed ? (
          <text x="50" y={size >= 90 ? 48 : 58} textAnchor="middle"
            style={{ fontSize: numFont, fontWeight: 800, fill: 'var(--px-fg)' }}>{ten}</text>
        ) : (
          <text x="50" y="56" textAnchor="middle"
            style={{ fontSize: 15, fontWeight: 700, fill: 'var(--px-fg-4)' }}>n/a</text>
        )}
        {assessed && size >= 90 && (
          <text x="50" y="64" textAnchor="middle" style={{ fontSize: 7, fill: 'var(--px-fg-4)' }}>/ 10</text>
        )}
      </svg>
      <div className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{label}</div>
      {caption && <div className="text-[9px]" style={{ color: 'var(--px-fg-4)' }}>{caption}</div>}
    </div>
  )
}
