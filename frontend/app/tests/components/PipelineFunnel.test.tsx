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
      include_stages: ['screen'],
      include_weights: [1, 2, 3],
      include_priority: ['required'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

describe('PipelineFunnel', () => {
  it('renders all stages in order', () => {
    const stages = [
      makeStage(0, 'Phone Screen'),
      makeStage(1, 'AI Interview'),
      makeStage(2, 'Panel'),
    ]
    render(<PipelineFunnel stages={stages} />)
    expect(screen.getByText('Phone Screen')).toBeInTheDocument()
    expect(screen.getByText('AI Interview')).toBeInTheDocument()
    expect(screen.getByText('Panel')).toBeInTheDocument()
  })

  it('calls onStageClick with the index when a stage is clicked', async () => {
    const user = userEvent.setup()
    const stages = [makeStage(0, 'Phone Screen'), makeStage(1, 'AI Interview')]
    const onClick = vi.fn()
    render(<PipelineFunnel stages={stages} onStageClick={onClick} />)
    await user.click(screen.getByText('AI Interview'))
    expect(onClick).toHaveBeenCalledWith(1)
  })

  it('renders nothing when stages is empty', () => {
    const { container } = render(<PipelineFunnel stages={[]} />)
    expect(container.firstChild?.childNodes.length).toBe(0)
  })
})
