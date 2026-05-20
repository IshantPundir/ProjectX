import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PreCheckResponse } from '@/lib/api/candidate-session'

// WizardShell drives its routing off the /pre-check hook; mock at that seam.
vi.mock('@/lib/hooks/use-candidate-session', () => ({
  useCandidateSession: vi.fn(),
}))

import { useCandidateSession } from '@/lib/hooks/use-candidate-session'
import { WizardShell } from '@/app/interview/[token]/WizardShell'

const BASE: PreCheckResponse = {
  session_id: 's',
  company_name: 'Acme',
  job_title: 'Engineer',
  stage_name: 'AI Interview',
  duration_minutes: 30,
  consent_text: 'consent',
  state: 'consented',
  otp_required: false,
  otp_verified_at: null,
  otp_issued_at: null,
  proctoring_enabled: true,
  proctoring_outcome: null,
}

function mockPreCheck(data: PreCheckResponse) {
  vi.mocked(useCandidateSession).mockReturnValue({
    data,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useCandidateSession>)
}

afterEach(() => vi.clearAllMocks())

describe('WizardShell — terminated session routing', () => {
  it('shows the proctoring-ended screen (not the cam/mic step) and names the reason', () => {
    mockPreCheck({ ...BASE, state: 'terminated', proctoring_outcome: 'tab_switch' })

    render(<WizardShell token="tok" />)

    // Terminal ended screen, with the specific reason.
    expect(screen.getByText('Your interview was ended.')).toBeInTheDocument()
    expect(screen.getByText(/switching tabs/)).toBeInTheDocument()
    // It must NOT fall through to the wizard / cam-mic step.
    expect(screen.queryByText('Camera & mic')).not.toBeInTheDocument()
  })

  it('falls back to a generic reason when proctoring_outcome is null', () => {
    mockPreCheck({ ...BASE, state: 'terminated', proctoring_outcome: null })

    render(<WizardShell token="tok" />)

    expect(screen.getByText('Your interview was ended.')).toBeInTheDocument()
    expect(screen.getByText(/a monitoring violation/)).toBeInTheDocument()
  })
})
