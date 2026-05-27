import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StrengthsConcerns } from '@/components/dashboard/reports/StrengthsConcerns'
import { makeReport } from './_fixture'

describe('StrengthsConcerns', () => {
  it('renders strengths, concerns, counts, and a severity chip', () => {
    const r = makeReport()
    render(<StrengthsConcerns strengths={r.strengths} concerns={r.concerns} />)
    expect(screen.getByText('Meets the experience bar')).toBeInTheDocument()
    expect(screen.getByText('No core skill reached the bar')).toBeInTheDocument()
    expect(screen.getByText('Deal-breaker')).toBeInTheDocument()
    expect(screen.getByText(/Strengths/)).toBeInTheDocument()
    expect(screen.getByText(/Concerns/)).toBeInTheDocument()
  })
})
