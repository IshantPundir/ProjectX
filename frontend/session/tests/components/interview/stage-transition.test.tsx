import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StageTransition } from '@/app/interview/[token]/StageTransition'
import { StageProgress } from '@/app/interview/[token]/StageProgress'

describe('StageTransition', () => {
  it('renders the active stage children', () => {
    render(
      <StageTransition stageKey="intro">
        <div>intro body</div>
      </StageTransition>,
    )
    expect(screen.getByText('intro body')).toBeInTheDocument()
  })
})

describe('StageProgress', () => {
  it('shows step position and total', () => {
    render(<StageProgress steps={['Welcome', 'Verify', 'Ready']} currentIndex={1} />)
    expect(screen.getByText(/step 2 of 3/i)).toBeInTheDocument()
  })

  it('marks the active dot with aria-current', () => {
    render(<StageProgress steps={['Welcome', 'Ready']} currentIndex={1} />)
    const current = screen.getByText('Ready').closest('li')
    expect(current).toHaveAttribute('aria-current', 'step')
  })
})
