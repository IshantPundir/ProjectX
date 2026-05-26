import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AiRecommendationCard } from '@/components/dashboard/reports/AiRecommendationCard'
import type { ReportRead } from '@/lib/api/reports'

const report = {
  verdict: 'reject', verdict_reason: 'failed must-have: Python proficiency',
  overall_score: 36, overall_coverage: 0.7, overall_confidence: 'medium',
  dimension_scores: {
    technical: { name: 'Technical', score: 37, coverage: 0.66, confidence: 'medium', note: null },
    behavioral: { name: 'Behavioral', score: null, coverage: 0, confidence: 'low', note: 'no signal' },
    communication: { name: 'Communication', score: 30, coverage: 1, confidence: 'medium', note: 'content-only' },
  },
} as unknown as ReportRead

describe('AiRecommendationCard', () => {
  it('renders the verdict band, overall 0-10, coverage and confidence', async () => {
    render(<AiRecommendationCard report={report} />)
    expect(screen.getByText('Reject')).toBeInTheDocument()
    expect(await screen.findByText('3.6')).toBeInTheDocument() // overall gauge
    expect(screen.getByText('0.70')).toBeInTheDocument()       // coverage
    expect(screen.getByText('Medium')).toBeInTheDocument()     // confidence
  })
  it('renders Behavioral dimension as n/a (null score)', () => {
    render(<AiRecommendationCard report={report} />)
    expect(screen.getByRole('img', { name: /Behavioral not assessed/i })).toBeInTheDocument()
  })
})
