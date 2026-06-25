import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreBar } from '@/components/dashboard/reports/ScoreBar'

describe('ScoreBar', () => {
  it('renders value, cleared-bar a11y label, and ✓ glyph when score clears 6.5', () => {
    render(<ScoreBar score={7.2} label="Technical" />)
    expect(screen.getByText('7.2')).toBeInTheDocument()
    const bar = screen.getByRole('img', { name: /Technical score 7.2 out of 10, above hiring bar/ })
    expect(bar).toBeInTheDocument()
    expect(bar.textContent).toContain('✓')
  })

  it('shows ⚠ and below-bar label when score is under 6.5', () => {
    render(<ScoreBar score={4.9} label="Behavioral" />)
    const bar = screen.getByRole('img', { name: /Behavioral score 4.9 out of 10, below hiring bar/ })
    expect(bar.textContent).toContain('⚠')
  })

  it('exposes a plain-language status tooltip explaining the glyph', () => {
    render(<ScoreBar score={4.9} label="Behavioral" />)
    const tip = screen.getByRole('tooltip')
    expect(tip).toHaveTextContent('Below the hiring bar — scored 4.9 of 10 (bar at 6.5)')
  })

  it('appends the hint to the status tooltip and the a11y label when provided', () => {
    render(<ScoreBar score={7.2} label="Domain" hint="dedicated: thin; +1 cross-credit → solid" />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('dedicated: thin; +1 cross-credit → solid')
    expect(screen.getByRole('img', { name: /dedicated: thin/ })).toBeInTheDocument()
  })

  it('sets the fill width to score/10 as a percentage', () => {
    const { container } = render(<ScoreBar score={6.0} label="Comms" />)
    const fill = container.querySelector('.px-scorebar-fill') as HTMLElement
    expect(fill).toBeTruthy()
    expect(fill.style.getPropertyValue('--px-bar-fill')).toBe('60%')
  })

  it('renders threshold band zones for a row bar', () => {
    const { container } = render(<ScoreBar score={5.0} label="X" variant="row" />)
    expect(container.querySelector('.px-band-reject')).toBeTruthy()
    expect(container.querySelector('.px-band-borderline')).toBeTruthy()
    expect(container.querySelector('.px-band-advance')).toBeTruthy()
    expect(container.querySelector('.px-bar-marker')).toBeTruthy()
  })

  it('compact variant omits the full band zones', () => {
    const { container } = render(<ScoreBar score={5.0} label="X" variant="compact" />)
    expect(container.querySelector('.px-band-reject')).toBeNull()
  })

  it('renders a must-have ★ marker when mustHave is set', () => {
    const { container } = render(<ScoreBar score={8} label="Domain" mustHave />)
    expect(container.textContent).toContain('★')
  })

  it('renders a not-assessed state for null score', () => {
    render(<ScoreBar score={null} label="Ownership" />)
    expect(screen.getByRole('img', { name: /Ownership not assessed/ })).toBeInTheDocument()
    expect(screen.getByText('n/a')).toBeInTheDocument()
  })

  it('renders a not-reached state when notReached is set', () => {
    render(<ScoreBar score={null} label="Stakeholder" notReached />)
    expect(screen.getByRole('img', { name: /Stakeholder not reached/ })).toBeInTheDocument()
  })
})
