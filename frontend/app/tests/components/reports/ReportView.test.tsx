import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReportView } from '@/components/dashboard/reports/ReportView'
import { makeReport } from './_fixture'

// ReportView embeds <GlanceBand> + <ProctoringIntegrityPanel> which call query
// hooks. Mock them so this stays a focused layout test (no network required).
vi.mock('@/lib/hooks/use-session-proctoring', () => ({
  useSessionProctoring: () => ({ data: undefined, isLoading: true }),
}))
// useReel is used by ReportView for the ImmersiveHeader reel CTA
vi.mock('@/lib/hooks/use-reel', () => ({
  useReel: () => ({ data: { status: 'absent', signed_url: null, eligible: false, chapters: [] }, isLoading: false }),
}))
// useShareReport is used by ShareReportDialog embedded in ReportView
vi.mock('@/lib/hooks/use-share-report', () => ({
  useShareReport: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

const noop = vi.fn()

function renderView(report = makeReport()) {
  // ReportView uses useReel + may embed ShareReportDialog (useShareReport →
  // useMutation), so a QueryClient is required.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ReportView report={report} sessionId="s1" candidateName="Asha" candidateId="c1"
        title="Jr. FDE" subtitle="AI Screening" canRegenerate={false}
        onRegenerate={noop} onDecision={noop} isSubmitting={false} />
    </QueryClientProvider>,
  )
}

describe('ReportView — Layout II', () => {
  it('renders the glance band, left-column content, and right-rail panels', () => {
    renderView()
    // Glance band
    expect(screen.getByRole('region', { name: /Candidate at a glance/ })).toBeInTheDocument()
    expect(screen.getByText('Dimensions')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Overall score 4.1 out of 10/ })).toBeInTheDocument()
    // Left column
    expect(screen.getByText('Why this verdict')).toBeInTheDocument()
    expect(screen.getByText('Quick summary')).toBeInTheDocument()
    expect(screen.getByText('Question by question')).toBeInTheDocument()
    expect(screen.getByText(/Audit detail/)).toBeInTheDocument()
    // Methodology footer
    expect(screen.getByText(/About this report/)).toBeInTheDocument()
  })

  it('renders a legacy fallback header when report.header is null', () => {
    // header is null in the default fixture — should show candidate name without crashing
    renderView()
    // The legacy header shows the candidateName passed as prop when header is null
    expect(screen.getByText('Asha')).toBeInTheDocument()
  })

  it('renders ImmersiveHeader when report.header is present', () => {
    const report = makeReport({
      header: {
        candidate_name: 'Punar Singh',
        candidate_email: 'punar@example.com',
        job_title: 'Software Engineer',
        stage_label: 'AI Screening',
        session_started_at: '2026-06-01T10:00:00Z',
        duration_seconds: 1800,
        skills: ['React', 'TypeScript'],
        reference_photo_url: null,
      },
    })
    renderView(report)
    expect(screen.getByText('Punar Singh')).toBeInTheDocument()
    expect(screen.getByText('punar@example.com')).toBeInTheDocument()
  })

  it('does not crash when optional collections are empty', () => {
    renderView(makeReport({ strengths: [], concerns: [], questions: [], signal_assessments: [] }))
    expect(screen.getByText('Quick summary')).toBeInTheDocument()
  })

  it('scores render without double-division (0-10 native)', () => {
    // Fixture overall.score=4.1 (0-10 native). ScoreBar uses formatTen (no ÷10),
    // so it should display "4.1" — not "0.4" (double-divided).
    renderView()
    expect(screen.getAllByText('4.1').length).toBeGreaterThan(0)
  })

  it('Share button is present in the action cluster', () => {
    renderView()
    expect(screen.getByRole('button', { name: /share/i })).toBeInTheDocument()
  })
})
