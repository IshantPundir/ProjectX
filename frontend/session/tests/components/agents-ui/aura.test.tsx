import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// Stub the WebGL stock aura so jsdom never touches WebGL.
const stockMock = vi.fn((props: Record<string, unknown>) => (
  <div data-testid="stock-aura" data-color-shift={String(props.colorShift)} data-theme={String(props.themeMode)} role="img" aria-label="AI interviewer" />
))
vi.mock('@/components/agents-ui/agent-audio-visualizer-aura', () => ({
  AgentAudioVisualizerAura: (props: Record<string, unknown>) => stockMock(props),
}))
const reducedMock = vi.fn(() => false)
vi.mock('@/hooks/use-prefers-reduced-motion', () => ({
  usePrefersReducedMotion: () => reducedMock(),
}))

import { Aura } from '@/components/agents-ui/aura'

describe('Aura', () => {
  it('renders the stock shader with colorShift=2 and themeMode=light when motion is allowed', () => {
    reducedMock.mockReturnValue(false)
    render(<Aura state="speaking" audioTrack={undefined} size="xl" />)
    const el = screen.getByTestId('stock-aura')
    expect(el).toHaveAttribute('data-color-shift', '2')
    expect(el).toHaveAttribute('data-theme', 'light')
  })

  it('renders a static aura-mark fallback (no shader) under reduced motion', () => {
    reducedMock.mockReturnValue(true)
    const { container } = render(<Aura state="listening" audioTrack={undefined} size="xl" />)
    expect(screen.queryByTestId('stock-aura')).not.toBeInTheDocument()
    expect(container.querySelector('.aura-mark')).not.toBeNull()
    expect(screen.getByRole('img', { name: /interviewer/i })).toBeInTheDocument()
  })
})
