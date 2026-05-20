import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EndInterviewControl } from '@/components/interview/session/EndInterviewControl'

describe('EndInterviewControl', () => {
  it('opens a confirmation dialog and only ends after confirm', async () => {
    const user = userEvent.setup()
    const onEnd = vi.fn()
    render(<EndInterviewControl onEnd={onEnd} />)

    await user.click(screen.getByRole('button', { name: /end interview/i }))
    expect(screen.getByText(/you won't be able to rejoin/i)).toBeInTheDocument()
    expect(onEnd).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /^end$/i }))
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('does not end when cancelled', async () => {
    const user = userEvent.setup()
    const onEnd = vi.fn()
    render(<EndInterviewControl onEnd={onEnd} />)
    await user.click(screen.getByRole('button', { name: /end interview/i }))
    await user.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onEnd).not.toHaveBeenCalled()
  })
})
