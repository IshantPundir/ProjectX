import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/app/interview/[token]/sampleNoiseFloorDbfs', () => ({
  sampleNoiseFloorDbfs: vi.fn().mockResolvedValue(-50),
}))
vi.mock('@/lib/proctoring/displays', () => ({
  isMultiDisplay: () => false,
  subscribeDisplayChange: () => () => {},
}))
vi.mock('@/components/interview/proctoring/use-precheck-face-gate', () => ({
  usePreCheckFaceGate: vi.fn(),
}))
vi.mock('@/hooks/use-fullscreen-lock', () => ({
  useFullscreenLock: () => ({ locked: true, enterFullscreen: vi.fn() }),
  requestAppFullscreen: vi.fn(),
}))
vi.mock('@/lib/capture-frame', () => ({
  captureVideoFrame: vi.fn().mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' })),
}))
// Drive the capture flow via buttons instead of real timers.
vi.mock('@/app/interview/[token]/CaptureCountdown', () => ({
  CaptureCountdown: ({
    onComplete,
    onAbort,
    unstable,
  }: {
    onComplete: () => void
    onAbort: () => void
    unstable: boolean
  }) => (
    <div data-testid="countdown" data-unstable={String(unstable)}>
      <button onClick={onComplete}>__complete__</button>
      <button onClick={onAbort}>__abort__</button>
    </div>
  ),
}))

import { ReadyStage } from '@/app/interview/[token]/ReadyStage'
import {
  usePreCheckFaceGate,
  type FaceGateState,
} from '@/components/interview/proctoring/use-precheck-face-gate'
import { candidateSessionApi } from '@/lib/api/candidate-session'

const mockGate = vi.mocked(usePreCheckFaceGate)

function gate(partial: Partial<FaceGateState>): FaceGateState {
  return { ready: false, failed: false, faceCount: 0, boxes: [], frame: null, ...partial }
}

beforeEach(() => mockGate.mockReturnValue(gate({})))
afterEach(() => vi.restoreAllMocks())

describe('ReadyStage', () => {
  it('disables Start while the vision model is still loading', async () => {
    mockGate.mockReturnValue(gate({ ready: false }))
    render(<ReadyStage token="tok" onStart={vi.fn()} proctored={false} />)
    await waitFor(() =>
      expect(screen.getByText(/loading the proctoring model/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /start interview/i })).toBeDisabled()
  })

  it('asks the candidate to position their face when no face is detected', async () => {
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 0 }))
    render(<ReadyStage token="tok" onStart={vi.fn()} proctored={false} />)
    await waitFor(() =>
      expect(screen.getByText(/position your face in the frame/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /start interview/i })).toBeDisabled()
  })

  it('shows the multi-face warning and disables Start with more than one face', async () => {
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 2 }))
    render(<ReadyStage token="tok" onStart={vi.fn()} proctored={false} />)
    await waitFor(() =>
      expect(screen.getByRole('alertdialog', { name: /multiple people/i })).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /start interview/i })).toBeDisabled()
  })

  it('runs the countdown, captures + uploads, then starts on success', async () => {
    const onStart = vi.fn()
    const upSpy = vi
      .spyOn(candidateSessionApi, 'uploadReferencePhoto')
      .mockResolvedValue(undefined)
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 1 }))
    const user = userEvent.setup()
    render(<ReadyStage token="tok" onStart={onStart} proctored={false} />)
    const start = screen.getByRole('button', { name: /start interview/i })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    // countdown overlay shown; complete it
    await user.click(await screen.findByText('__complete__'))
    await waitFor(() => expect(upSpy).toHaveBeenCalledWith('tok', expect.any(Blob)))
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1))
  })

  it('aborts the countdown (back to idle, no start) on a proctoring violation', async () => {
    const onStart = vi.fn()
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 1 }))
    const user = userEvent.setup()
    render(<ReadyStage token="tok" onStart={onStart} proctored={false} />)
    const start = screen.getByRole('button', { name: /start interview/i })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    await user.click(await screen.findByText('__abort__'))
    expect(onStart).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByText(/let.s try again/i)).toBeInTheDocument(),
    )
  })

  it('blocks with a retry when the upload fails, and retries on click', async () => {
    const onStart = vi.fn()
    const upSpy = vi
      .spyOn(candidateSessionApi, 'uploadReferencePhoto')
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(undefined)
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 1 }))
    const user = userEvent.setup()
    render(<ReadyStage token="tok" onStart={onStart} proctored={false} />)
    const start = screen.getByRole('button', { name: /start interview/i })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    await user.click(await screen.findByText('__complete__'))
    await waitFor(() => expect(screen.getByText(/couldn.t save your photo/i)).toBeInTheDocument())
    expect(onStart).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /try again/i }))
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1))
    expect(upSpy).toHaveBeenCalledTimes(2)
  })

  it('shows a retry path when camera permission is denied', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockRejectedValueOnce(
      Object.assign(new Error('denied'), { name: 'NotAllowedError' }),
    )
    render(<ReadyStage token="tok" onStart={vi.fn()} proctored={false} />)
    await waitFor(() => expect(screen.getByText(/permission denied/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
  })
})
