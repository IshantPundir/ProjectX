import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreGauge } from '@/components/dashboard/reports/ScoreGauge'

describe('ScoreGauge', () => {
  it('renders already-0-10 value as-is (7.5 -> "7.5")', () => {
    render(<ScoreGauge score={7.5} label="Overall" />)
    expect(screen.getByText('7.5')).toBeInTheDocument()
  })
  it('renders "n/a" for a null score (never a zero)', () => {
    render(<ScoreGauge score={null} label="Behavioral" />)
    expect(screen.getByText('n/a')).toBeInTheDocument()
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
  })
  it('exposes an accessible label with the value + context', () => {
    render(<ScoreGauge score={7.0} label="Technical" />)
    expect(screen.getByRole('img', { name: /Technical score 7\.0 out of 10/i })).toBeInTheDocument()
  })
  it('null gauge label says not assessed', () => {
    render(<ScoreGauge score={null} label="Behavioral" />)
    expect(screen.getByRole('img', { name: /Behavioral not assessed/i })).toBeInTheDocument()
  })
})
