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
    isFullscreen: false,
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
    fireEvent.click(screen.getByLabelText('Enter fullscreen'))
    expect(fs).toHaveBeenCalledOnce()
  })

  it('calls toggleMute when the mute button is clicked', () => {
    const c = makeController()
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    fireEvent.click(screen.getByLabelText('Mute'))
    expect(c.toggleMute).toHaveBeenCalledOnce()
  })

  it('calls cycleRate when the speed button is clicked', () => {
    const c = makeController()
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    fireEvent.click(screen.getByLabelText('Playback speed: 1×'))
    expect(c.cycleRate).toHaveBeenCalledOnce()
  })

  it('calls setVolume when the volume slider changes', () => {
    const c = makeController()
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Volume'), { target: { value: '0.5' } })
    expect(c.setVolume).toHaveBeenCalledWith(0.5)
  })

  it('shows the unmute control and forces the volume slider to 0 when muted', () => {
    const c = makeController({ muted: true, volume: 0.7 })
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    expect(screen.getByLabelText('Unmute')).toBeTruthy()
    expect((screen.getByLabelText('Volume') as HTMLInputElement).value).toBe('0')
  })

  it('reflects the visible prop on the data-visible attribute', () => {
    const { container, rerender } = render(
      <VideoControls controller={makeController()} visible={false} onToggleFullscreen={vi.fn()} />,
    )
    expect((container.firstChild as HTMLElement).getAttribute('data-visible')).toBe('false')
    rerender(<VideoControls controller={makeController()} visible onToggleFullscreen={vi.fn()} />)
    expect((container.firstChild as HTMLElement).getAttribute('data-visible')).toBe('true')
  })

  it('labels the fullscreen button by state', () => {
    const c = makeController({ isFullscreen: true })
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    expect(screen.getByLabelText('Exit fullscreen')).toBeTruthy()
  })
})
