import { renderHook, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { detectForVideo } = vi.hoisted(() => ({ detectForVideo: vi.fn() }))
vi.mock('@/components/interview/proctoring/vision/face-landmarker', async () => {
  const actual = await vi.importActual<
    typeof import('@/components/interview/proctoring/vision/face-landmarker')
  >('@/components/interview/proctoring/vision/face-landmarker')
  return {
    ...actual,
    createFaceLandmarker: vi.fn().mockResolvedValue({ detectForVideo, close: vi.fn() }),
  }
})

const fakeTrack = { attach: vi.fn(), detach: vi.fn() }
vi.mock('@livekit/components-react', () => ({
  useLocalParticipant: () => ({
    localParticipant: { getTrackPublication: () => ({ track: fakeTrack, isMuted: false }) },
  }),
}))

import { useVisionGuard } from '@/components/interview/proctoring/use-vision-guard'

const IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
// Ry(30deg) column-major -> yaw +30 -> classified 'right' (off-screen)
const c = Math.cos(Math.PI / 6)
const s = Math.sin(Math.PI / 6)
const RIGHT = [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]

function frame(matrices: number[][]) {
  return {
    faceLandmarks: matrices.map(() => []),
    faceBlendshapes: matrices.map(() => ({ categories: [] })),
    facialTransformationMatrixes: matrices.map((data) => ({ data })),
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(HTMLMediaElement.prototype, 'readyState', { get: () => 4, configurable: true })
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) =>
    setTimeout(() => cb(performance.now()), 16) as unknown as number,
  )
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
})

afterEach(() => {
  delete (HTMLMediaElement.prototype as unknown as { readyState?: unknown }).readyState
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  detectForVideo.mockReset()
})

describe('useVisionGuard', () => {
  it('does nothing when not armed', () => {
    renderHook(() => useVisionGuard({ armed: false }))
    expect(detectForVideo).not.toHaveBeenCalled()
  })

  it('reports a single forward-facing face as center gaze with no warning', async () => {
    detectForVideo.mockReturnValue(frame([IDENT]))
    const { result } = renderHook(() => useVisionGuard({ armed: true }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(300) })
    expect(result.current.signals.faceCount).toBe(1)
    expect(result.current.signals.gazeZone).toBe('center')
    expect(result.current.warning).toBeNull()
  })

  it('warns multiple_faces once a second face persists past the sustain window', async () => {
    detectForVideo.mockReturnValue(frame([IDENT, IDENT]))
    const { result } = renderHook(() => useVisionGuard({ armed: true }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(2500) })
    expect(result.current.warning).toBe('multiple_faces')
  })

  it('warns looking_away_sustained when the head stays turned off-screen', async () => {
    detectForVideo.mockReturnValue(frame([RIGHT]))
    const { result } = renderHook(() => useVisionGuard({ armed: true }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(4500) })
    expect(result.current.signals.gazeZone).toBe('right')
    expect(result.current.warning).toBe('looking_away_sustained')
  })
})
