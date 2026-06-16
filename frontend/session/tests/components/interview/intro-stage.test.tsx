import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../_utils/render'
import { candidateSessionApi } from '@/lib/api/candidate-session'

// Stub the WebGL Aura hero so jsdom doesn't load the shader.
vi.mock('@/components/agents-ui/aura', () => ({ Aura: () => <div data-testid="hero-aura" /> }))

import { IntroStage } from '@/app/interview/[token]/IntroStage'

const baseProps = {
  token: 'tok',
  companyName: 'Acme',
  jobTitle: 'Backend Engineer',
  durationMinutes: 20,
  consentText: 'You consent to recording.',
  proctoringEnabled: true,
}

afterEach(() => vi.restoreAllMocks())

describe('IntroStage', () => {
  it('shows the screening title, duration, and the one-time-link warning', () => {
    renderWithProviders(<IntroStage {...baseProps} />)
    expect(screen.getByRole('heading', { name: /backend engineer/i })).toBeInTheDocument()
    expect(screen.getByText(/20 min/i)).toBeInTheDocument()
    expect(screen.getByText(/one-time link/i)).toBeInTheDocument()
    expect(screen.getByText(/meet arjun/i)).toBeInTheDocument()
  })

  it('fires POST /consent exactly once when "I\'m ready" is clicked', async () => {
    const user = userEvent.setup()
    const spy = vi.spyOn(candidateSessionApi, 'consent').mockResolvedValue(undefined)
    renderWithProviders(<IntroStage {...baseProps} />)
    await user.click(screen.getByRole('button', { name: /i'm ready/i }))
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1))
    expect(spy).toHaveBeenCalledWith('tok', expect.objectContaining({ consented: true }))
  })

  it('keeps the CTA enabled again after a consent error (no advance)', async () => {
    const user = userEvent.setup()
    vi.spyOn(candidateSessionApi, 'consent').mockRejectedValue(new Error('network'))
    renderWithProviders(<IntroStage {...baseProps} />)
    const cta = screen.getByRole('button', { name: /i'm ready/i })
    await user.click(cta)
    await waitFor(() => expect(cta).toBeEnabled())
  })

  it('omits the proctoring item when proctoringEnabled is false', () => {
    renderWithProviders(<IntroStage {...baseProps} proctoringEnabled={false} />)
    expect(screen.queryByText(/proctored screening/i)).not.toBeInTheDocument()
  })

  it('enters fullscreen on the "I\'m ready" click (same gesture as consent)', async () => {
    const user = userEvent.setup()
    const req = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(document.documentElement, 'requestFullscreen', {
      configurable: true,
      value: req,
    })
    vi.spyOn(candidateSessionApi, 'consent').mockResolvedValue(undefined)
    renderWithProviders(<IntroStage {...baseProps} />)
    await user.click(screen.getByRole('button', { name: /i'm ready/i }))
    expect(req).toHaveBeenCalledTimes(1)
  })
})
