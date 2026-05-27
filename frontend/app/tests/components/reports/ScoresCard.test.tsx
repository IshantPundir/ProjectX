import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoresCard } from '@/components/dashboard/reports/ScoresCard'
import { makeReport } from './_fixture'

describe('ScoresCard', () => {
  it('shows the relabeled verdict, headline, and four gauges', () => {
    render(<ScoresCard report={makeReport()} />)
    expect(screen.getByText('Borderline')).toBeInTheDocument()
    expect(screen.getByText(/Credible baseline/)).toBeInTheDocument()
    expect(screen.getByText('Overall')).toBeInTheDocument()
    expect(screen.getByText('Technical')).toBeInTheDocument()
    expect(screen.getByText('Behavioral')).toBeInTheDocument()
    expect(screen.getByText('Communication')).toBeInTheDocument()
    expect(screen.getAllByText('4.1').length).toBeGreaterThan(0)
  })
})
