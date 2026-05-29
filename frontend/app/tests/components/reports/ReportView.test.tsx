import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportView } from '@/components/dashboard/reports/ReportView'
import { makeReport } from './_fixture'

// ReportView embeds <SessionPlayback>, which calls the recording query hook.
// Mock it so this stays a focused layout test (no QueryClient / network).
vi.mock('@/lib/hooks/use-session-recording', () => ({
  useSessionRecording: () => ({ data: { status: 'absent', transcript: [] }, isLoading: false }),
}))

const noop = vi.fn()

function renderView(report = makeReport()) {
  return render(
    <ReportView report={report} candidateName="Asha" candidateId="c1"
      title="Jr. FDE" subtitle="AI Screening" canRegenerate={false}
      onRegenerate={noop} onDecision={noop} isSubmitting={false} />,
  )
}

describe('ReportView', () => {
  it('renders all PDF sections from a ready report', () => {
    renderView()
    expect(screen.getAllByText('Borderline').length).toBeGreaterThan(0)
    expect(screen.getByText('Why this verdict')).toBeInTheDocument()
    expect(screen.getByText('Quick summary')).toBeInTheDocument()
    expect(screen.getByText(/Strengths/)).toBeInTheDocument()
    expect(screen.getByText('Question by question')).toBeInTheDocument()
    expect(screen.getByText(/Audit detail/)).toBeInTheDocument()
    expect(screen.getByText(/About this report/)).toBeInTheDocument()
  })
  it('does not crash when optional collections are empty', () => {
    renderView(makeReport({ strengths: [], concerns: [], questions: [], signal_assessments: [] }))
    expect(screen.getByText('Quick summary')).toBeInTheDocument()
  })
})
