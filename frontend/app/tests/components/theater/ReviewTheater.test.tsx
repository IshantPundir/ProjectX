import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { ReviewTheater } from '@/components/dashboard/reports/theater/ReviewTheater'
import type { ProctoringAnalysis, RecordingPlayback, ReportRead } from '@/lib/api/reports'

vi.mock('@/lib/hooks/use-session-recording', () => ({
  useSessionRecording: () => ({
    data: {
      status: 'ready', signed_url: 'https://x/v.mp4', expires_at: null,
      duration_seconds: 242, offset_ms: 0,
      transcript: [{ role: 'agent', text: 'Hi', t_ms: 0 }],
    } satisfies RecordingPlayback,
    isLoading: false,
  }),
}))

vi.mock('@/lib/hooks/use-session-proctoring', () => ({
  useSessionProctoring: () => ({
    data: {
      status: 'ready', risk_band: 'high',
      detector_summary: { off_screen_pct: 0.56, down_glance_count: 42, reading_sweep_intervals: 0, max_faces: 2, multi_face_intervals: [] },
      gaze_heatmap: null,
      flagged_intervals: [{ kind: 'off_screen_sustained', start_ms: 16200, end_ms: 18400, confidence: 0.65 }],
      gaze_signal_quality: 'good', unscorable_pct: 0.02,
    } satisfies ProctoringAnalysis,
    isLoading: false,
  }),
}))

beforeAll(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
})

const report = {
  session_id: 's1', verdict: 'reject',
  decision: { headline: 'Closed early', why_positive: { title: 'P', body: 'p' }, why_negative: { title: 'N', body: 'n' } },
  scores: { overall: { score: 35, tier_label: 'x', tone: 'danger', confidence: 'low', coverage: 0.3 } },
  questions: [
    { seq: 1, question_id: 'q1', title: 'Experience', status_badge: 'passed', status_tone: 'ok',
      question_text: 'Years?', candidate_quote: 'six', our_read: 'ok', asked_at_ms: 23000, thumbnail_url: null },
  ],
} as unknown as ReportRead

describe('ReviewTheater', () => {
  it('renders the stage, timeline and verdict when open', () => {
    render(<ReviewTheater open report={report} candidateName="Aarav" subtitle="Jr. FDE" onClose={() => {}} />)
    expect(screen.getByLabelText(/Interview session recording/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Experience/i })).toBeInTheDocument()
    expect(screen.getByText(/Not Recommended/i)).toBeInTheDocument()
  })

  it('selecting a question shows its read in the panel', async () => {
    render(<ReviewTheater open report={report} candidateName="Aarav" subtitle="" onClose={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: /Experience/i }))
    expect(screen.getByText(/Years\?/)).toBeInTheDocument()
  })

  it('does not render when closed', () => {
    render(<ReviewTheater open={false} report={report} candidateName="A" subtitle="" onClose={() => {}} />)
    expect(screen.queryByLabelText(/Interview session recording/i)).not.toBeInTheDocument()
  })

  it('posters the <video> with the mid-interview question frame when one qualifies', () => {
    // duration 242s → midpoint 121_000ms; q2 (120_000) is nearest among the
    // questions that carry BOTH a timing and a thumbnail.
    const withThumbs = {
      ...report,
      questions: [
        { ...report.questions[0], question_id: 'q1', asked_at_ms: 10_000, thumbnail_url: 'thumb-q1' },
        { ...report.questions[0], question_id: 'q2', asked_at_ms: 120_000, thumbnail_url: 'thumb-q2' },
      ],
    } as unknown as ReportRead
    render(<ReviewTheater open report={withThumbs} candidateName="Aarav" subtitle="" onClose={() => {}} />)
    const video = screen.getByLabelText(/Interview session recording/i)
    expect(video.getAttribute('poster')).toBe('thumb-q2')
  })

  it('omits the poster when no question carries a thumbnail', () => {
    render(<ReviewTheater open report={report} candidateName="Aarav" subtitle="" onClose={() => {}} />)
    const video = screen.getByLabelText(/Interview session recording/i)
    expect(video.hasAttribute('poster')).toBe(false)
  })

  it('pre-selects the flag when opened with initialFlagStartMs', async () => {
    render(
      <ReviewTheater open report={report} candidateName="Aarav" subtitle=""
        initialFlagStartMs={16200} onClose={() => {}} />,
    )
    // The "this moment" panel switches to the flag detail (kind label) + confidence.
    expect(await screen.findByText(/Looked off-screen/i)).toBeInTheDocument()
    expect(screen.getByText(/65% confidence/i)).toBeInTheDocument()
  })
})
