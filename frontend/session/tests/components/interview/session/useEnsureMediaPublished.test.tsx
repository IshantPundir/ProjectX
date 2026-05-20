import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useEnsureMediaPublished } from '@/components/interview/session/useEnsureMediaPublished'

function makeRoom(connected: boolean) {
  return {
    state: connected ? 'connected' : 'disconnected',
    localParticipant: {
      setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined),
      setCameraEnabled: vi.fn().mockResolvedValue(undefined),
    },
  }
}

describe('useEnsureMediaPublished', () => {
  it('enables mic and camera once the room is connected', async () => {
    const room = makeRoom(true)
    renderHook(() => useEnsureMediaPublished(room as never))
    await waitFor(() => {
      expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(true)
      expect(room.localParticipant.setCameraEnabled).toHaveBeenCalledWith(true)
    })
  })

  it('does nothing while disconnected', () => {
    const room = makeRoom(false)
    renderHook(() => useEnsureMediaPublished(room as never))
    expect(room.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalled()
  })

  it('tolerates a missing room', () => {
    expect(() => renderHook(() => useEnsureMediaPublished(undefined))).not.toThrow()
  })
})
