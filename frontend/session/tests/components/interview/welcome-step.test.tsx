import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WelcomeStep } from '@/app/interview/[token]/WelcomeStep'
import { WelcomeView } from '@/components/interview/app/welcome-view'

vi.mock('@/components/agents-ui/aura', () => ({
  Aura: () => <div data-testid="aura-stub" />,
}))

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

describe('WelcomeView — proctoring disclosure', () => {
  const baseProps = {
    companyName: 'Acme',
    jobTitle: 'Engineer',
    durationMinutes: 30,
    startButtonText: 'Start interview',
    mode: 'start' as const,
    onStartCall: vi.fn(),
  }

  it('shows the camera-monitoring disclosure when proctoring is enabled', () => {
    render(<WelcomeView {...baseProps} proctored={true} />)
    expect(screen.getByText(/camera is monitored automatically/i)).toBeInTheDocument()
  })

  it('does not show the proctoring disclosure when proctoring is disabled', () => {
    render(<WelcomeView {...baseProps} proctored={false} />)
    expect(screen.queryByText(/camera is monitored automatically/i)).not.toBeInTheDocument()
  })
})
