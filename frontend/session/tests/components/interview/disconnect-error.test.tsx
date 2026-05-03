import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DisconnectError } from '@/components/interview/app/DisconnectError'

describe('DisconnectError', () => {
  it('renders CANDIDATE_UNRESPONSIVE copy', () => {
    const { container } = render(<DisconnectError code="CANDIDATE_UNRESPONSIVE" />)
    expect(container.textContent).toContain("We didn't hear from you")
    expect(container.textContent).toContain('contact your recruiter')
    expect(container.textContent).toContain('Error code: CANDIDATE_UNRESPONSIVE')
  })

  it('falls back to default copy for unknown code', () => {
    const { container } = render(<DisconnectError code="MYSTERY" />)
    expect(container.textContent).toContain('Session disconnected')
  })
})
