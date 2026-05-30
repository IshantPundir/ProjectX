import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { NodeTrack } from '@/components/dashboard/reports/theater/NodeTrack'
import type { TimelineMarker } from '@/components/dashboard/reports/theater/timeline-model'

const markers: TimelineMarker[] = [
  { seq: 1, questionId: 'q1', title: 'A', statusBadge: 'passed', tone: 'ok',
    askedAtMs: 10_000, thumbnailUrl: null, positionPct: 10 },
  { seq: 2, questionId: 'q2', title: 'B', statusBadge: 'partial', tone: 'caution',
    askedAtMs: null, thumbnailUrl: null, positionPct: null },
]

describe('NodeTrack', () => {
  it('renders a node only for markers with a position', () => {
    render(<NodeTrack markers={markers} playheadPct={0} activeQuestionId={null} onSeekMs={() => {}} />)
    expect(screen.getAllByRole('button', { name: /jump to/i })).toHaveLength(1)
  })

  it('seeks to the marker asked_at_ms on node click', async () => {
    const onSeekMs = vi.fn()
    render(<NodeTrack markers={markers} playheadPct={0} activeQuestionId={null} onSeekMs={onSeekMs} />)
    await userEvent.click(screen.getByRole('button', { name: /jump to/i }))
    expect(onSeekMs).toHaveBeenCalledWith(10_000)
  })
})
