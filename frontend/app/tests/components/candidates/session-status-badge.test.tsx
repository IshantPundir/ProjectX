import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { SessionStatusBadge } from '@/components/dashboard/candidates/SessionStatusBadge'

describe('SessionStatusBadge', () => {
  it.each([
    ['active', null, /live/i],
    ['completed', null, /completed/i],
    ['cancelled', null, /cancelled/i],
    [null, null, /not invited/i],
  ])('state=%s renders the right pill', (state, errorCode, pattern) => {
    render(<SessionStatusBadge state={state} errorCode={errorCode as string | null} />)
    expect(screen.getByText(pattern)).toBeInTheDocument()
  })

  it.each([
    ['engine_session_config_invalid', /configuration error/i],
    ['engine_company_profile_missing', /company profile incomplete/i],
    ['engine_question_bank_not_ready', /question bank not ready/i],
    ['engine_room_join_failed', /couldn['']t reach/i],
    ['engine_internal_error', /internal error/i],
    ['engine_unresponsive', /interview never started/i],
  ])('state=error error_code=%s renders Failed: <label>', (code, pattern) => {
    render(<SessionStatusBadge state="error" errorCode={code} />)
    // The badge text combines "Failed: " with the labeled code.
    expect(screen.getByText(new RegExp(`failed:\\s+${pattern.source}`, 'i'))).toBeInTheDocument()
  })

  it('state=error with unknown code falls back to generic "Failed"', () => {
    render(<SessionStatusBadge state="error" errorCode="future_unknown_code" />)
    // No suffix because labelForErrorCode returns plain "Failed".
    expect(screen.getByText(/^failed$/i)).toBeInTheDocument()
  })

  it('state=error with null error_code falls back to generic "Failed"', () => {
    render(<SessionStatusBadge state="error" errorCode={null} />)
    expect(screen.getByText(/^failed$/i)).toBeInTheDocument()
  })
})
