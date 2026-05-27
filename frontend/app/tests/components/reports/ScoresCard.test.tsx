import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoresCard } from '@/components/dashboard/reports/ScoresCard'
import { makeReport } from './_fixture'

describe('ScoresCard', () => {
  it('shows the relabeled verdict, headline, and assessed gauges only', () => {
    render(<ScoresCard report={makeReport()} />)
    expect(screen.getByText('Borderline')).toBeInTheDocument()
    expect(screen.getByText(/Credible baseline/)).toBeInTheDocument()
    expect(screen.getByText('Overall')).toBeInTheDocument()
    expect(screen.getByText('Technical')).toBeInTheDocument()
    expect(screen.getByText('Communication')).toBeInTheDocument()
    expect(screen.queryByText('Behavioral')).not.toBeInTheDocument()  // not_assessed → hidden
    expect(screen.getAllByText('4.1').length).toBeGreaterThan(0)
  })
})
