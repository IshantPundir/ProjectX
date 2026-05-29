import { renderHook, act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// --- Mock the MediaPipe loader so no real WASM/model is needed ---
// vi.mock is hoisted to the top of the file, so detectForVideo must be
// declared with vi.hoisted() to be accessible inside the factory.
const { detectForVideo } = vi.hoisted(() => ({ detectForVideo: vi.fn() }))
vi.mock('@/components/interview/proctoring/vision/face-landmarker', async () => {
  const actual = await vi.importActual<typeof import('@/components/interview/proctoring/vision/face-landmarker')>(
    '@/components/interview/proctoring/vision/face-landmarker',
  )
  return {
    ...actual,
    createFaceLandmarker: vi.fn().mockResolvedValue({ detectForVideo, close: vi.fn() }),
  }
})

// --- Mock the LiveKit local camera track ---
const fakeTrack = { attach: vi.fn((el: HTMLVideoElement) => el), detach: vi.fn() }
vi.mock('@livekit/components-react', () => ({
  useLocalParticipant: () => ({
    localParticipant: {
      getTrackPublication: () => ({ track: fakeTrack, isMuted: false }),
    },
  }),
}))

import { useVisionGuard } from '@/components/interview/proctoring/use-vision-guard'

function oneFaceCenter() {
  return {
    faceLandmarks: [[]],
    faceBlendshapes: [{ categories: [] }],
    facialTransformationMatrixes: [{ data: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] }],
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    return setTimeout(() => cb(performance.now()), 16) as unknown as number
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  detectForVideo.mockReset()
})

describe('useVisionGuard', () => {
  it('does nothing when not armed', () => {
    const onNudge = vi.fn()
    renderHook(() => useVisionGuard({ armed: false, onNudge }))
    expect(detectForVideo).not.toHaveBeenCalled()
  })

  it('produces center-gaze signals for a single forward-facing face', async () => {
    detectForVideo.mockReturnValue(oneFaceCenter())
    const onNudge = vi.fn()
    const { result } = renderHook(() => useVisionGuard({ armed: true, onNudge }))

    // Step 1: flush the createFaceLandmarker Promise (sets landmarker + schedules first rAF tick).
    // Step 2: advance timers so the rAF setTimeout(16) fires (tick → detectForVideo → setSignals).
    // Both steps must be inside a single async act so React flushes state updates atomically.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      vi.advanceTimersByTime(32)
    })

    expect(detectForVideo).toHaveBeenCalled()
    expect(result.current.signals.faceCount).toBe(1)
    expect(result.current.signals.gazeZone).toBe('center')
    expect(onNudge).not.toHaveBeenCalled()
  })

  it('fires multiple_faces when a second face persists past the sustain window', async () => {
    detectForVideo.mockReturnValue({
      faceLandmarks: [[], []],
      faceBlendshapes: [{ categories: [] }, { categories: [] }],
      facialTransformationMatrixes: [
        { data: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] },
        { data: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] },
      ],
    })
    const onNudge = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onNudge }))

    // Flush Promise, then advance past the multiple_faces sustain window (2000ms)
    // Using many small steps so requestAnimationFrame fires repeatedly
    await act(async () => {
      await Promise.resolve()
      // Advance in 16ms steps to simulate rAF ticks across the 2000ms sustain window
      vi.advanceTimersByTime(2100)
    })

    expect(onNudge).toHaveBeenCalledWith('multiple_faces')
  })
})
