import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Lightweight stubs for the heavy theater modals — we only assert which one is
// "open" and that the toggle drives it. Their internals have their own tests.
vi.mock('@/components/dashboard/reports/theater/ReviewTheater', () => ({
  ReviewTheater: ({ open }: { open: boolean }) => (
    <div data-testid="review-theater" data-open={open ? 'true' : 'false'} />
  ),
}))
vi.mock('@/components/dashboard/reports/theater/ReelTheater', () => ({
  ReelTheater: ({ open }: { open: boolean }) => (
    <div data-testid="reel-theater" data-open={open ? 'true' : 'false'} />
  ),
}))

import { reportsApi } from '@/lib/api/reports'
import { PublicRecordingsView } from '@/components/dashboard/reports/PublicRecordingsView'

function envelope(reelStatus: 'ready' | 'absent') {
  return {
    candidate_name: 'Jane Doe',
    job_title: 'FDE',
    stage_label: 'New Stage',
    report: { session_id: 's1', questions: [], reference_photo_url: null, verdict: 'advance' },
    recording: { status: 'ready', signed_url: 'https://r2/v', offset_ms: 0, duration_seconds: 60, transcript: [] },
    proctoring: { status: 'absent' },
    reel: {
      status: reelStatus,
      signed_url: reelStatus === 'ready' ? 'https://r2/reel' : null,
      chapters: [], duration_seconds: reelStatus === 'ready' ? 45 : null,
      eligible: reelStatus === 'ready', ineligible_reason: null, version: reelStatus === 'ready' ? 1 : 0,
      generation_error: null, expires_at: null,
    },
  }
}

function renderView() {
  const qc = new QueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
  return render(<PublicRecordingsView token="tok" />, { wrapper })
}

afterEach(() => vi.restoreAllMocks())

describe('PublicRecordingsView', () => {
  it('lands on the reel and shows a switch with both views when a reel exists', async () => {
    vi.spyOn(reportsApi, 'getPublicRecordings').mockResolvedValue(envelope('ready') as never)
    renderView()

    await waitFor(() => expect(screen.getByRole('tablist')).toBeInTheDocument())
    expect(screen.getByRole('tab', { name: /highlight reel/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /full session/i })).toBeInTheDocument()

    // Defaults to the reel playing; full session is mounted but closed.
    expect(screen.getByTestId('reel-theater')).toHaveAttribute('data-open', 'true')
    expect(screen.getByTestId('review-theater')).toHaveAttribute('data-open', 'false')
  })

  it('switches to Full session when the toggle is clicked', async () => {
    vi.spyOn(reportsApi, 'getPublicRecordings').mockResolvedValue(envelope('ready') as never)
    renderView()
    await waitFor(() => expect(screen.getByRole('tablist')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('tab', { name: /full session/i }))

    expect(screen.getByTestId('review-theater')).toHaveAttribute('data-open', 'true')
    expect(screen.getByTestId('reel-theater')).toHaveAttribute('data-open', 'false')
  })

  it('shows Full session with NO switch when there is no reel', async () => {
    vi.spyOn(reportsApi, 'getPublicRecordings').mockResolvedValue(envelope('absent') as never)
    renderView()

    await waitFor(() =>
      expect(screen.getByTestId('review-theater')).toHaveAttribute('data-open', 'true'),
    )
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByTestId('reel-theater')).toBeNull()
  })
})
