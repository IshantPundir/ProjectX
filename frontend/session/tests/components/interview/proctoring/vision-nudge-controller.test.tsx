import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

vi.mock('@livekit/components-react', () => ({ useSessionContext: () => ({ end: vi.fn() }) }))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))

import { candidateSessionApi } from '@/lib/api/candidate-session'
import { useProctoringController } from '@/components/interview/proctoring/use-proctoring-controller'

afterEach(() => vi.restoreAllMocks())

const CFG = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('controller.nudge (advisory)', () => {
  it('flashes soft + toasts but never POSTs or terminates', () => {
    const post = vi.spyOn(candidateSessionApi, 'proctoringEvent')
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: CFG, onTerminated }),
    )
    act(() => result.current.nudge('multiple_faces'))
    expect(result.current.flash?.tone).toBe('soft')
    expect(vi.mocked(toast.warning)).toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
    expect(onTerminated).not.toHaveBeenCalled()
  })
})
