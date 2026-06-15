import { render, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import { ProctoringGuard } from '@/components/interview/proctoring/ProctoringGuard'

// Agent has NOT started speaking ('initializing'); the room IS connected.
const lkState = { voiceState: 'initializing', isConnected: true }
vi.mock('@livekit/components-react', () => ({
  useVoiceAssistant: () => ({ state: lkState.voiceState }),
  useSessionContext: () => ({ end: vi.fn(), isConnected: lkState.isConnected }),
  useLocalParticipant: () => ({ localParticipant: { getTrackPublication: () => undefined } }),
}))
vi.mock('@/components/interview/proctoring/vision/face-landmarker', () => ({
  createFaceLandmarker: vi.fn().mockResolvedValue({
    detectForVideo: vi.fn(() => ({ facialTransformationMatrixes: [], faceBlendshapes: [] })),
    close: vi.fn(),
  }),
  blendshape: () => 0,
}))
vi.mock('@/components/interview/proctoring/vision/face-detector', () => ({
  createFaceDetector: vi.fn().mockResolvedValue({
    detectForVideo: vi.fn(() => ({ detections: [] })),
    close: vi.fn(),
  }),
  summarizeDetections: () => ({ faceCount: 0, topConfidence: 0 }),
}))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/env', () => ({
  env: { NEXT_PUBLIC_API_URL: 'http://localhost:8000', NEXT_PUBLIC_PROCTORING_DEBUG: false },
}))

afterEach(() => {
  vi.restoreAllMocks()
  lkState.voiceState = 'initializing'
  lkState.isConnected = true
})

const cfg = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('ProctoringGuard two-tier arming', () => {
  it('arms the visibility (env) guard at connect even before the agent speaks', () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: true, violation_count: 1, soft_violation_count: 0,
    })
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    const onTerminated = vi.fn()
    render(
      <ProctoringGuard token="t" config={cfg} onTerminated={onTerminated}>
        <div>live</div>
      </ProctoringGuard>,
    )
    // No timer advance, agent never spoke — env guard is armed purely on connect.
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).toHaveBeenCalledWith('tab_switch')
  })

  it('does NOT arm env guards before the room is connected', () => {
    lkState.isConnected = false
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    const onTerminated = vi.fn()
    render(
      <ProctoringGuard token="t" config={cfg} onTerminated={onTerminated}>
        <div>live</div>
      </ProctoringGuard>,
    )
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).not.toHaveBeenCalled()
  })
})
