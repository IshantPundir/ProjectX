import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { VerdictBand, VerdictChip } from '@/components/dashboard/reports/VerdictBand'

describe('VerdictBand', () => {
  it('renders the verdict label', () => {
    render(<VerdictBand verdict="reject" />)
    expect(screen.getByText('Reject')).toBeInTheDocument()
  })
  it('borderline uses the human (lavender) ink token', () => {
    render(<VerdictBand verdict="borderline" />)
    const el = screen.getByText('Borderline')
    expect(el).toHaveStyle({ color: 'var(--px-human)' })
  })
})

describe('VerdictChip', () => {
  it('renders a compact uppercase chip', () => {
    render(<VerdictChip verdict="advance" />)
    expect(screen.getByText('ADVANCE')).toBeInTheDocument()
  })
})
