// tests/components/theater/filmstrip.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { Filmstrip } from '@/components/dashboard/reports/theater/Filmstrip'
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

describe('Filmstrip', () => {
  it('shows a thumbnail image when thumbnailUrl is set', () => {
    render(
      <Filmstrip
        markers={[marker({ thumbnailUrl: 'https://signed/q1.webp' })]}
        activeQuestionId={null}
        onSelect={vi.fn()}
      />,
    )
    const img = screen.getByRole('img')
    expect(img.getAttribute('src')).toBe('https://signed/q1.webp')
  })

  it('shows a tone placeholder (Q number, no img) when thumbnailUrl is null', () => {
    render(<Filmstrip markers={[marker({ thumbnailUrl: null })]} activeQuestionId={null} onSelect={vi.fn()} />)
    expect(screen.queryByRole('img')).toBeNull()
    // Q1 renders twice: the thumbnail-area placeholder + the body label row
    expect(screen.getAllByText('Q1')).toHaveLength(2)
  })

  it('marks a card non-seekable when askedAtMs is null', () => {
    render(
      <Filmstrip
        markers={[marker({ askedAtMs: null, positionPct: null })]}
        activeQuestionId={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByRole('button').getAttribute('data-seekable')).toBe('false')
  })

  it('still calls onSelect when a non-seekable card is clicked', () => {
    const onSelect = vi.fn()
    render(
      <Filmstrip
        markers={[marker({ askedAtMs: null, positionPct: null })]}
        activeQuestionId={null}
        onSelect={onSelect}
      />,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledWith('q1')
  })
})
