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

  it('shows the session-score provenance sub-line', () => {
    const report = makeReport({
      scores: {
        overall: { score: 38, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium',
          coverage: 0.47, session_score: 36, holistic_delta: 2 },
        technical: { score: 41, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium', coverage: 0.55 },
        behavioral: { score: null, tier_label: 'Not Assessed', tone: 'neutral', confidence: 'low', coverage: 0 },
        communication: { score: 70, tier_label: 'Meets Bar', tone: 'ok', confidence: 'medium', coverage: 1 },
      },
    })
    render(<ScoresCard report={report} />)
    expect(screen.getByText(/Session score 3\.6/)).toBeInTheDocument()
    expect(screen.getByText(/\+0\.2/)).toBeInTheDocument()
  })
})
