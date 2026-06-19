import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CompetencyRadar } from '@/components/dashboard/reports/CompetencyRadar'
import type { SignalAssessmentOut } from '@/lib/api/reports'

const sa = (signal: string, score: number, provenance = 'asked_directly'): SignalAssessmentOut => ({
  signal, type: 'competency', weight: 2, knockout: false, priority: 'required',
  provenance: provenance as SignalAssessmentOut['provenance'], level: 'solid', score,
  evidence: [], overridden: false, override_reason: null,
})

test('plots assessed signals as radar axes', () => {
  render(<CompetencyRadar assessments={[sa('Intune', 9), sa('Comms', 7), sa('Identity', 6)]} />)
  expect(screen.getByText('Intune')).toBeInTheDocument()
  expect(screen.getByText('Identity')).toBeInTheDocument()
})

test('falls back to bars under 3 assessed signals', () => {
  render(<CompetencyRadar assessments={[sa('Intune', 9), sa('Comms', 7)]} />)
  // bar fallback uses role="img" with a known label
  expect(screen.getByLabelText(/competency scores/i)).toBeInTheDocument()
})

test('excludes not_reached signals', () => {
  render(<CompetencyRadar assessments={[sa('A', 9), sa('B', 8), sa('C', 7), sa('D', 1, 'not_reached')]} />)
  expect(screen.queryByText('D')).not.toBeInTheDocument()
})
