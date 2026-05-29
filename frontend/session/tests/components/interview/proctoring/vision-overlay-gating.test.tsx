import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Flag ON for this whole file — proves the overlay DOES render when the
// debug flag is set + proctoring is enabled. (The composition test covers
// the flag-OFF negative; env is a const, so the positive case needs its
// own module-scoped env mock here.)
vi.mock('@/lib/env', () => ({
  env: { NEXT_PUBLIC_API_URL: 'http://localhost:8000', NEXT_PUBLIC_PROCTORING_DEBUG: true },
}))
const endMock = vi.fn()
vi.mock('@livekit/components-react', () => ({
  useVoiceAssistant: () => ({ state: 'listening' }),
  useSessionContext: () => ({ end: endMock }),
  useLocalParticipant: () => ({
    localParticipant: { getTrackPublication: () => undefined },
  }),
}))
vi.mock('@/components/interview/proctoring/vision/face-landmarker', () => ({
  createFaceLandmarker: vi.fn().mockResolvedValue({ detectForVideo: vi.fn(), close: vi.fn() }),
  blendshape: () => 0,
}))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))

import { ProctoringGuard } from '@/components/interview/proctoring/ProctoringGuard'

afterEach(() => vi.restoreAllMocks())

const cfg = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('ProctoringGuard — debug overlay gating (flag ON)', () => {
  it('renders the vision debug overlay when the flag is set and proctoring is enabled', () => {
    render(
      <ProctoringGuard token="t" config={cfg} onTerminated={vi.fn()}>
        <div>child</div>
      </ProctoringGuard>,
    )
    expect(screen.getByTestId('vision-debug-overlay')).toBeInTheDocument()
  })

  it('still hides the overlay when proctoring is disabled, even with the flag on', () => {
    render(
      <ProctoringGuard token="t" config={{ ...cfg, enabled: false }} onTerminated={vi.fn()}>
        <div>child</div>
      </ProctoringGuard>,
    )
    expect(screen.queryByTestId('vision-debug-overlay')).toBeNull()
  })
})
