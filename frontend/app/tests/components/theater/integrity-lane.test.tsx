// tests/components/theater/integrity-lane.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { IntegrityLane } from '@/components/dashboard/reports/theater/IntegrityLane'
import type { FlagMarker } from '@/components/dashboard/reports/theater/timeline-model'

function flag(over: Partial<FlagMarker>): FlagMarker {
  return {
    kind: 'down_glance',
    startMs: 1000,
    endMs: 2000,
    confidence: 0.6,
    thumbnailUrl: null,
    positionPct: 10,
    ...over,
  }
}

describe('IntegrityLane', () => {
  it('renders a clickable tick per flag and fires onSelectFlag', () => {
    const onSelect = vi.fn()
    const flags = [
      flag({ kind: 'down_glance', startMs: 1000, positionPct: 10 }),
      flag({ kind: 'off_screen_sustained', startMs: 5000, positionPct: 50 }),
      flag({ kind: 'down_glance', startMs: 8000, positionPct: 80 }),
    ]
    render(
      <IntegrityLane
        downBuckets={[0.2, 0.8]}
        offBuckets={[0, 0.5]}
        flags={flags}
        caption="⚠ HIGH RISK · 36% off-screen · 42 down-glances"
        onSelectFlag={onSelect}
      />,
    )
    const ticks = screen.getAllByRole('button')
    expect(ticks).toHaveLength(3)
    fireEvent.click(ticks[0])
    expect(onSelect).toHaveBeenCalledOnce()
  })

  it('renders the caption', () => {
    render(
      <IntegrityLane
        downBuckets={[0.2]}
        offBuckets={[0.1]}
        flags={[]}
        caption="⚠ HIGH RISK · 36% off-screen · 42 down-glances"
        onSelectFlag={vi.fn()}
      />,
    )
    expect(screen.getByText(/HIGH RISK/)).toBeTruthy()
  })

  it('renders nothing when there is no data', () => {
    const { container } = render(
      <IntegrityLane downBuckets={[]} offBuckets={[]} flags={[]} caption="" onSelectFlag={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })
})
