// Superseded by integrity-lane.test.tsx — kept here to avoid a stale file
// but content is replaced to match the new IntegrityLane API.
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { IntegrityLane } from '@/components/dashboard/reports/theater/IntegrityLane'
import type { FlagMarker } from '@/components/dashboard/reports/theater/timeline-model'

const flags: FlagMarker[] = [
  { kind: 'off_screen_sustained', startMs: 5000, endMs: 6000, confidence: 0.65,
    thumbnailUrl: null, positionPct: 20 },
]

describe('IntegrityLane (legacy test file — see integrity-lane.test.tsx)', () => {
  it('renders the caption and a clickable flag marker', () => {
    render(
      <IntegrityLane
        downBuckets={[0.2, 0.8, 0.4, 0]}
        offBuckets={[0, 0.3, 0.6, 0]}
        flags={flags}
        caption="56% off-screen · 42 down-glances"
        onSelectFlag={() => {}}
      />,
    )
    expect(screen.getByText(/56% off-screen/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /off-screen/i })).toBeInTheDocument()
  })

  it('calls onSelectFlag with the flag on marker click', async () => {
    const onSelectFlag = vi.fn()
    render(
      <IntegrityLane
        downBuckets={[0.2]}
        offBuckets={[0.5]}
        flags={flags}
        caption=""
        onSelectFlag={onSelectFlag}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /off-screen/i }))
    expect(onSelectFlag).toHaveBeenCalledWith(flags[0])
  })

  it('renders nothing for an empty/absent lane', () => {
    const { container } = render(
      <IntegrityLane downBuckets={[]} offBuckets={[]} flags={[]} caption="" onSelectFlag={() => {}} />,
    )
    expect(container.firstChild).toBeNull()
  })
})
