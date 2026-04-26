import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'

import {
  UNIT_TYPE_STYLE,
  getUnitTypeStyle,
  Glyph,
  type UnitType,
} from '@/components/dashboard/org-units/unit-type-style'

describe('UNIT_TYPE_STYLE', () => {
  it('has an entry for each of the five unit types', () => {
    const expected: UnitType[] = [
      'company',
      'client_account',
      'region',
      'division',
      'team',
    ]
    for (const t of expected) {
      expect(UNIT_TYPE_STYLE[t]).toBeDefined()
      expect(UNIT_TYPE_STYLE[t].stripVar).toMatch(/^var\(--/)
      expect(UNIT_TYPE_STYLE[t].bgVar).toMatch(/^var\(--/)
      expect(UNIT_TYPE_STYLE[t].lineVar).toMatch(/^var\(--/)
    }
  })

  it('maps each type to a unique glyph kind', () => {
    const glyphs = Object.values(UNIT_TYPE_STYLE).map((s) => s.glyph)
    expect(new Set(glyphs).size).toBe(glyphs.length)
  })
})

describe('getUnitTypeStyle', () => {
  it('returns the typed style for a known unit type', () => {
    expect(getUnitTypeStyle('region')).toBe(UNIT_TYPE_STYLE.region)
  })

  it('falls back to team style and warns once per unknown type', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // Use a distinct type name per test run so the module-level dedup
    // set doesn't suppress the warn from prior tests.
    const unknown = `not_a_real_type_${Math.random().toString(36).slice(2)}`
    const result1 = getUnitTypeStyle(unknown)
    const result2 = getUnitTypeStyle(unknown)
    expect(result1).toBe(UNIT_TYPE_STYLE.team)
    expect(result2).toBe(UNIT_TYPE_STYLE.team)
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('unknown unit_type'),
    )
    warn.mockRestore()
  })
})

describe('Glyph', () => {
  it('renders an SVG with the requested fill color', () => {
    const { container } = render(<Glyph kind="circle" color="#abc123" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(svg?.getAttribute('aria-hidden')).toBe('true')
    expect(container.innerHTML).toContain('#abc123')
  })

  it('renders different shape elements per glyph kind', () => {
    const { container: c1 } = render(<Glyph kind="square" color="#000" />)
    expect(c1.querySelector('rect')).toBeInTheDocument()

    const { container: c2 } = render(<Glyph kind="circle" color="#000" />)
    expect(c2.querySelector('circle')).toBeInTheDocument()

    const { container: c3 } = render(<Glyph kind="diamond" color="#000" />)
    expect(c3.querySelector('polygon')).toBeInTheDocument()

    const { container: c4 } = render(<Glyph kind="hexagon" color="#000" />)
    expect(c4.querySelector('polygon')).toBeInTheDocument()

    const { container: c5 } = render(<Glyph kind="pill" color="#000" />)
    expect(c5.querySelector('rect')).toBeInTheDocument()
  })
})
