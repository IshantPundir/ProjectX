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

import { ReadyStage } from '@/app/interview/[token]/ReadyStage'
import { usePreCheckFaceGate, type FaceGateState } from '@/components/interview/proctoring/use-precheck-face-gate'

const mockGate = vi.mocked(usePreCheckFaceGate)

function gate(partial: Partial<FaceGateState>): FaceGateState {
  return { ready: false, failed: false, faceCount: 0, boxes: [], frame: null, ...partial }
}

beforeEach(() => {
  mockGate.mockReturnValue(gate({}))
})
afterEach(() => vi.restoreAllMocks())

describe('ReadyStage', () => {
  it('disables Start while the vision model is still loading', async () => {
    mockGate.mockReturnValue(gate({ ready: false }))
    render(<ReadyStage onStart={vi.fn()} proctored={false} />)
    // Camera auto-starts; the model is still loading → Start stays disabled.
    await waitFor(() =>
      expect(screen.getByText(/loading the proctoring model/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /start interview/i })).toBeDisabled()
  })

  it('asks the candidate to position their face when no face is detected', async () => {
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 0 }))
    render(<ReadyStage onStart={vi.fn()} proctored={false} />)
    await waitFor(() =>
      expect(screen.getByText(/position your face in the frame/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /start interview/i })).toBeDisabled()
  })

  it('enables Start when exactly one face is detected, and starts on click', async () => {
    const onStart = vi.fn()
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 1 }))
    const user = userEvent.setup()
    render(<ReadyStage onStart={onStart} proctored={false} />)
    const start = screen.getByRole('button', { name: /start interview/i })
    // Camera goes live + the 400ms gate debounce settles to one face.
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  it('shows the multi-face warning and disables Start with more than one face', async () => {
    mockGate.mockReturnValue(gate({ ready: true, faceCount: 2 }))
    render(<ReadyStage onStart={vi.fn()} proctored={false} />)
    await waitFor(() =>
      expect(screen.getByRole('alertdialog', { name: /multiple people/i })).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /start interview/i })).toBeDisabled()
  })

  it('falls back to enabling Start if the vision model fails to load (no lockout)', async () => {
    mockGate.mockReturnValue(gate({ ready: false, failed: true }))
    render(<ReadyStage onStart={vi.fn()} proctored={false} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /start interview/i })).toBeEnabled(),
    )
  })

  it('shows a retry path when camera permission is denied', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockRejectedValueOnce(
      Object.assign(new Error('denied'), { name: 'NotAllowedError' }),
    )
    render(<ReadyStage onStart={vi.fn()} proctored={false} />)
    await waitFor(() => expect(screen.getByText(/permission denied/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
  })
})
