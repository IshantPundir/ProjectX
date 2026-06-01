import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { TheaterTopBar } from '@/components/dashboard/reports/theater/TheaterTopBar'
import type { ReportRead } from '@/lib/api/reports'

// The top chrome is now minimal: a watermark, the verdict chip, an optional
// integrity-risk badge and a close button. Identity + gauges live in ScoreRail.
const report = { verdict: 'reject', scores: {} } as unknown as ReportRead

describe('TheaterTopBar', () => {
  it('renders the verdict chip, integrity-risk badge and a close button', () => {
    render(<TheaterTopBar report={report} riskBand="high" onClose={() => {}} />)
    expect(screen.getByText(/Not Recommended/i)).toBeInTheDocument()
    expect(screen.getByText(/integrity risk/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument()
  })

  it('omits the integrity badge unless risk is high', () => {
    render(<TheaterTopBar report={report} riskBand="low" onClose={() => {}} />)
    expect(screen.queryByText(/integrity risk/i)).not.toBeInTheDocument()
  })

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn()
    render(<TheaterTopBar report={report} riskBand={null} onClose={onClose} />)
    await userEvent.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
