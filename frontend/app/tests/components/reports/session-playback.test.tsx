import { describe, expect, it, vi, beforeEach, type Mock } from 'vitest'
import { render, screen } from '@testing-library/react'

import {
  SessionPlayback,
  activeSegmentIndex,
} from '@/components/dashboard/reports/SessionPlayback'
import type { RecordingPlayback, RecordingTranscriptSegment } from '@/lib/api/reports'

vi.mock('@/lib/hooks/use-session-recording', () => ({
  useSessionRecording: vi.fn(),
}))
import { useSessionRecording } from '@/lib/hooks/use-session-recording'

const mockHook = useSessionRecording as unknown as Mock

function setRecording(data: RecordingPlayback | undefined, isLoading = false) {
  mockHook.mockReturnValue({ data, isLoading })
}

const SEGMENTS: RecordingTranscriptSegment[] = [
  { role: 'agent', text: 'Tell me about yourself', t_ms: 0 },
  { role: 'candidate', text: 'I am a backend engineer', t_ms: 2000 },
  { role: 'agent', text: 'Great, go on', t_ms: 5000 },
]

describe('activeSegmentIndex', () => {
  it('returns -1 before the first segment', () => {
    expect(activeSegmentIndex(SEGMENTS, -1)).toBe(-1)
  })
  it('returns the last segment whose start <= currentMs', () => {
    expect(activeSegmentIndex(SEGMENTS, 0)).toBe(0)
    expect(activeSegmentIndex(SEGMENTS, 1999)).toBe(0)
    expect(activeSegmentIndex(SEGMENTS, 2000)).toBe(1)
    expect(activeSegmentIndex(SEGMENTS, 4000)).toBe(1)
    expect(activeSegmentIndex(SEGMENTS, 99999)).toBe(2)
  })
  it('handles an empty transcript', () => {
    expect(activeSegmentIndex([], 1000)).toBe(-1)
  })
})

describe('SessionPlayback', () => {
  beforeEach(() => mockHook.mockReset())

  it('renders the video and transcript when ready', () => {
    setRecording({
      status: 'ready',
      signed_url: 'https://signed.example/v.mp4?sig=x',
      expires_at: null,
      duration_seconds: 12,
      offset_ms: 0,
      transcript: SEGMENTS,
    })
    const { container } = render(<SessionPlayback sessionId="s1" />)

    const video = container.querySelector('video')
    expect(video).not.toBeNull()
    expect(video?.getAttribute('src')).toContain('v.mp4')
    expect(screen.getByText('Tell me about yourself')).toBeInTheDocument()
    expect(screen.getByText('I am a backend engineer')).toBeInTheDocument()
    expect(screen.getAllByText(/Candidate/i).length).toBeGreaterThan(0)
  })

  it('shows a processing state while recording', () => {
    setRecording({ status: 'recording', signed_url: null, expires_at: null, duration_seconds: null, offset_ms: 0, transcript: [] })
    render(<SessionPlayback sessionId="s1" />)
    expect(screen.getByText(/processing/i)).toBeInTheDocument()
  })

  it('shows an unavailable state on failure', () => {
    setRecording({ status: 'failed', signed_url: null, expires_at: null, duration_seconds: null, offset_ms: 0, transcript: [] })
    render(<SessionPlayback sessionId="s1" />)
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument()
  })

  it('shows a no-recording state when absent', () => {
    setRecording({ status: 'absent', signed_url: null, expires_at: null, duration_seconds: null, offset_ms: 0, transcript: [] })
    render(<SessionPlayback sessionId="s1" />)
    expect(screen.getByText(/no recording/i)).toBeInTheDocument()
  })

  it('always renders the verbal-content-only badge', () => {
    setRecording({ status: 'absent', signed_url: null, expires_at: null, duration_seconds: null, offset_ms: 0, transcript: [] })
    render(<SessionPlayback sessionId="s1" />)
    expect(screen.getByText(/no facial/i)).toBeInTheDocument()
  })
})
