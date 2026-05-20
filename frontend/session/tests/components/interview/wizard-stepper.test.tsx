import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WizardStepper } from '@/app/interview/[token]/WizardStepper'

describe('WizardStepper', () => {
  it('omits Verify when OTP is not required', () => {
    render(<WizardStepper current="consent" otpRequired={false} />)
    expect(screen.getByText('Consent')).toBeInTheDocument()
    expect(screen.getByText('Camera & mic')).toBeInTheDocument()
    expect(screen.queryByText('Verify')).not.toBeInTheDocument()
  })

  it('includes Verify when OTP is required', () => {
    render(<WizardStepper current="otp" otpRequired={true} />)
    expect(screen.getByText('Verify')).toBeInTheDocument()
  })

  it('marks the current step with aria-current', () => {
    render(<WizardStepper current="cam-mic" otpRequired={false} />)
    const active = screen.getByText('Camera & mic').closest('[data-step]')
    expect(active).toHaveAttribute('aria-current', 'step')
  })
})
