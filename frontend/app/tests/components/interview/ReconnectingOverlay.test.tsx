import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const useSessionContextMock = vi.fn()

vi.mock('@livekit/components-react', () => ({
  useSessionContext: () => useSessionContextMock(),
}))

import { ReconnectingOverlay } from '@/components/interview/app/ReconnectingOverlay'

describe('ReconnectingOverlay', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('renders when reconnecting and clears when reconnected', () => {
    useSessionContextMock.mockReturnValue({ state: 'reconnecting' })
    const { rerender } = render(<ReconnectingOverlay onTimeout={() => {}} />)
    expect(screen.getByText(/Reconnecting/i)).toBeInTheDocument()

    useSessionContextMock.mockReturnValue({ state: 'connected' })
    rerender(<ReconnectingOverlay onTimeout={() => {}} />)
    expect(screen.queryByText(/Reconnecting/i)).toBeNull()
  })

  it('fires onTimeout after 30 seconds of reconnecting', () => {
    useSessionContextMock.mockReturnValue({ state: 'reconnecting' })
    const onTimeout = vi.fn()
    render(<ReconnectingOverlay onTimeout={onTimeout} />)
    act(() => {
      vi.advanceTimersByTime(30_000)
    })
    expect(onTimeout).toHaveBeenCalledTimes(1)
  })
})
