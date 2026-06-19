import type { CSSProperties } from 'react'

import { tierTone, TONE_FILL } from './report-format'
import './report.css'

interface ScoreRingProps {
  /** 0–10 domain (the ring's native scale). null → "not assessed". */
  valueTen: number | null
  label: string
  /** Tone override string (validated via tierTone; unknown → neutral). */
  tone?: string
  /** Diameter in px. Default 58. */
  size?: number
}

const R = 42
const C = 2 * Math.PI * R // ≈ 263.9

export function ScoreRing({ valueTen, label, tone, size = 58 }: ScoreRingProps) {
  const assessed = valueTen !== null && valueTen !== undefined
  const resolvedTone = tone
    ? tierTone(tone)
    : assessed
      ? valueTen! >= 6.5 ? 'ok' : valueTen! >= 4.0 ? 'caution' : 'danger'
      : 'neutral'
  const finalOffset = assessed ? C * (1 - valueTen! / 10) : C
  const stroke = size >= 90 ? 9 : 10
  const numFont = size >= 90 ? 22 : 26
  const displayValue = assessed ? valueTen!.toFixed(1) : '—'
  const aria = assessed ? `${label} score ${valueTen!.toFixed(1)} out of 10` : `${label} not assessed`
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
            stroke={TONE_FILL[resolvedTone]} strokeWidth={stroke} strokeLinecap="round"
            strokeDasharray={C} strokeDashoffset={C}
            transform="rotate(-90 50 50)"
            style={ringStyle}
          />
        )}
        {assessed ? (
          <text x="50" y={size >= 90 ? 48 : 58} textAnchor="middle"
            style={{ fontSize: numFont, fontWeight: 800, fill: 'var(--px-fg)' }}>{displayValue}</text>
        ) : (
          <text x="50" y="56" textAnchor="middle"
            style={{ fontSize: 15, fontWeight: 700, fill: 'var(--px-fg-4)' }}>{displayValue}</text>
        )}
        {assessed && size >= 90 && (
          <text x="50" y="64" textAnchor="middle" style={{ fontSize: 7, fill: 'var(--px-fg-4)' }}>/ 10</text>
        )}
      </svg>
      <div className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{label}</div>
    </div>
  )
}
