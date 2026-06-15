import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ConnectingView } from '@/components/interview/app/ConnectingView'

describe('ConnectingView', () => {
  it('shows a branded connecting message', () => {
    render(<ConnectingView />)
    expect(screen.getByText(/connecting you to your interview/i)).toBeInTheDocument()
  })
})
