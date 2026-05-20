import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const stageMock = vi.fn()
vi.mock('@/components/interview/app/hooks/use-stage-progress', () => ({
  useStageProgress: () => stageMock(),
}))

import { ProgressChip } from '@/components/interview/session/ProgressChip'

describe('ProgressChip', () => {
  it('renders the question label and clock when progress is available', () => {
    stageMock.mockReturnValue({ currentQuestion: 1, totalQuestions: 8, timeRemainingSeconds: 750 })
    render(<ProgressChip />)
    expect(screen.getByText(/Question 2 of 8/)).toBeInTheDocument()
    expect(screen.getByText(/12:30 left/)).toBeInTheDocument()
  })

  it('renders nothing when there is no progress yet', () => {
    stageMock.mockReturnValue(null)
    const { container } = render(<ProgressChip />)
    expect(container).toBeEmptyDOMElement()
  })
})
