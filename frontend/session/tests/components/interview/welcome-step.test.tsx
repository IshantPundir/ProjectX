import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WelcomeStep } from '@/app/interview/[token]/WelcomeStep'

describe('WelcomeStep', () => {
  it('sets expectations and begins on CTA click', async () => {
    const user = userEvent.setup()
    const onBegin = vi.fn()
    render(<WelcomeStep durationMinutes={20} onBegin={onBegin} />)
    expect(screen.getByText(/no trick questions/i)).toBeInTheDocument()
    expect(screen.getByText(/20 minutes/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /begin/i }))
    expect(onBegin).toHaveBeenCalledTimes(1)
  })
})
