import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SignalScorecards } from '@/components/dashboard/reports/SignalScorecards'
import type { KnockoutResultOut, SignalScorecard } from '@/lib/api/reports'

const ko: KnockoutResultOut = {
  signal: '4+ years experience', status: 'failed',
  reason: 'answer did not establish required tenure',
  evidence: [{ quote: 'more than sixteen years', timestamp_ms: 90000, question_id: 'years', grounded: true }],
}
const passSig: SignalScorecard = {
  value: 'REST API design', type: 'competency', weight: 2, knockout: false,
  state: 'meets_bar', score: 70, opportunity: 'full', evidence: [], covered_by: ['q1'],
}
const naSig: SignalScorecard = {
  value: 'System design', type: 'competency', weight: 2, knockout: false,
  state: 'not_assessed', score: null, opportunity: 'none', evidence: [], covered_by: [],
}

describe('SignalScorecards', () => {
  it('shows every knockout reason AND its evidence quote inline', () => {
    render(<SignalScorecards knockouts={[ko]} signals={[]} />)
    expect(screen.getByText(/did not establish required tenure/)).toBeInTheDocument()
    expect(screen.getByText(/more than sixteen years/)).toBeInTheDocument() // the "catch the miscalibration" payoff
  })
  it('renders not_assessed signals as an explicit state, not a zero', () => {
    render(<SignalScorecards knockouts={[]} signals={[naSig]} />)
    expect(screen.getByText('Not assessed')).toBeInTheDocument()
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
  })
  it('renders a passing signal with its 0-10 score', () => {
    render(<SignalScorecards knockouts={[]} signals={[passSig]} />)
    expect(screen.getByText('7.0')).toBeInTheDocument()
  })
})
