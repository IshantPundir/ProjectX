import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { SessionErrorScreen } from '@/components/interview/app/session-error-screen'

describe('SessionErrorScreen', () => {
  it.each([
    ['engine_session_config_invalid', /configuration issue/i],
    ['engine_company_profile_missing', /isn['']t fully set up/i],
    ['engine_question_bank_not_ready', /isn['']t fully set up/i],
    ['engine_room_join_failed', /something went wrong/i],
    ['engine_internal_error', /something went wrong/i],
    ['engine_unresponsive', /didn['']t start/i],
  ])('renders the right copy for %s', (code, pattern) => {
    render(<SessionErrorScreen errorCode={code} sessionId="sess-1" />)
    expect(screen.getByText(pattern)).toBeInTheDocument()
  })

  it('renders fallback copy for unknown codes', () => {
    render(<SessionErrorScreen errorCode="future_code_not_yet_known" sessionId="sess-1" />)
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
  })

  it('renders fallback copy when errorCode is null (LK-attribute path)', () => {
    render(<SessionErrorScreen errorCode={null} sessionId="sess-1" />)
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
  })

  it('shows the session id in the footer for support correlation', () => {
    render(<SessionErrorScreen errorCode={null} sessionId="sess-12345" />)
    expect(screen.getByText(/sess-12345/)).toBeInTheDocument()
  })
})
