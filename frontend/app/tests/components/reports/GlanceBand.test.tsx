import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { GlanceBand } from '@/components/dashboard/reports/GlanceBand'
import { makeReport, makeSignalAssessment } from './_fixture'

describe('GlanceBand', () => {
  it('renders the verdict label, headline, and overall score', () => {
    render(<GlanceBand report={makeReport()} />)
    expect(screen.getByText('Borderline')).toBeInTheDocument()
    expect(screen.getByText(/Credible baseline/)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Overall score 4.1 out of 10/ })).toBeInTheDocument()
  })

  it('renders assessed dimensions only (behavioral is null in fixture)', () => {
    render(<GlanceBand report={makeReport()} />)
    expect(screen.getByRole('img', { name: /Technical score 4.1/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Communication score 7.0/ })).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /Behavioral score/ })).not.toBeInTheDocument()
  })

  it('groups must-have (knockout) signals under Must-have competencies', () => {
    const report = makeReport({
      signal_assessments: [
        makeSignalAssessment({ signal: 'Domain knowledge', knockout: true, weight: 3, score: 7.6 }),
        makeSignalAssessment({ signal: 'Problem-solving', knockout: false, weight: 2, score: 6.4 }),
      ],
    })
    render(<GlanceBand report={report} />)
    expect(screen.getByText('Must-have competencies')).toBeInTheDocument()
    expect(screen.getByText('Other competencies')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Domain knowledge score 7.6/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Problem-solving score 6.4/ })).toBeInTheDocument()
  })

  it('shows a not-reached competency as muted', () => {
    const report = makeReport({
      signal_assessments: [
        makeSignalAssessment({ signal: 'Ownership', knockout: true, provenance: 'not_reached', score: null }),
      ],
    })
    render(<GlanceBand report={report} />)
    expect(screen.getByRole('img', { name: /Ownership not reached/ })).toBeInTheDocument()
  })

  it('omits the competencies tier entirely when there are no signal assessments', () => {
    render(<GlanceBand report={makeReport({ signal_assessments: [] })} />)
    expect(screen.queryByText('Must-have competencies')).not.toBeInTheDocument()
    expect(screen.queryByText('Other competencies')).not.toBeInTheDocument()
  })

  it('does not render coverage or confidence (removed — caused recruiter confusion)', () => {
    render(<GlanceBand report={makeReport()} />)
    expect(screen.queryByText('Coverage')).not.toBeInTheDocument()
    expect(screen.queryByText('Confidence')).not.toBeInTheDocument()
  })
})
