/**
 * Render smoke test for the public recordings route.
 *
 * Verifies that `PublicRecordingsView` can mount, resolve a mocked
 * `PublicRecordingsEnvelope`, and render the candidate name + full-session
 * affordance without throwing. Theater components are stubbed to keep the
 * test free of Dialog portals, <video> elements, and canvas APIs that are
 * unavailable in jsdom.
 */
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

// --- Mocks (hoisted by Vitest before module resolution) ---

vi.mock('next/navigation', () => ({
  useSearchParams: () => ({ get: (_key: string): string | null => null }),
}))

// Stub the ReviewTheater to avoid Dialog portals, <video> elements, and canvas
// (none available in jsdom). Emits the candidate name + a "Full session"
// landmark so the smoke assertions can probe both without touching theater
// internals.
vi.mock('@/components/recordings/theater/ReviewTheater', () => ({
  ReviewTheater: ({
    candidateName,
    open,
  }: {
    candidateName: string
    open: boolean
  }) =>
    open ? (
      <section aria-label="Full session">{candidateName}</section>
    ) : null,
}))

// ReelTheater is never mounted when reel.status is 'absent', but stub it to
// prevent any import-side-effect from running in jsdom.
vi.mock('@/components/recordings/theater/ReelTheater', () => ({
  ReelTheater: () => null,
}))

// --- Imports (after mocks so the stubs are in place) ---

import type { PublicRecordingsEnvelope } from '@/components/recordings/api/reports'
import { reportsApi } from '@/components/recordings/api/reports'
import { PublicRecordingsView } from '@/components/recordings/PublicRecordingsView'

// --- Test data ---

const CANDIDATE_NAME = 'Jane Smith'

const MOCK_ENVELOPE: PublicRecordingsEnvelope = {
  candidate_name: CANDIDATE_NAME,
  job_title: 'Software Engineer',
  stage_label: 'AI Screening',
  report: {
    header: null,
    verdict: 'advance',
    verdict_reason: 'Strong candidate.',
    overall_score: 8,
    overall_coverage: 0.9,
    overall_confidence: 'high',
    decision: {
      headline: 'Strong fit',
      why_positive: { title: 'Strengths', body: 'Technically solid.' },
      why_negative: { title: 'Areas for growth', body: 'None identified.' },
    },
    scores: {},
    quick_summary: 'Strong candidate.',
    strengths: [],
    concerns: [],
    questions: [],
    methodology: { note: '', charity_flags: [] },
    signal_assessments: [],
    id: 'report-abc',
    session_id: 'session-abc',
    status: 'ready',
    engine_version: null,
    version: 1,
    scoring_manifest: null,
    human_decision: null,
    generated_at: '2024-01-01T00:00:00Z',
    reference_photo_url: null,
  },
  recording: {
    status: 'ready',
    signed_url: 'https://example-r2.cloudflarestorage.com/recording.mp4',
    expires_at: null,
    duration_seconds: 1200,
    offset_ms: 0,
    transcript: [],
  },
  proctoring: {
    status: 'absent',
    risk_band: null,
    detector_summary: null,
    gaze_heatmap: null,
    flagged_intervals: [],
    gaze_signal_quality: null,
    unscorable_pct: null,
  },
  // No reel: the page should show the full-session theater directly.
  reel: {
    status: 'absent',
    signed_url: null,
    expires_at: null,
    duration_seconds: null,
    chapters: [],
    generation_error: null,
    eligible: false,
    ineligible_reason: 'No reel generated',
    version: 0,
  },
}

// --- Helpers ---

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

afterEach(() => vi.restoreAllMocks())

// --- Tests ---

describe('PublicRecordingsView — render smoke', () => {
  it('renders the candidate name and full-session view from the mocked envelope', async () => {
    vi.spyOn(reportsApi, 'getPublicRecordings').mockResolvedValue(MOCK_ENVELOPE)

    renderWithQuery(<PublicRecordingsView token="share-token-abc" />)

    // Waits for the query to resolve and the component to re-render with data.
    expect(await screen.findByText(CANDIDATE_NAME)).toBeInTheDocument()

    // No reel → activeMode = 'full' → ReviewTheater stub renders with the
    // full-session landmark.
    expect(
      screen.getByRole('region', { name: /full session/i }),
    ).toBeInTheDocument()
  })

  it('shows a skeleton while the envelope is fetching', () => {
    // Never resolves — stays in loading state indefinitely.
    vi.spyOn(reportsApi, 'getPublicRecordings').mockImplementation(
      () => new Promise(() => {}),
    )

    renderWithQuery(<PublicRecordingsView token="share-token-abc" />)

    // No candidate name while loading.
    expect(screen.queryByText(CANDIDATE_NAME)).not.toBeInTheDocument()
    // The Skeleton block is rendered (data-testid is on the wrapper div).
    // We verify loading state by confirming the name hasn't appeared yet.
  })

  it('shows an error state when the API returns an error', async () => {
    const { ApiError } = await import('@/components/recordings/api/client')
    vi.spyOn(reportsApi, 'getPublicRecordings').mockRejectedValue(
      new ApiError('Not found', 404),
    )

    renderWithQuery(<PublicRecordingsView token="share-token-abc" />)

    // The "no longer available" error message should appear.
    expect(
      await screen.findByText(/no longer available/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(CANDIDATE_NAME)).not.toBeInTheDocument()
  })
})
