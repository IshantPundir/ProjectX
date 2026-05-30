// tests/components/theater/video-controls.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { clockFromSec } from '@/components/dashboard/reports/theater/useVideoController'
import { VideoControls } from '@/components/dashboard/reports/theater/VideoControls'
import type { VideoController } from '@/components/dashboard/reports/theater/useVideoController'

describe('clockFromSec', () => {
  it('formats seconds as m:ss and floors fractions', () => {
    expect(clockFromSec(0)).toBe('0:00')
    expect(clockFromSec(9.9)).toBe('0:09')
    expect(clockFromSec(75)).toBe('1:15')
  })
  it('guards NaN/negative to 0:00', () => {
    expect(clockFromSec(NaN)).toBe('0:00')
    expect(clockFromSec(-5)).toBe('0:00')
    expect(clockFromSec(Infinity)).toBe('0:00')
  })
})

function makeController(over: Partial<VideoController> = {}): VideoController {
  return {
    playing: false,
    currentSec: 75,
    durationSec: 251,
    bufferedSec: 100,
    volume: 1,
    muted: false,
    rate: 1,
    togglePlay: vi.fn(),
    seekToSec: vi.fn(),
    setVolume: vi.fn(),
    toggleMute: vi.fn(),
    cycleRate: vi.fn(),
    ...over,
  }
}

describe('VideoControls', () => {
  it('renders current and total time', () => {
    render(<VideoControls controller={makeController()} visible onToggleFullscreen={vi.fn()} />)
    expect(screen.getByText('1:15')).toBeTruthy()
    expect(screen.getByText('4:11')).toBeTruthy()
  })

  it('calls togglePlay when the play button is clicked', () => {
    const c = makeController()
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    fireEvent.click(screen.getByLabelText('Play'))
    expect(c.togglePlay).toHaveBeenCalledOnce()
  })

  it('calls seekToSec when the scrubber changes', () => {
    const c = makeController()
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Seek'), { target: { value: '120' } })
    expect(c.seekToSec).toHaveBeenCalledWith(120)
  })

  it('calls onToggleFullscreen', () => {
    const fs = vi.fn()
    render(<VideoControls controller={makeController()} visible onToggleFullscreen={fs} />)
    fireEvent.click(screen.getByLabelText('Fullscreen'))
    expect(fs).toHaveBeenCalledOnce()
  })
})
