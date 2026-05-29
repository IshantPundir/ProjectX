import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { GazeWarningOverlay } from '@/components/interview/proctoring/GazeWarningOverlay'

describe('GazeWarningOverlay', () => {
  it('shows a multiple-faces warning', () => {
    render(<GazeWarningOverlay kind="multiple_faces" />)
    expect(screen.getByTestId('gaze-warning-overlay')).toBeInTheDocument()
    expect(screen.getByText(/multiple people detected/i)).toBeInTheDocument()
  })

  it('announces the look-at-screen warning via role=alert', () => {
    render(<GazeWarningOverlay kind="looking_away_sustained" />)
    expect(screen.getByRole('alert')).toHaveTextContent(/look at the screen/i)
  })

  it('shows a face-not-visible warning', () => {
    render(<GazeWarningOverlay kind="face_not_visible" />)
    expect(screen.getByText(/can.t see you/i)).toBeInTheDocument()
  })
})
