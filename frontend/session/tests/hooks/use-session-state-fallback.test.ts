import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

import { useSessionStateFallback } from '@/components/interview/app/hooks/use-session-state-fallback'
import { candidateSessionApi } from '@/lib/api/candidate-session'

vi.mock('@/lib/api/candidate-session')

const mockedGetState = vi.mocked(candidateSessionApi.getState)

beforeEach(() => {
  // shouldAdvanceTime: true lets real-clock time pass (so waitFor's internal
  // setInterval fires), while still giving us vi.advanceTimersByTimeAsync()
  // to step through the hook's poll loop without actual waiting.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockedGetState.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useSessionStateFallback', () => {
  it('polls every 5 seconds while enabled', async () => {
    mockedGetState.mockResolvedValue({
      state: 'active',
      error_code: null,
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    renderHook(() => useSessionStateFallback('tok-1', true))

    // First tick is immediate.
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(1))

    await vi.advanceTimersByTimeAsync(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(2))

    await vi.advanceTimersByTimeAsync(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(3))
  })

  it('stops polling once a terminal state is seen', async () => {
    mockedGetState
      .mockResolvedValueOnce({
        state: 'active',
        error_code: null,
        state_changed_at: '2026-05-16T12:00:00Z',
      })
      .mockResolvedValueOnce({
        state: 'error',
        error_code: 'engine_internal_error',
        state_changed_at: '2026-05-16T12:00:05Z',
      })

    renderHook(() => useSessionStateFallback('tok-1', true))

    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(2))

    // Should NOT call again — terminal state stops the loop.
    await vi.advanceTimersByTimeAsync(5000)
    expect(mockedGetState).toHaveBeenCalledTimes(2)
  })

  it('surfaces error_code from the response', async () => {
    mockedGetState.mockResolvedValue({
      state: 'error',
      error_code: 'engine_session_config_invalid',
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    const { result } = renderHook(() => useSessionStateFallback('tok-1', true))
    await waitFor(() => {
      expect(result.current?.state).toBe('error')
      expect(result.current?.error_code).toBe('engine_session_config_invalid')
    })
  })

  it('keeps polling through network errors', async () => {
    mockedGetState
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({
        state: 'active',
        error_code: null,
        state_changed_at: '2026-05-16T12:00:05Z',
      })

    renderHook(() => useSessionStateFallback('tok-1', true))

    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(5000)
    await waitFor(() => expect(mockedGetState).toHaveBeenCalledTimes(2))
  })

  it('does not poll while disabled', () => {
    mockedGetState.mockResolvedValue({
      state: 'active',
      error_code: null,
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    renderHook(() => useSessionStateFallback('tok-1', false))
    expect(mockedGetState).not.toHaveBeenCalled()
  })
})
