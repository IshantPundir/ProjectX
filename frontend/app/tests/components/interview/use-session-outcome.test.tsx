import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const remoteParticipantsMock = vi.fn()

vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => remoteParticipantsMock(),
}))

import { useSessionOutcome } from '@/components/interview/app/hooks/use-session-outcome'

function Probe({ onChange }: { onChange: (v: string | null) => void }) {
  const v = useSessionOutcome()
  onChange(v)
  return null
}

describe('useSessionOutcome', () => {
  it('captures the latest session_outcome attribute and returns it after the participant disappears', () => {
    remoteParticipantsMock.mockReturnValue([
      {
        identity: 'agent-stub',
        attributes: { session_outcome: 'completed' },
      },
    ])
    let captured: string | null = null
    const { rerender } = render(<Probe onChange={(v) => { captured = v }} />)
    expect(captured).toBe('completed')

    // Agent disappears (simulating disconnect mid-frame).
    remoteParticipantsMock.mockReturnValue([])
    rerender(<Probe onChange={(v) => { captured = v }} />)
    // Hook still returns 'completed' from its ref.
    expect(captured).toBe('completed')
  })

  it('returns null when no agent is present and none ever was', () => {
    remoteParticipantsMock.mockReturnValue([])
    let captured: string | null = 'init'
    render(<Probe onChange={(v) => { captured = v }} />)
    expect(captured).toBeNull()
  })
})
