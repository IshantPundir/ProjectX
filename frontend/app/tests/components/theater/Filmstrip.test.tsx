import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Filmstrip } from '@/components/dashboard/reports/theater/Filmstrip'
import type { TimelineMarker } from '@/components/dashboard/reports/theater/timeline-model'

const markers: TimelineMarker[] = [
  { seq: 1, questionId: 'q1', title: 'Experience', statusBadge: 'passed', tone: 'ok',
    askedAtMs: 23_000, thumbnailUrl: 'https://x/q1.webp', positionPct: 10 },
  { seq: 2, questionId: 'q2', title: 'AI agent', statusBadge: 'failed_required', tone: 'danger',
    askedAtMs: null, thumbnailUrl: null, positionPct: null },
]

describe('Filmstrip', () => {
  it('renders one card per marker with a thumbnail when present', () => {
    render(<Filmstrip markers={markers} activeQuestionId={null} onSelect={() => {}} />)
    expect(screen.getByText('Experience')).toBeInTheDocument()
    expect(screen.getByText('AI agent')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Experience/i })).toHaveAttribute('src', 'https://x/q1.webp')
  })

  it('calls onSelect with the questionId on click', async () => {
    const onSelect = vi.fn()
    render(<Filmstrip markers={markers} activeQuestionId={null} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: /Experience/i }))
    expect(onSelect).toHaveBeenCalledWith('q1')
  })

  it('marks the active card', () => {
    render(<Filmstrip markers={markers} activeQuestionId="q1" onSelect={() => {}} />)
    expect(screen.getByRole('button', { name: /Experience/i })).toHaveAttribute('data-active', 'true')
  })
})
