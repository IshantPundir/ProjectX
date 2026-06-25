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
    // Scores are 0–10; fixture overall.score=4.1, formatTen renders as-is → "4.1"
    expect(screen.getAllByText('4.1').length).toBeGreaterThan(0)
  })

  it('shows the session-score provenance sub-line (values already 0–10)', () => {
    const report = makeReport({
      scores: {
        overall: { score: 3.8, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium',
          coverage: 0.47, session_score: 3.6, holistic_delta: 0.2 },
        technical: { score: 4.1, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium', coverage: 0.55 },
        behavioral: { score: null, tier_label: 'Not Assessed', tone: 'neutral', confidence: 'low', coverage: 0 },
        communication: { score: 7.0, tier_label: 'Meets Bar', tone: 'ok', confidence: 'medium', coverage: 1 },
      },
    })
    render(<ScoresCard report={report} />)
    // session_score=3.6 → "3.6", holistic_delta=0.2 → "+0.2"
    expect(screen.getByText(/Session score 3\.6 · holistic \+0\.2/)).toBeInTheDocument()
  })

  it('renders the competency radar section when signal_assessments is non-empty', () => {
    render(<ScoresCard report={makeReport()} />)
    // Section label
    expect(screen.getByText('Competency breakdown')).toBeInTheDocument()
    // CompetencyRadar renders the signal name as an SVG label (fixture has one assessment)
    expect(screen.getByRole('img', { name: /competency/i })).toBeInTheDocument()
  })

  it('does not render the competency radar section when signal_assessments is empty', () => {
    render(<ScoresCard report={makeReport({ signal_assessments: [] })} />)
    expect(screen.queryByText('Competency breakdown')).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /competency/i })).not.toBeInTheDocument()
  })
})
