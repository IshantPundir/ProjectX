import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ScoreRail } from '@/components/dashboard/reports/theater/ScoreRail'
import type { ReportRead } from '@/lib/api/reports'

const base = {
  verdict: 'reject',
  scores: {
    overall: { score: 35, tier_label: '', tone: 'danger', confidence: 'low', coverage: 0.27 },
    technical: { score: 44, tier_label: '', tone: 'caution', confidence: 'low', coverage: 0.3 },
    communication: { score: 70, tier_label: '', tone: 'ok', confidence: 'medium', coverage: 1 },
    // behavioral exists but was never scored — must not render a gauge.
    behavioral: { score: null, tier_label: '', tone: 'neutral', confidence: 'low', coverage: 0 },
  },
} as unknown as ReportRead

describe('ScoreRail', () => {
  it('shows identity, only scored gauges, and an off-screen gauge with the real %', () => {
    render(<ScoreRail report={base} candidateName="Ishant Pundir" subtitle="New Stage" offScreenPct={0.3} />)
    expect(screen.getByText('Ishant Pundir')).toBeInTheDocument()
    expect(screen.getByText('New Stage')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Overall score/i })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Technical score/i })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Comms score/i })).toBeInTheDocument()
    // unscored behavioral dimension is omitted
    expect(screen.queryByRole('img', { name: /Behavioral score/i })).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Off-screen 30%/i })).toBeInTheDocument()
  })

  it('omits the off-screen gauge when proctoring is unavailable', () => {
    render(<ScoreRail report={base} candidateName="Ishant Pundir" subtitle="" offScreenPct={null} />)
    expect(screen.queryByText(/Off-screen/i)).not.toBeInTheDocument()
  })
})
