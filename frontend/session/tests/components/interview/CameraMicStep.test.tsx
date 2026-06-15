/**
 * Coverage for CameraMicStep:
 *
 * 1. getUserMedia called with `audio: true` (default browser EC/NS/AGC on).
 * 2. NOISE_WARN_DBFS threshold: at -35 dBFS no warning; at -25 dBFS the
 *    "sounds noisy" copy appears.
 *
 * sampleNoiseFloorDbfs is mocked at the module boundary so tests are
 * not sensitive to AudioContext / requestAnimationFrame / performance.now
 * timing in jsdom (which is too unreliable for a 2-second sampling loop).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// vi.mock is hoisted to the top of the file at parse time by Vitest.
// The mock factory must be a function literal (no imports from the module
// under test are available inside the factory).
vi.mock('@/app/interview/[token]/sampleNoiseFloorDbfs', () => ({
  sampleNoiseFloorDbfs: vi.fn(),
}))

const { isMultiDisplay, subscribeDisplayChange } = vi.hoisted(() => ({
  isMultiDisplay: vi.fn((): boolean | null => null),
  subscribeDisplayChange: vi.fn((_cb: () => void) => () => {}),
}))
vi.mock('@/lib/proctoring/displays', () => ({ isMultiDisplay, subscribeDisplayChange }))

import { CameraMicStep } from '@/app/interview/[token]/CameraMicStep'
import { sampleNoiseFloorDbfs } from '@/app/interview/[token]/sampleNoiseFloorDbfs'

const mockSampleNoiseFloorDbfs = vi.mocked(sampleNoiseFloorDbfs)

function buildAudioTrack() {
  return {
    kind: 'audio',
    getSettings: () => ({}),
    stop: vi.fn(),
  } as unknown as MediaStreamTrack
}

function buildStream(audioTrack: MediaStreamTrack | null) {
  const tracks = audioTrack ? [audioTrack] : []
  return {
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((t) => t.kind === 'audio'),
    getVideoTracks: () => [],
  } as unknown as MediaStream
}

describe('CameraMicStep', () => {
  let getUserMediaMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    getUserMediaMock = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: getUserMediaMock,
        enumerateDevices: vi.fn().mockResolvedValue([]),
        addEventListener: () => {},
        removeEventListener: () => {},
      },
      writable: true,
      configurable: true,
    })
    // Default: silent room (-100 dBFS), well below the -30 warn threshold.
    mockSampleNoiseFloorDbfs.mockResolvedValue(-100)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('calls getUserMedia with default audio (browser EC/NS/AGC on)', async () => {
    getUserMediaMock.mockResolvedValueOnce(buildStream(buildAudioTrack()))

    render(<CameraMicStep onPass={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))

    await waitFor(() => {
      expect(getUserMediaMock).toHaveBeenCalledWith({
        video: true,
        audio: true,
      })
    })
  })

  it('shows no noisy warning at -35 dBFS and shows the warning at -25 dBFS', async () => {
    // Case A: -35 dBFS — below the -30 NOISE_WARN_DBFS threshold → no warning.
    {
      getUserMediaMock.mockResolvedValueOnce(buildStream(buildAudioTrack()))
      mockSampleNoiseFloorDbfs.mockResolvedValueOnce(-35)

      const { unmount } = render(<CameraMicStep onPass={() => {}} />)
      fireEvent.click(screen.getByRole('button', { name: /test camera/i }))

      await waitFor(() => {
        expect(
          screen.getByRole('button', { name: /continue/i }),
        ).toBeInTheDocument()
      })

      expect(screen.queryByText(/sounds noisy/i)).not.toBeInTheDocument()

      unmount()
    }

    // Case B: -25 dBFS — above the -30 NOISE_WARN_DBFS threshold → warning shown.
    {
      getUserMediaMock.mockResolvedValueOnce(buildStream(buildAudioTrack()))
      mockSampleNoiseFloorDbfs.mockResolvedValueOnce(-25)

      render(<CameraMicStep onPass={() => {}} />)
      fireEvent.click(screen.getByRole('button', { name: /test camera/i }))

      await waitFor(() => {
        expect(screen.getByText(/sounds noisy/i)).toBeInTheDocument()
      })
    }
  })
})

describe('CameraMicStep — multi-display gate', () => {
  let getUserMediaMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    getUserMediaMock = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: getUserMediaMock,
        enumerateDevices: vi.fn().mockResolvedValue([]),
        addEventListener: () => {},
        removeEventListener: () => {},
      },
      writable: true,
      configurable: true,
    })
    mockSampleNoiseFloorDbfs.mockResolvedValue(-100)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('blocks Continue when proctored and a second display is detected', async () => {
    isMultiDisplay.mockReturnValue(true)
    mockSampleNoiseFloorDbfs.mockResolvedValue(-45)
    getUserMediaMock.mockResolvedValueOnce(buildStream(buildAudioTrack()))
    render(<CameraMicStep onPass={vi.fn()} proctored />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))
    await waitFor(() => expect(screen.getByText(/disconnect additional displays/i)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /continue/i })).toBeNull()
  })

  it('allows Continue when proctored but single-display', async () => {
    isMultiDisplay.mockReturnValue(false)
    mockSampleNoiseFloorDbfs.mockResolvedValue(-45)
    getUserMediaMock.mockResolvedValueOnce(buildStream(buildAudioTrack()))
    render(<CameraMicStep onPass={vi.fn()} proctored />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /continue/i })).toBeInTheDocument())
  })

  it('does not gate when not proctored even if extended', async () => {
    isMultiDisplay.mockReturnValue(true)
    mockSampleNoiseFloorDbfs.mockResolvedValue(-45)
    getUserMediaMock.mockResolvedValueOnce(buildStream(buildAudioTrack()))
    render(<CameraMicStep onPass={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /continue/i })).toBeInTheDocument())
  })
})
