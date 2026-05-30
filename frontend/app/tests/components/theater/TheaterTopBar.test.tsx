import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { TheaterTopBar } from '@/components/dashboard/reports/theater/TheaterTopBar'
import type { ReportRead } from '@/lib/api/reports'

const report = {
  verdict: 'reject',
  scores: {
    overall: { score: 35, tier_label: 'Well Below Bar', tone: 'danger', confidence: 'low', coverage: 0.27 },
    technical: { score: 44, tier_label: 'Below Bar', tone: 'caution', confidence: 'low', coverage: 0.3 },
    communication: { score: 70, tier_label: 'Strong', tone: 'ok', confidence: 'medium', coverage: 1 },
  },
} as unknown as ReportRead

describe('TheaterTopBar', () => {
  it('renders the verdict chip + dimension gauges and a close button', () => {
    render(<TheaterTopBar report={report} candidateName="Aarav" subtitle="Jr. FDE" riskBand="high" onClose={() => {}} />)
    expect(screen.getByText(/Not Recommended/i)).toBeInTheDocument()
    expect(screen.getByText('Aarav')).toBeInTheDocument()
    expect(screen.getByText(/high integrity risk/i)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Overall/i })).toBeInTheDocument()
  })

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn()
    render(<TheaterTopBar report={report} candidateName="Aarav" subtitle="" riskBand={null} onClose={onClose} />)
    await userEvent.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
