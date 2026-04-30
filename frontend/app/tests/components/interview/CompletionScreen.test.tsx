import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CompletionScreen } from '@/app/(interview)/interview/[token]/LiveSession/CompletionScreen'

describe('CompletionScreen', () => {
  it('renders the thank-you copy and contains no navigation links', () => {
    const { container } = render(<CompletionScreen />)
    expect(
      screen.getByText(/Thanks for completing your interview/i),
    ).toBeInTheDocument()
    expect(container.querySelectorAll('a')).toHaveLength(0)
  })
})
