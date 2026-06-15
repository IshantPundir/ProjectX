import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ReadyStage } from '@/app/interview/[token]/ReadyStage'

vi.mock('@/app/interview/[token]/sampleNoiseFloorDbfs', () => ({
  sampleNoiseFloorDbfs: vi.fn().mockResolvedValue(-50),
}))
vi.mock('@/lib/proctoring/displays', () => ({
  isMultiDisplay: () => false,
  subscribeDisplayChange: () => () => {},
}))

describe('ReadyStage', () => {
  it('keeps Start hidden until devices are tested, then starts on click', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn()
    render(<ReadyStage onStart={onStart} proctored={false} />)

    expect(screen.queryByRole('button', { name: /^start$/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /test camera & mic/i }))
    const start = await screen.findByRole('button', { name: /^start$/i })
    await user.click(start)
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  it('shows a retry path when permission is denied', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockRejectedValueOnce(
      Object.assign(new Error('denied'), { name: 'NotAllowedError' }),
    )
    render(<ReadyStage onStart={vi.fn()} proctored={false} />)
    await user.click(screen.getByRole('button', { name: /test camera & mic/i }))
    await waitFor(() => expect(screen.getByText(/permission denied/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
