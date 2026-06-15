import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SessionPlayback } from '@/components/dashboard/reports/SessionPlayback'
import type { ReportRead } from '@/lib/api/reports'

// SessionPlayback reuses the recording duration to poster itself with the same
// mid-interview frame the ReviewTheater <video> uses. Mock the recording hook so
// the test supplies a known duration without a QueryClient / network.
const recMock = vi.hoisted(() => ({
  value: { status: 'absent', duration_seconds: null } as {
    status: string
    duration_seconds: number | null
  },
}))
vi.mock('@/lib/hooks/use-session-recording', () => ({
  useSessionRecording: () => ({ data: recMock.value, isLoading: false }),
}))

describe('SessionPlayback poster', () => {
  it('calls onOpen when the play poster is clicked', async () => {
    const report = { verdict: 'reject', questions: [] } as unknown as ReportRead
    const onOpen = vi.fn()
    render(<SessionPlayback report={report} onOpen={onOpen} />)
    await userEvent.click(screen.getByRole('button', { name: /play|review/i }))
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('posters with the question frame nearest the recording midpoint', () => {
    // 600s recording → midpoint 300_000ms. q2 (asked at 290s) is the mid-most.
    recMock.value = { status: 'ready', duration_seconds: 600 }
    const report = {
      verdict: 'advance',
      session_id: 'sess-1',
      questions: [
        { asked_at_ms: 5_000, thumbnail_url: 'https://r2.example/frame-q1.jpg' },
        { asked_at_ms: 290_000, thumbnail_url: 'https://r2.example/frame-q2.jpg' },
        { asked_at_ms: 580_000, thumbnail_url: 'https://r2.example/frame-q3.jpg' },
      ],
    } as unknown as ReportRead
    const { container } = render(<SessionPlayback report={report} onOpen={() => {}} />)
    const img = container.querySelector('img')
    expect(img).not.toBeNull()
    expect(img?.getAttribute('src')).toContain('frame-q2.jpg')
  })

  it('shows no poster image when the recording duration is unknown', () => {
    // Without a duration, pickPosterUrl can't pick a mid-frame → gradient fallback.
    recMock.value = { status: 'ready', duration_seconds: null }
    const report = {
      verdict: 'advance',
      session_id: 'sess-1',
      questions: [
        { asked_at_ms: 5_000, thumbnail_url: 'https://r2.example/frame-q1.jpg' },
      ],
    } as unknown as ReportRead
    const { container } = render(<SessionPlayback report={report} onOpen={() => {}} />)
    expect(container.querySelector('img')).toBeNull()
  })
})
