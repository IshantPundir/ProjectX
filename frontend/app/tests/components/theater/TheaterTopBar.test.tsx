import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { TheaterTopBar } from '@/components/dashboard/reports/theater/TheaterTopBar'
import type { ReportRead } from '@/lib/api/reports'

// The top chrome now carries candidate identity on the left and the inline score
// gauges + verdict + close on the right (the old left ScoreRail was folded in).
const report = {
  verdict: 'reject',
  scores: {
    overall: { score: 35, tier_label: '', tone: 'danger', confidence: 'low', coverage: 0.27 },
    technical: { score: 44, tier_label: '', tone: 'caution', confidence: 'low', coverage: 0.3 },
    communication: { score: 70, tier_label: '', tone: 'ok', confidence: 'medium', coverage: 1 },
    // behavioral exists but was never scored — must not render a gauge.
    behavioral: { score: null, tier_label: '', tone: 'neutral', confidence: 'low', coverage: 0 },
  },
} as unknown as ReportRead

function renderBar(props: Partial<React.ComponentProps<typeof TheaterTopBar>> = {}) {
  return render(
    <TheaterTopBar
      report={report}
      candidateName="Ishant Pundir"
      subtitle="New Stage"
      integrityCaption={null}
      integrityPending={false}
      offScreenPct={0.3}
      onClose={() => {}}
      {...props}
    />,
  )
}

describe('TheaterTopBar', () => {
  it('renders identity, only-scored gauges, off-screen gauge, integrity summary, verdict chip and close', () => {
    renderBar({ integrityCaption: '⚠ HIGH RISK · 30% off-screen · 41 down-glances' })
    expect(screen.getByText('Ishant Pundir')).toBeInTheDocument()
    expect(screen.getByText('New Stage')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Overall score/i })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Technical score/i })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Comms score/i })).toBeInTheDocument()
    // unscored behavioral dimension is omitted
    expect(screen.queryByRole('img', { name: /Behavioral score/i })).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Off-screen 30%/i })).toBeInTheDocument()
    expect(screen.getByText(/HIGH RISK · 30% off-screen · 41 down-glances/i)).toBeInTheDocument()
    expect(screen.getByText(/Not Recommended/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument()
  })

  it('shows an analyzing hint while proctoring is still in flight', () => {
    renderBar({ integrityPending: true })
    expect(screen.getByText(/Analyzing integrity/i)).toBeInTheDocument()
  })

  it('omits the off-screen gauge and integrity summary when proctoring is unavailable', () => {
    renderBar({ offScreenPct: null })
    expect(screen.queryByRole('img', { name: /Off-screen/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/off-screen ·/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Analyzing integrity/i)).not.toBeInTheDocument()
  })

  it('hides the close button when showClose is false', () => {
    renderBar({ showClose: false })
    expect(screen.queryByRole('button', { name: /close/i })).not.toBeInTheDocument()
  })

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn()
    renderBar({ onClose })
    await userEvent.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
