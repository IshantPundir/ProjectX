import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import { useProctoringController } from '@/components/interview/proctoring/use-proctoring-controller'

vi.mock('@livekit/components-react', () => ({
  useSessionContext: () => ({ end: vi.fn() }),
}))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))

afterEach(() => vi.restoreAllMocks())

const cfg = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('useProctoringController', () => {
  it('hard violation ends locally even if the POST rejects (fail-safe)', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockRejectedValue(new Error('offline'))
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated }),
    )
    await act(async () => {
      await result.current.report('devtools')
    })
    expect(onTerminated).toHaveBeenCalledWith('devtools')
  })

  it('soft violation terminates only when backend says terminated', async () => {
    const spy = vi
      .spyOn(candidateSessionApi, 'proctoringEvent')
      .mockResolvedValue({ terminated: true, violation_count: 4, soft_violation_count: 4 })
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated }),
    )
    await act(async () => {
      await result.current.report('keyboard')
    })
    expect(spy).toHaveBeenCalled()
    await waitFor(() => expect(onTerminated).toHaveBeenCalledWith('soft_threshold_exceeded'))
  })

  it('terminates only once', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: true, violation_count: 1, soft_violation_count: 0,
    })
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated }),
    )
    await act(async () => {
      await result.current.report('tab_switch')
      await result.current.report('fullscreen_abandoned')
    })
    expect(onTerminated).toHaveBeenCalledTimes(1)
  })
})

describe('useProctoringController — dry-run (terminate_enabled=false)', () => {
  const dryCfg = { ...cfg, terminate_enabled: false }

  it('does not terminate on a hard violation when termination is disabled', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 1, soft_violation_count: 0,
    })
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: dryCfg, onTerminated }),
    )
    await act(async () => { await result.current.report('devtools') })
    expect(onTerminated).not.toHaveBeenCalled()
  })

  it('keeps processing further violations after a hard one (no latch)', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 2, soft_violation_count: 1,
    })
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: dryCfg, onTerminated }),
    )
    await act(async () => { await result.current.report('devtools') })
    await act(async () => { await result.current.report('keyboard') })
    // The soft violation after the hard one still produced a notice (not blocked
    // by a latched terminatedRef).
    expect(result.current.notice).toMatchObject({ kind: 'keyboard' })
    expect(onTerminated).not.toHaveBeenCalled()
  })

  it('still terminates a hard violation when terminate_enabled is omitted (default)', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 1, soft_violation_count: 0,
    })
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated }),
    )
    await act(async () => { await result.current.report('devtools') })
    expect(onTerminated).toHaveBeenCalledWith('devtools')
  })
})

describe('useProctoringController — soft notice', () => {
  it('sets a notice and does NOT toast.warning on a soft violation', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 1, soft_violation_count: 1,
    })
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated: vi.fn() }),
    )
    await act(async () => {
      await result.current.report('keyboard')
    })
    expect(result.current.notice).toMatchObject({ kind: 'keyboard', softCount: 1, limit: 3 })
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('dismissNotice clears the notice', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 1, soft_violation_count: 1,
    })
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated: vi.fn() }),
    )
    await act(async () => { await result.current.report('keyboard') })
    act(() => { result.current.dismissNotice() })
    expect(result.current.notice).toBeNull()
  })

  it('accumulates softCount and bumps the notice key across successive soft violations', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 1, soft_violation_count: 1,
    })
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated: vi.fn() }),
    )
    await act(async () => { await result.current.report('keyboard') })
    const firstKey = result.current.notice!.key
    await act(async () => { await result.current.report('looking_away_sustained') })
    expect(result.current.notice).toMatchObject({ kind: 'looking_away_sustained', softCount: 2 })
    expect(result.current.notice!.key).toBeGreaterThan(firstKey)
  })
})
