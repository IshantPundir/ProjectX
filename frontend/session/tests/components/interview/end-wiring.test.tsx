import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// No engine outcome published → lastOutcome stays null.
vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => [],
}))

import { OutcomeWatcher } from '@/components/interview/app/app'

/** Minimal Room stub with a Disconnected event we can fire. */
function makeRoom() {
  const handlers: Record<string, ((...a: unknown[]) => void)[]> = {}
  return {
    on(evt: string, cb: (...a: unknown[]) => void) { (handlers[evt] ??= []).push(cb) },
    off(evt: string, cb: (...a: unknown[]) => void) {
      handlers[evt] = (handlers[evt] ?? []).filter((h) => h !== cb)
    },
    disconnect: vi.fn(),
    emit(evt: string, ...args: unknown[]) { (handlers[evt] ?? []).forEach((h) => h(...args)) },
  }
}

describe('OutcomeWatcher — End-interview wiring', () => {
  it('routes a CLIENT_INITIATED disconnect (no engine outcome) to onCompleted', () => {
    const room = makeRoom()
    const onCompleted = vi.fn()
    const onError = vi.fn()
    render(<OutcomeWatcher room={room as never} onCompleted={onCompleted} onError={onError} />)

    // DisconnectReason.CLIENT_INITIATED === 1 (proto enum)
    room.emit('disconnected', 1)

    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })
})
