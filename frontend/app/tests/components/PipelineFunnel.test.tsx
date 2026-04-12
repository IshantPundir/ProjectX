import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'

import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import type { PipelineStageInput } from '@/lib/api/pipelines'

function makeStage(position: number, name: string): PipelineStageInput {
  return {
    position,
    name,
    stage_type: 'phone_screen',
    duration_minutes: 10,
    difficulty: 'easy',
    signal_filter: {
      include_types: ['competency'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

describe('PipelineFunnel', () => {
  it('renders all stages in order', () => {
    const stages = [
      makeStage(0, 'Initial Screen'),
      makeStage(1, 'Deep Interview'),
      makeStage(2, 'Hiring Panel'),
    ]
    render(<PipelineFunnel stages={stages} />)
    expect(screen.getByText('Initial Screen')).toBeInTheDocument()
    expect(screen.getByText('Deep Interview')).toBeInTheDocument()
    expect(screen.getByText('Hiring Panel')).toBeInTheDocument()
  })

  it('calls onStageClick with the index when a stage is clicked', async () => {
    const user = userEvent.setup()
    const stages = [makeStage(0, 'Initial Screen'), makeStage(1, 'Deep Interview')]
    const onClick = vi.fn()
    render(<PipelineFunnel stages={stages} onStageClick={onClick} />)
    await user.click(screen.getByText('Deep Interview'))
    expect(onClick).toHaveBeenCalledWith(1)
  })

  it('renders nothing when stages is empty', () => {
    const { container } = render(<PipelineFunnel stages={[]} />)
    expect(container.firstChild?.childNodes.length).toBe(0)
  })
})
