import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@livekit/components-styles', () => ({}))

vi.mock('livekit-client', () => ({
  Track: {
    Source: {
      Camera: 'camera',
      Microphone: 'microphone',
    },
  },
}))

vi.mock('@livekit/components-react', () => ({
  LiveKitRoom: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  RoomAudioRenderer: () => null,
  VideoTrack: () => null,
  useRemoteParticipants: () => [],
  useVoiceAssistant: () => ({ state: 'listening', agent: undefined }),
  useParticipants: () => [],
  useChat: () => ({ chatMessages: [], send: vi.fn(), isSending: false }),
  useLocalParticipant: () => ({
    localParticipant: {
      getTrackPublication: () => null,
    },
  }),
}))

import { LiveSessionShell } from '@/app/(interview)/interview/[token]/LiveSession/LiveSessionShell'

describe('LiveSessionShell', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('flips to AGENT_NO_SHOW after 30s grace timeout when no agent joins', () => {
    render(
      <LiveSessionShell
        livekitUrl="wss://stub"
        livekitToken="tok"
        roomName="session-stub"
      />,
    )

    // Pre-condition: shell has rendered (mocked LiveKitRoom passes through);
    // no agent participant present (useRemoteParticipants returns []).
    expect(screen.queryByText(/Interviewer didn't connect/i)).toBeNull()

    // Advance fake timers past the 30-second grace deadline.
    act(() => {
      vi.advanceTimersByTime(30_000)
    })

    expect(
      screen.getByText(/Interviewer didn't connect/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/AGENT_NO_SHOW/i)).toBeInTheDocument()
  })
})
