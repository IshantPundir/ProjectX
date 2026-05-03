import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSessionOutcome } from '@/components/interview/app/hooks/use-session-outcome'
import { SESSION_OUTCOMES } from '@/components/interview/lib/session-outcome'

// Mock the @livekit/components-react useRemoteParticipants hook.
const mockRemotes = vi.hoisted(() => ({ value: [] as Array<{ identity: string; attributes: Record<string, string> }> }))
vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => mockRemotes.value,
}))

describe('useSessionOutcome', () => {
  beforeEach(() => {
    mockRemotes.value = []
  })

  it('returns null when no agent participant', () => {
    const { result } = renderHook(() => useSessionOutcome())
    expect(result.current).toBeNull()
  })

  it.each(SESSION_OUTCOMES)('returns %s when agent publishes it', (outcome) => {
    mockRemotes.value = [
      { identity: 'agent-abc123', attributes: { session_outcome: outcome } },
    ]
    const { result } = renderHook(() => useSessionOutcome())
    expect(result.current).toBe(outcome)
  })

  it('drops an unknown outcome string to null', () => {
    mockRemotes.value = [
      { identity: 'agent-abc123', attributes: { session_outcome: 'mystery_outcome' } },
    ]
    const { result } = renderHook(() => useSessionOutcome())
    expect(result.current).toBeNull()
  })

  it('keeps the last seen value when the agent disappears (ref-stickiness)', () => {
    mockRemotes.value = [
      { identity: 'agent-abc123', attributes: { session_outcome: 'completed' } },
    ]
    const { result, rerender } = renderHook(() => useSessionOutcome())
    expect(result.current).toBe('completed')

    mockRemotes.value = [] // agent disappears
    rerender()
    expect(result.current).toBe('completed') // sticky
  })
})
