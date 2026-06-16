import { render, screen, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CaptureCountdown } from '@/app/interview/[token]/CaptureCountdown'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('CaptureCountdown', () => {
  it('counts down and fires onComplete after the final tick', () => {
    const onComplete = vi.fn()
    const onAbort = vi.fn()
    render(<CaptureCountdown seconds={3} unstable={false} onComplete={onComplete} onAbort={onAbort} />)
    expect(screen.getByText('3')).toBeInTheDocument()
    // Advance one tick at a time so React can flush each state update between ticks.
    act(() => { vi.advanceTimersByTime(1000) })
    act(() => { vi.advanceTimersByTime(1000) })
    act(() => { vi.advanceTimersByTime(1000) })
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onAbort).not.toHaveBeenCalled()
  })

  it('aborts (and never completes) when unstable is true', () => {
    const onComplete = vi.fn()
    const onAbort = vi.fn()
    render(<CaptureCountdown seconds={3} unstable={true} onComplete={onComplete} onAbort={onAbort} />)
    act(() => { vi.advanceTimersByTime(3000) })
    expect(onAbort).toHaveBeenCalledTimes(1)
    expect(onComplete).not.toHaveBeenCalled()
  })
})
