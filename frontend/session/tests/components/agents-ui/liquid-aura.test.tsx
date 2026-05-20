import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// Mock the LiveKit hook so the component renders without a real room.
const multibandMock = vi.fn(() => [0.6])
vi.mock('@livekit/components-react', () => ({
  useMultibandTrackVolume: () => multibandMock(),
}))

import { LiquidAura } from '@/components/agents-ui/liquid-aura'

describe('LiquidAura', () => {
  it('renders with the current agent state as a data attribute', () => {
    render(<LiquidAura state="speaking" audioTrack={undefined} />)
    const el = screen.getByRole('img', { name: /interviewer/i })
    expect(el).toHaveAttribute('data-lk-state', 'speaking')
  })

  it('writes the smoothed amplitude to the --amp CSS variable', () => {
    multibandMock.mockReturnValueOnce([0.6])
    render(<LiquidAura state="speaking" audioTrack={undefined} />)
    const el = screen.getByRole('img', { name: /interviewer/i })
    const amp = Number((el as HTMLElement).style.getPropertyValue('--amp'))
    expect(amp).toBeGreaterThan(0)
    expect(amp).toBeLessThanOrEqual(1)
  })

  it('does not crash and reports zero amplitude when there is no audio track', () => {
    multibandMock.mockReturnValueOnce([])
    render(<LiquidAura state="listening" audioTrack={undefined} />)
    const el = screen.getByRole('img', { name: /interviewer/i })
    expect(el).toHaveAttribute('data-lk-state', 'listening')
    expect((el as HTMLElement).style.getPropertyValue('--amp')).toBe('0')
  })

  it('applies the mark size class when size="mark"', () => {
    render(<LiquidAura state="listening" audioTrack={undefined} size="mark" />)
    expect(screen.getByRole('img', { name: /interviewer/i })).toHaveAttribute('data-aura-size', 'mark')
  })
})
