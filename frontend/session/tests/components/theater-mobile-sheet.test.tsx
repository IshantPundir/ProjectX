import { it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

import { TheaterMobileSheet } from '@/components/recordings/theater/TheaterMobileSheet'
import type { ReportRead } from '@/components/recordings/api/reports'
import type { TimelineMarker } from '@/components/recordings/theater/timeline-model'

afterEach(cleanup)

const report = {
  session_id: 's1',
  verdict: 'advance',
  scores: { overall: { score: 8, tone: 'strong' }, technical: { score: 7, tone: 'solid' } },
  decision: null,
  questions: [],
} as unknown as ReportRead

const markers: TimelineMarker[] = [
  { seq: 1, questionId: 'q1', title: 'Tell me about X', statusBadge: 'passed', tone: 'ok', askedAtMs: 1000, thumbnailUrl: null, positionPct: 10 },
]

it('renders the question list when open and fires onSelectQuestion', () => {
  const onSelect = vi.fn()
  render(
    <TheaterMobileSheet
      open
      onClose={() => {}}
      report={report}
      railMarkers={markers}
      activeQuestionId={null}
      selection={null}
      offScreenPct={null}
      onSelectQuestion={onSelect}
      onJump={() => {}}
    />,
  )
  const q = screen.getByRole('button', { name: /Tell me about X/i })
  fireEvent.click(q)
  expect(onSelect).toHaveBeenCalledWith('q1')
})

it('does not render sheet content when closed', () => {
  render(
    <TheaterMobileSheet
      open={false}
      onClose={() => {}}
      report={report}
      railMarkers={markers}
      activeQuestionId={null}
      selection={null}
      offScreenPct={null}
      onSelectQuestion={() => {}}
      onJump={() => {}}
    />,
  )
  expect(screen.queryByRole('button', { name: /Tell me about X/i })).toBeNull()
})
