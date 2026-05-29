import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VisionDebugOverlay } from '@/components/interview/proctoring/VisionDebugOverlay'
import type { VisionSignals } from '@/components/interview/proctoring/vision/types'

const SIGNALS: VisionSignals = {
  faceCount: 2, pose: { yaw: 12.3, pitch: -4.1, roll: 1.0 },
  gazeZone: 'right', gazePoint: { x: 0.7, y: 0.4 },
  gazeTrail: [{ x: 0.6, y: 0.45 }, { x: 0.65, y: 0.42 }, { x: 0.7, y: 0.4 }],
  blinking: false, earValue: 0.12, quality: 'glasses_degraded', fps: 24.5,
}

describe('VisionDebugOverlay', () => {
  it('renders the key tracking signals', () => {
    render(<VisionDebugOverlay signals={SIGNALS} />)
    expect(screen.getByText(/faces:\s*2/i)).toBeInTheDocument()
    expect(screen.getByText(/zone:\s*right/i)).toBeInTheDocument()
    expect(screen.getByText(/glasses_degraded/i)).toBeInTheDocument()
    expect(screen.getByText(/yaw/i)).toBeInTheDocument()
  })

  it('renders the gaze pointer when a gazePoint is present', () => {
    render(<VisionDebugOverlay signals={SIGNALS} />)
    expect(screen.getByTestId('vision-gaze-dot')).toBeInTheDocument()
    expect(screen.getByTestId('vision-gaze-layer')).toBeInTheDocument()
  })

  it('omits the gaze layer when gazePoint is null', () => {
    render(<VisionDebugOverlay signals={{ ...SIGNALS, gazePoint: null }} />)
    expect(screen.queryByTestId('vision-gaze-layer')).toBeNull()
  })
})
