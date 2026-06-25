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

test('long signal name renders truncated label (≤17 chars, contains "…") and full name in <title>', () => {
  const longName = 'Incident, change, and knowledge management for EMM/MDM operations'
  render(
    <CompetencyRadar
      assessments={[
        sa(longName, 8),
        sa('Comms', 7),
        sa('Identity', 6),
      ]}
    />
  )

  // Find SVG <text> elements that have a <title> child — that's our truncated label
  const svgTexts = document.querySelectorAll('text')
  const truncatedTextEl = Array.from(svgTexts).find((el) => el.querySelector('title') !== null)
  expect(truncatedTextEl).toBeTruthy()

  // Extract only the direct text node content (not the <title> child's content)
  // by iterating childNodes and collecting TEXT_NODE values
  const directText = Array.from(truncatedTextEl!.childNodes)
    .filter((n) => n.nodeType === Node.TEXT_NODE)
    .map((n) => n.textContent ?? '')
    .join('')
  expect(directText.length).toBeLessThanOrEqual(17)
  expect(directText).toContain('…')

  // The <title> child of that text element must contain the full signal name
  const titleEl = truncatedTextEl!.querySelector('title')
  expect(titleEl).toBeTruthy()
  expect(titleEl?.textContent).toBe(longName)
})
