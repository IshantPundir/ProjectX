import { render, screen, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import { ProctoringGuard } from '@/components/interview/proctoring/ProctoringGuard'

const voiceState = 'listening'
const endMock = vi.fn()
vi.mock('@livekit/components-react', () => ({
  useVoiceAssistant: () => ({ state: voiceState }),
  useSessionContext: () => ({ end: endMock }),
}))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  endMock.mockClear()
})

const cfg = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('ProctoringGuard composition', () => {
  it('a hard violation (tab switch) terminates the session', () => {
    vi.useFakeTimers()
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: true, violation_count: 1, soft_violation_count: 0,
    })
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    const onTerminated = vi.fn()

    render(
      <ProctoringGuard token="t" config={cfg} onTerminated={onTerminated}>
        <div>live interview</div>
      </ProctoringGuard>,
    )
    act(() => { vi.advanceTimersByTime(900) })
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).toHaveBeenCalledWith('tab_switch')
    expect(endMock).toHaveBeenCalled()
  })

  it('negative control: with proctoring disabled, no listeners terminate the session', () => {
    vi.useFakeTimers()
    const onTerminated = vi.fn()
    render(
      <ProctoringGuard token="t" config={{ ...cfg, enabled: false }} onTerminated={onTerminated}>
        <div>live interview</div>
      </ProctoringGuard>,
    )
    act(() => { vi.advanceTimersByTime(2000) })
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).not.toHaveBeenCalled()
    expect(screen.getByText('live interview')).toBeInTheDocument()
  })
})
