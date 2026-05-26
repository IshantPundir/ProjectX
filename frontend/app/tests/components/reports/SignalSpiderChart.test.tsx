import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { SignalSpiderChart, radarPolygonPoints } from '@/components/dashboard/reports/SignalSpiderChart'
import type { SignalScorecard } from '@/lib/api/reports'

const sig = (value: string, state: SignalScorecard['state'], score: number | null): SignalScorecard => ({
  value, type: 'competency', weight: 2, knockout: false, state, score, opportunity: 'full', evidence: [], covered_by: [],
})

describe('radarPolygonPoints', () => {
  it('maps n values to n "x,y" pairs scaled by value/10', () => {
    const pts = radarPolygonPoints([10, 0], 100, 100, 80).split(' ')
    expect(pts).toHaveLength(2)
    // first axis points straight up at full radius (value 10): y = 100 - 80.
    // Coordinates are toFixed(1)-formatted, so expect "100.0,20.0".
    expect(pts[0]).toBe('100.0,20.0')
  })
})

describe('SignalSpiderChart', () => {
  it('returns null with fewer than 3 assessed signals', () => {
    const { container } = render(<SignalSpiderChart signals={[sig('A', 'meets_bar', 70), sig('B', 'below_bar', 30)]} />)
    expect(container.firstChild).toBeNull()
  })
  it('plots only assessed signals (excludes not_assessed)', () => {
    const signals = [
      sig('A', 'meets_bar', 70), sig('B', 'excellent', 100),
      sig('C', 'below_bar', 30), sig('D', 'not_assessed', null),
    ]
    const { container } = render(<SignalSpiderChart signals={signals} />)
    // 3 assessed → polygon present with 3 vertices
    const poly = container.querySelector('polygon.px-radar-data') as SVGPolygonElement | null
    expect(poly).not.toBeNull()
    expect(poly!.getAttribute('points')!.trim().split(' ')).toHaveLength(3)
  })
})
