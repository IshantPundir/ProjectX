import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { ProctoringIntegrityPanel } from '@/components/dashboard/reports/ProctoringIntegrityPanel'
import * as hook from '@/lib/hooks/use-session-proctoring'

function mockAnalysis(over = {}) {
  return {
    status: 'ready', risk_band: 'medium',
    detector_summary: { off_screen_pct: 0.12, down_glance_count: 4, reading_sweep_intervals: 1, max_faces: 1, multi_face_intervals: [] },
    gaze_heatmap: { grid: Array.from({ length: 5 }, () => [0, 0, 1, 0, 0]), off_screen_timeline: [0.1] },
    flagged_intervals: [{ start_ms: 3000, end_ms: 5000, kind: 'off_screen_sustained', confidence: 0.65 }],
    gaze_signal_quality: 'good', unscorable_pct: 0.05, ...over,
  }
}

describe('ProctoringIntegrityPanel', () => {
  it('renders the band + "for review, not a decision" disclaimer and fires onSeek', () => {
    vi.spyOn(hook, 'useSessionProctoring').mockReturnValue({ data: mockAnalysis(), isLoading: false } as never)
    const onSeek = vi.fn()
    render(<ProctoringIntegrityPanel sessionId="s1" onSeek={onSeek} />)
    expect(screen.getByText(/for review/i)).toBeTruthy()
    expect(screen.getByText(/MEDIUM/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /jump to/i }))
    expect(onSeek).toHaveBeenCalledWith(3000)
  })

  it('shows insufficient-data state without a scary band', () => {
    vi.spyOn(hook, 'useSessionProctoring').mockReturnValue({
      data: mockAnalysis({ risk_band: 'insufficient_data', status: 'unscorable', gaze_signal_quality: 'unscorable' }),
      isLoading: false,
    } as never)
    render(<ProctoringIntegrityPanel sessionId="s1" onSeek={() => {}} />)
    expect(screen.getByText(/insufficient/i)).toBeTruthy()
  })
})
