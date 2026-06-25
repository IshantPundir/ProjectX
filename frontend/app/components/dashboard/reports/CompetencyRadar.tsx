'use client'

import { useId } from 'react'
import type { SignalAssessmentOut } from '@/lib/api/reports'

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

/** Convert polar (angle in degrees, radius 0–1) to SVG cartesian coords. */
function polar(cx: number, cy: number, r: number, angleDeg: number): [number, number] {
  const rad = (angleDeg * Math.PI) / 180
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
}

/** Build the `points` string for an SVG polygon over N axes at the given radius factor. */
function buildPolygon(
  cx: number,
  cy: number,
  maxR: number,
  n: number,
  factor: number,
): string {
  return Array.from({ length: n }, (_, i) => {
    const angle = -90 + (i * 360) / n
    const [x, y] = polar(cx, cy, maxR * factor, angle)
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
}

/** Build the `points` string for the data polygon from per-axis score factors. */
function buildDataPolygon(
  cx: number,
  cy: number,
  maxR: number,
  factors: number[],
): string {
  return factors
    .map((f, i) => {
      const angle = -90 + (i * 360) / factors.length
      const [x, y] = polar(cx, cy, maxR * f, angle)
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

// ---------------------------------------------------------------------------
// Label truncation
// ---------------------------------------------------------------------------

const MAX_LABEL_CHARS = 16

/**
 * Truncate a signal name to ≤ MAX_LABEL_CHARS characters, breaking on a word
 * boundary and appending "…". If the name is already within the limit, return
 * it unchanged.
 */
function truncateLabel(name: string): string {
  if (name.length <= MAX_LABEL_CHARS) return name

  // Walk backwards from position MAX_LABEL_CHARS to find the last word boundary
  let cut = MAX_LABEL_CHARS
  while (cut > 0 && name[cut] !== ' ') cut--

  // If no space found at all, hard-cut at MAX_LABEL_CHARS
  if (cut === 0) return name.slice(0, MAX_LABEL_CHARS) + '…'

  return name.slice(0, cut) + '…'
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  assessments: SignalAssessmentOut[]
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CompetencyRadar({ assessments }: Props): React.ReactElement {
  const gradId = useId()

  // Filter + sort + cap — matches the brief algorithm exactly
  const assessed = assessments
    .filter((a) => a.provenance !== 'not_reached' && a.score != null)
    .sort((a, b) => {
      if (b.weight !== a.weight) return b.weight - a.weight
      return a.signal.localeCompare(b.signal)
    })
    .slice(0, 8)

  // ---- Bar fallback (fewer than 3 assessed signals) ----
  if (assessed.length < 3) {
    return (
      <div role="img" aria-label="Competency scores" className="flex flex-col gap-2 py-2">
        {assessed.map((a) => (
          <div key={a.signal} className="grid grid-cols-[1fr_auto] items-center gap-2">
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-[var(--px-fg-2)] uppercase tracking-wide truncate">
                {a.signal}
              </span>
              <div className="h-2 rounded-full bg-[var(--px-surface-3)] overflow-hidden">
                <div
                  className="h-full rounded-full bg-[var(--px-accent)]"
                  style={{ width: `${(a.score ?? 0) * 10}%` }}
                />
              </div>
            </div>
            <span className="text-sm font-bold text-[var(--px-fg-1)] tabular-nums w-6 text-right">
              {a.score}
            </span>
          </div>
        ))}
      </div>
    )
  }

  // ---- Radar SVG ----
  const n = assessed.length
  const factors = assessed.map((a) => (a.score ?? 0) / 10)

  // SVG viewport — wider to give labels room on left/right
  const VW = 360
  const VH = 300
  const cx = VW / 2         // 180
  const cy = VH / 2 - 0.5  // ~149.5
  const maxR = 95           // distance from center to outer polygon vertex

  // Text clearance around the radar — extra pixels beyond maxR where labels live
  const labelPad = 24

  return (
    <svg
      viewBox={`0 0 ${VW} ${VH}`}
      className="px-radar w-full h-full"
      aria-label="Competency radar"
      role="img"
    >
      <defs>
        <radialGradient id={gradId} cx="50%" cy="42%" r="60%">
          <stop offset="0%" stopColor="var(--px-accent)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--px-accent)" stopOpacity="0.06" />
        </radialGradient>
      </defs>

      {/* Outer grid polygon (value = 1.0) */}
      <polygon
        points={buildPolygon(cx, cy, maxR, n, 1)}
        fill="none"
        stroke="#e3e5ec"
        strokeWidth="1"
      />

      {/* Mid grid polygon (value = 0.5) */}
      <polygon
        points={buildPolygon(cx, cy, maxR, n, 0.5)}
        fill="none"
        stroke="#eceef3"
        strokeWidth="1"
      />

      {/* Axis lines from center to outer vertex */}
      <g stroke="#eceef3" strokeWidth="1">
        {Array.from({ length: n }, (_, i) => {
          const angle = -90 + (i * 360) / n
          const [x, y] = polar(cx, cy, maxR, angle)
          return (
            <line
              key={i}
              x1={cx.toFixed(2)}
              y1={cy.toFixed(2)}
              x2={x.toFixed(2)}
              y2={y.toFixed(2)}
            />
          )
        })}
      </g>

      {/* Data polygon */}
      <polygon
        className="px-radar-data"
        points={buildDataPolygon(cx, cy, maxR, factors)}
        fill={`url(#${gradId})`}
        stroke="var(--px-accent)"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />

      {/* Data vertex dots */}
      <g fill="var(--px-accent)">
        {factors.map((f, i) => {
          const angle = -90 + (i * 360) / n
          const [x, y] = polar(cx, cy, maxR * f, angle)
          return <circle key={i} cx={x.toFixed(2)} cy={y.toFixed(2)} r="3.5" />
        })}
      </g>

      {/* Axis labels — truncated with full name in <title> for tooltip */}
      {assessed.map((a, i) => {
        const angle = -90 + (i * 360) / n
        const [lx, ly] = polar(cx, cy, maxR + labelPad, angle)

        // Derive text-anchor from the horizontal position relative to center
        const xDiff = lx - cx
        const textAnchor =
          xDiff > 6 ? 'start' : xDiff < -6 ? 'end' : 'middle'

        const displayLabel = truncateLabel(a.signal)
        const isTruncated = displayLabel !== a.signal

        return (
          <text
            key={a.signal}
            x={lx.toFixed(2)}
            y={ly.toFixed(2)}
            textAnchor={textAnchor}
            dominantBaseline="middle"
            fontSize="11"
            fontWeight="700"
            fill="var(--px-fg-3)"
          >
            {isTruncated && <title>{a.signal}</title>}
            {displayLabel}
          </text>
        )
      })}
    </svg>
  )
}
