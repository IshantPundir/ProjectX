import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@livekit/components-react', () => ({
  useParticipants: () => [
    {
      identity: 'agent-stub',
      attributes: {
        current_question_index: '2',
        total_questions: '9',
        time_remaining_seconds: '660',
      },
    },
  ],
}))

import { ProgressBanner } from '@/app/(interview)/interview/[token]/LiveSession/ProgressBanner'

describe('ProgressBanner', () => {
  it('renders Q3 of 9 with 11 min remaining when attributes are populated', () => {
    render(<ProgressBanner />)
    expect(
      screen.getByText(/Q3 of 9/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/11 min remaining/),
    ).toBeInTheDocument()
  })
})
