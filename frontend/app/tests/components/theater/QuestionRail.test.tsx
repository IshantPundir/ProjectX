import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'

import { QuestionRail } from '@/components/dashboard/reports/theater/QuestionRail'
import type { TimelineMarker } from '@/components/dashboard/reports/theater/timeline-model'

function marker(over: Partial<TimelineMarker>): TimelineMarker {
  return {
    seq: 1,
    questionId: 'q1',
    title: 'How many years of full-time experience?',
    statusBadge: 'passed',
    tone: 'ok',
    askedAtMs: 12_000,
    thumbnailUrl: null,
    positionPct: 20,
    ...over,
  }
}

describe('QuestionRail', () => {
  it('renders a clickable pill per marker with the title in its accessible name', () => {
    render(
      <QuestionRail
        markers={[marker({}), marker({ seq: 2, questionId: 'q2', title: 'Walk me through a tricky bug' })]}
        activeQuestionId={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /full-time experience/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /tricky bug/i })).toBeInTheDocument()
  })

  it('marks the active (playhead) pill via data-active', () => {
    render(
      <QuestionRail
        markers={[marker({}), marker({ seq: 2, questionId: 'q2' })]}
        activeQuestionId="q2"
        onSelect={vi.fn()}
      />,
    )
    const buttons = screen.getAllByRole('button')
    expect(buttons[0].getAttribute('data-active')).toBe('false')
    expect(buttons[1].getAttribute('data-active')).toBe('true')
  })

  it('marks a pill non-seekable when askedAtMs is null but still selectable', () => {
    const onSelect = vi.fn()
    render(
      <QuestionRail
        markers={[marker({ askedAtMs: null, positionPct: null })]}
        activeQuestionId={null}
        onSelect={onSelect}
      />,
    )
    const btn = screen.getByRole('button')
    expect(btn.getAttribute('data-seekable')).toBe('false')
    fireEvent.click(btn)
    expect(onSelect).toHaveBeenCalledWith('q1')
  })
})
