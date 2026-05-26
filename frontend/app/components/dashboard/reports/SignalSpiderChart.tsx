'use client'

import type { SignalScorecard } from '@/lib/api/reports'
import './report.css'

const CX = 100
const CY = 100
const RADIUS = 80

/** angle for axis i of n, starting straight up (-90°), clockwise. */
function axisAngle(i: number, n: number): number {
  return (-90 + (360 / n) * i) * (Math.PI / 180)
}

/** values are 0–10; returns "x,y x,y ..." scaled by value/10. */
export function radarPolygonPoints(values: number[], cx: number, cy: number, radius: number): string {
  const n = values.length
  return values
    .map((v, i) => {
      const a = axisAngle(i, n)
      const r = radius * (Math.max(0, Math.min(10, v)) / 10)
      return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`
    })
    .join(' ')
}

function axisEndpoints(n: number): { x: number; y: number }[] {
  return Array.from({ length: n }, (_, i) => {
    const a = axisAngle(i, n)
    return { x: CX + RADIUS * Math.cos(a), y: CY + RADIUS * Math.sin(a) }
  })
}

export function SignalSpiderChart({ signals }: { signals: SignalScorecard[] }) {
  const assessed = signals.filter((s) => s.state !== 'not_assessed' && s.score !== null)
  if (assessed.length < 3) return null

  const n = assessed.length
  const ends = axisEndpoints(n)
  const outer = ends.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const inner = ends
    .map((p) => `${(CX + 0.5 * (p.x - CX)).toFixed(1)},${(CY + 0.5 * (p.y - CY)).toFixed(1)}`)
    .join(' ')
  const data = radarPolygonPoints(assessed.map((s) => (s.score as number) / 10), CX, CY, RADIUS)

  return (
    <svg viewBox="0 0 200 200" width="200" height="200" role="img" aria-label="Signal score profile">
      <polygon points={outer} fill="none" stroke="var(--px-hairline)" strokeWidth="0.8" />
      <polygon points={inner} fill="none" stroke="var(--px-hairline)" strokeWidth="0.8" />
      {ends.map((p, i) => (
        <line key={i} x1={CX} y1={CY} x2={p.x} y2={p.y} stroke="var(--px-hairline-strong)" strokeWidth="0.5" />
      ))}
      <polygon
        className="px-radar-data"
        points={data}
        fill="var(--px-accent-tint)"
        stroke="var(--px-accent)"
        strokeWidth="1.6"
      />
      {assessed.map((s, i) => {
        const a = axisAngle(i, n)
        const rr = RADIUS * ((s.score as number) / 100) // score is 0–100; full radius at 100
        return <circle key={s.value} cx={CX + rr * Math.cos(a)} cy={CY + rr * Math.sin(a)} r="2" fill="var(--px-accent)" />
      })}
      {ends.map((p, i) => {
        const a = axisAngle(i, n)
        const lx = CX + (RADIUS + 12) * Math.cos(a)
        const ly = CY + (RADIUS + 12) * Math.sin(a)
        const anchor = Math.abs(Math.cos(a)) < 0.3 ? 'middle' : Math.cos(a) > 0 ? 'start' : 'end'
        return (
          <text key={`l${i}`} x={lx} y={ly} textAnchor={anchor as 'start' | 'middle' | 'end'}
            style={{ fontSize: 6.5, fill: 'var(--px-fg-3)' }}>
            {assessed[i].value.length > 14 ? `${assessed[i].value.slice(0, 13)}…` : assessed[i].value}
          </text>
        )
      })}
    </svg>
  )
}
