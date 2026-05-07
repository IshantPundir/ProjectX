import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockRemotes = vi.hoisted(() => ({ value: [] as Array<{ identity: string; attributes: Record<string, string> }> }))
vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => mockRemotes.value,
}))

import { OutcomeWatcher } from '@/components/interview/app/app'

const RoomEvent = { Disconnected: 'disconnected' } as const

class FakeRoom {
  private handlers = new Map<string, Array<(...args: unknown[]) => void>>()
  // OutcomeWatcher proactively calls room.disconnect() when an engine
  // session_outcome attribute appears, so the fake needs to expose it.
  // We track invocations as a vi.fn() so individual tests can assert on
  // the auto-disconnect path. The implementation is a no-op — the tests
  // that exercise the Disconnected handler still emit the event manually
  // for deterministic ordering.
  disconnect = vi.fn()
  on(event: string, fn: (...args: unknown[]) => void) {
    const arr = this.handlers.get(event) ?? []
    arr.push(fn)
    this.handlers.set(event, arr)
    return this
  }
  off(event: string, fn: (...args: unknown[]) => void) {
    const arr = this.handlers.get(event)?.filter((h) => h !== fn) ?? []
    this.handlers.set(event, arr)
    return this
  }
  emit(event: string, ...args: unknown[]) {
    for (const h of this.handlers.get(event) ?? []) h(...args)
  }
}

describe('OutcomeWatcher', () => {
  let onCompleted: ReturnType<typeof vi.fn>
  let onError: ReturnType<typeof vi.fn>
  let room: FakeRoom

  beforeEach(() => {
    onCompleted = vi.fn()
    onError = vi.fn()
    room = new FakeRoom()
    mockRemotes.value = []
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  function setOutcome(outcome: string | null) {
    if (outcome === null) {
      mockRemotes.value = []
    } else {
      mockRemotes.value = [
        { identity: 'agent-abc', attributes: { session_outcome: outcome } },
      ]
    }
  }

  function mount() {
    return render(
      <OutcomeWatcher
        room={room as never}
        onCompleted={onCompleted}
        onError={onError}
      />,
    )
  }

  it.each([
    ['completed'],
    ['knockout_closed'],
    ['time_expired'],
    ['candidate_ended'],
  ])('routes %s to onCompleted', (outcome) => {
    setOutcome(outcome)
    mount()
    room.emit(RoomEvent.Disconnected)
    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })

  it('routes candidate_unresponsive to onError(CANDIDATE_UNRESPONSIVE)', () => {
    setOutcome('candidate_unresponsive')
    mount()
    room.emit(RoomEvent.Disconnected)
    expect(onError).toHaveBeenCalledWith('CANDIDATE_UNRESPONSIVE')
    expect(onCompleted).not.toHaveBeenCalled()
  })

  it('routes error to onError(ENGINE_ERROR)', () => {
    setOutcome('error')
    mount()
    room.emit(RoomEvent.Disconnected)
    expect(onError).toHaveBeenCalledWith('ENGINE_ERROR')
    expect(onCompleted).not.toHaveBeenCalled()
  })

  it('falls through to CLIENT_INITIATED to onCompleted when no engine outcome', () => {
    setOutcome(null)
    mount()
    room.emit(RoomEvent.Disconnected, 1) // DisconnectReason.CLIENT_INITIATED = 1
    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })

  it('falls through to DUPLICATE_IDENTITY to onError(DUPLICATE_SESSION)', () => {
    setOutcome(null)
    mount()
    room.emit(RoomEvent.Disconnected, 2) // DisconnectReason.DUPLICATE_IDENTITY = 2
    expect(onError).toHaveBeenCalledWith('DUPLICATE_SESSION')
    expect(onCompleted).not.toHaveBeenCalled()
  })

  it('falls through to UNEXPECTED_DISCONNECT for unknown reason', () => {
    setOutcome(null)
    mount()
    room.emit(RoomEvent.Disconnected, 99)
    expect(onError).toHaveBeenCalledWith('UNEXPECTED_DISCONNECT')
    expect(onCompleted).not.toHaveBeenCalled()
  })

  it('auto-calls room.disconnect() when an engine outcome is published', () => {
    setOutcome('completed')
    mount()
    expect(room.disconnect).toHaveBeenCalledTimes(1)
  })

  it('does not call room.disconnect() when no engine outcome is published', () => {
    setOutcome(null)
    mount()
    expect(room.disconnect).not.toHaveBeenCalled()
  })
})
