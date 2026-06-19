import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReportView } from '@/components/dashboard/reports/ReportView'
import { makeReport } from './_fixture'

// ReportView embeds <SessionPlayback>, <ProctoringIntegrityPanel>, and
// <AtAGlanceBand> which call query hooks. Mock them so this stays a focused
// layout test (no network required).
vi.mock('@/lib/hooks/use-session-recording', () => ({
  useSessionRecording: () => ({ data: { status: 'absent', transcript: [] }, isLoading: false }),
}))
vi.mock('@/lib/hooks/use-session-proctoring', () => ({
  useSessionProctoring: () => ({ data: undefined, isLoading: true }),
}))
// useReel is used by ReportView for the ImmersiveHeader reel CTA
vi.mock('@/lib/hooks/use-reel', () => ({
  useReel: () => ({ data: { status: 'absent', signed_url: null, eligible: false, chapters: [] }, isLoading: false }),
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
  it('renders the at-a-glance band, left-column content, and right-rail panels', () => {
    renderView()
    // At-a-glance band: signals top strip (multiple "Strengths" elements expected)
    expect(screen.getAllByText(/Strengths/i).length).toBeGreaterThan(0)
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

  it('scores in ScoresCard are rendered without double-division (0-10 native)', () => {
    // The fixture has overall.score=41 which is OLD 0-100 format (test data not yet updated)
    // but ScoresCard/ScoreGauge now render as-is (formatTen, no ÷10).
    // This confirms no double-division: score=41 → "41.0" displayed, not "4.1".
    renderView()
    // ScoresCard should show the score as-is (41.0 from the fixture)
    expect(screen.getAllByText('41.0').length).toBeGreaterThan(0)
  })
})
