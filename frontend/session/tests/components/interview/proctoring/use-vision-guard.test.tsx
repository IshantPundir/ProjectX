import { renderHook, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { lmDetect, detDetect } = vi.hoisted(() => ({ lmDetect: vi.fn(), detDetect: vi.fn() }))

vi.mock('@/components/interview/proctoring/vision/face-landmarker', async () => {
  const actual = await vi.importActual<
    typeof import('@/components/interview/proctoring/vision/face-landmarker')
  >('@/components/interview/proctoring/vision/face-landmarker')
  return { ...actual, createFaceLandmarker: vi.fn().mockResolvedValue({ detectForVideo: lmDetect, close: vi.fn() }) }
})
vi.mock('@/components/interview/proctoring/vision/face-detector', async () => {
  const actual = await vi.importActual<
    typeof import('@/components/interview/proctoring/vision/face-detector')
  >('@/components/interview/proctoring/vision/face-detector')
  return { ...actual, createFaceDetector: vi.fn().mockResolvedValue({ detectForVideo: detDetect, close: vi.fn() }) }
})

const fakeTrack = { attach: vi.fn(), detach: vi.fn() }
vi.mock('@livekit/components-react', () => ({
  useLocalParticipant: () => ({
    localParticipant: { getTrackPublication: () => ({ track: fakeTrack, isMuted: false }) },
  }),
}))

import { useVisionGuard } from '@/components/interview/proctoring/use-vision-guard'

const IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
const c = Math.cos(Math.PI / 6)
const s = Math.sin(Math.PI / 6)
// Ry(+30deg) -> yaw +30 -> 'right'; Ry(-30deg) -> yaw -30 -> 'left' (both off-screen)
const RIGHT = [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]
const LEFT = [c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1]

function lmFrame(matrices: number[][]) {
  return {
    faceLandmarks: matrices.map(() => []),
    faceBlendshapes: matrices.map(() => ({ categories: [] })),
    facialTransformationMatrixes: matrices.map((data) => ({ data })),
  }
}
function detFrame(n: number) {
  return { detections: Array.from({ length: n }, () => ({ categories: [{ score: 0.9 }] })) }
}

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(HTMLMediaElement.prototype, 'readyState', { get: () => 4, configurable: true })
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) =>
    setTimeout(() => cb(performance.now()), 16) as unknown as number,
  )
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
  // sensible defaults; individual tests override
  lmDetect.mockReturnValue(lmFrame([IDENT]))
  detDetect.mockReturnValue(detFrame(1))
})

afterEach(() => {
  delete (HTMLMediaElement.prototype as unknown as { readyState?: unknown }).readyState
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  lmDetect.mockReset()
  detDetect.mockReset()
})

describe('useVisionGuard', () => {
  it('does nothing when not armed', () => {
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: false, onViolation }))
    expect(lmDetect).not.toHaveBeenCalled()
    expect(detDetect).not.toHaveBeenCalled()
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('a single forward-facing face is center gaze with no violation', async () => {
    const onViolation = vi.fn()
    const { result } = renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(800) })
    expect(result.current.signals.gazeZone).toBe('center')
    expect(result.current.signals.faceCount).toBe(1)
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('fires multiple_faces from the DETECTOR count, not the landmarker', async () => {
    lmDetect.mockReturnValue(lmFrame([IDENT])) // landmarker still sees one
    detDetect.mockReturnValue(detFrame(2)) // detector sees two
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(1000) })
    expect(onViolation).toHaveBeenCalledWith('multiple_faces')
  })

  it('fires face_not_visible when the detector sees zero faces', async () => {
    detDetect.mockReturnValue(detFrame(0))
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(onViolation).toHaveBeenCalledWith('face_not_visible')
  })

  it('fires looking_away_sustained when the head stays turned off-screen', async () => {
    lmDetect.mockReturnValue(lmFrame([RIGHT]))
    detDetect.mockReturnValue(detFrame(1))
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(1500) })
    expect(onViolation).toHaveBeenCalledWith('looking_away_sustained')
  })

  it('samples the detector far less often than the landmarker (throttle)', async () => {
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(2000) })
    // landmarker runs ~every 16ms; detector ~every 350ms.
    expect(detDetect.mock.calls.length).toBeLessThan(lmDetect.mock.calls.length)
    expect(detDetect.mock.calls.length).toBeLessThanOrEqual(8)
  })
})
