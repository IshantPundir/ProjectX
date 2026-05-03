/**
 * Phase 6 — Server-authoritative audio coverage for CameraMicStep.
 *
 * Three cases:
 * 1. getUserMedia called with the constraint object disabling EC/NS/AGC.
 * 2. track.getSettings() divergence emits the structured log AND does
 *    not block the candidate (Continue button still appears).
 * 3. NOISE_WARN_DBFS threshold recalibration: at -28 dBFS no warning;
 *    at -15 dBFS the revised warning text appears.
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

import { CameraMicStep } from '@/app/interview/[token]/CameraMicStep'
import { sampleNoiseFloorDbfs } from '@/app/interview/[token]/sampleNoiseFloorDbfs'

const mockSampleNoiseFloorDbfs = vi.mocked(sampleNoiseFloorDbfs)

function buildAudioTrack(settings: Partial<MediaTrackSettings>) {
  return {
    kind: 'audio',
    getSettings: () => settings,
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

describe('CameraMicStep — Phase 6 audio constraints', () => {
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
    // Default: silent room (-100 dBFS), well below the -20 warn threshold.
    mockSampleNoiseFloorDbfs.mockResolvedValue(-100)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('calls getUserMedia with EC/NS/AGC explicitly disabled', async () => {
    const audioTrack = buildAudioTrack({
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    })
    getUserMediaMock.mockResolvedValueOnce(buildStream(audioTrack))

    render(<CameraMicStep onPass={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))

    await waitFor(() => {
      expect(getUserMediaMock).toHaveBeenCalledWith({
        video: true,
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      })
    })
  })

  it('logs cammic.constraints.diverged when the browser silently re-enables EC and continues', async () => {
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const audioTrack = buildAudioTrack({
      echoCancellation: true,  // browser ignored the request
      noiseSuppression: false,
      autoGainControl: false,
    })
    getUserMediaMock.mockResolvedValueOnce(buildStream(audioTrack))

    render(<CameraMicStep onPass={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))

    // Continue button must appear despite the divergence — session
    // continues regardless per the Phase 6 browser-divergence decision.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /continue/i })).toBeInTheDocument()
    })

    expect(consoleWarn).toHaveBeenCalledWith(
      'cammic.constraints.diverged',
      expect.objectContaining({
        requested: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
        applied: expect.objectContaining({
          echoCancellation: true,
        }),
      }),
    )

    consoleWarn.mockRestore()
  })

  it('shows no noisy warning at -28 dBFS (post-Phase-6 quiet) and shows the revised warning at -15 dBFS', async () => {
    // Case A: -28 dBFS — below the -20 NOISE_WARN_DBFS threshold → no warning.
    {
      const audioTrack = buildAudioTrack({
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      })
      getUserMediaMock.mockResolvedValueOnce(buildStream(audioTrack))
      mockSampleNoiseFloorDbfs.mockResolvedValueOnce(-28)

      const { unmount } = render(<CameraMicStep onPass={() => {}} />)
      fireEvent.click(screen.getByRole('button', { name: /test camera/i }))

      await waitFor(() => {
        expect(
          screen.getByRole('button', { name: /continue/i }),
        ).toBeInTheDocument()
      })

      // Pre-Phase-6 this would have been "noisy" (warning above -30
      // threshold); post-Phase-6 it is quiet (below -20 threshold).
      expect(screen.queryByText(/sounds noisy/i)).not.toBeInTheDocument()

      unmount()
    }

    // Case B: -15 dBFS — above the -20 NOISE_WARN_DBFS threshold → warning shown.
    {
      const audioTrack = buildAudioTrack({
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      })
      getUserMediaMock.mockResolvedValueOnce(buildStream(audioTrack))
      mockSampleNoiseFloorDbfs.mockResolvedValueOnce(-15)

      render(<CameraMicStep onPass={() => {}} />)
      fireEvent.click(screen.getByRole('button', { name: /test camera/i }))

      await waitFor(() => {
        expect(screen.getByText(/sounds noisy/i)).toBeInTheDocument()
      })
      // Revised copy must mention "raw room noise" — the load-bearing
      // string from Phase 6 spec §5.3.
      expect(screen.getByText(/raw room noise/i)).toBeInTheDocument()
    }
  })
})
