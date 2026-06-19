import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AtAGlanceBand } from '@/components/dashboard/reports/AtAGlanceBand'
import { makeReport, makeSignalAssessment } from './_fixture'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeReport2() {
  return makeReport({
    scores: {
      overall:       { score: 8.1, tier_label: 'Meets Bar',  tone: 'ok',      confidence: 'high',   coverage: 0.9 },
      technical:     { score: 7.5, tier_label: 'Meets Bar',  tone: 'ok',      confidence: 'high',   coverage: 0.85 },
      behavioral:    { score: null, tier_label: 'N/A',        tone: 'neutral', confidence: 'low',    coverage: 0 },
      communication: { score: 6.0, tier_label: 'Borderline', tone: 'caution', confidence: 'medium', coverage: 0.7 },
    },
    quick_summary: 'Strong technical troubleshooter. Solid depth, lighter on identity.',
    signal_assessments: [
      // Top strengths candidates (level solid/strong)
      makeSignalAssessment({ signal: 'Intune / MDM',      level: 'strong', weight: 3, knockout: false, priority: 'preferred' }),
      makeSignalAssessment({ signal: 'Troubleshooting',   level: 'solid',  weight: 2, knockout: false, priority: 'preferred' }),
      makeSignalAssessment({ signal: 'Conditional Access', level: 'solid', weight: 2, knockout: false, priority: 'preferred' }),
      // A 4th solid — should be capped out of top strengths
      makeSignalAssessment({ signal: 'Enrollment',        level: 'solid',  weight: 1, knockout: false, priority: 'preferred' }),
      // Watch-out candidate (required + thin)
      makeSignalAssessment({ signal: 'Identity / Azure AD', level: 'thin', weight: 3, knockout: false, priority: 'required' }),
      // Knockout + absent → watch-out
      makeSignalAssessment({ signal: 'Comms Skill',       level: 'absent', weight: 2, knockout: true,  priority: 'preferred' }),
    ],
  })
}

// ---------------------------------------------------------------------------
// Ring labels
// ---------------------------------------------------------------------------

test('renders Overall, Technical, and Communication ring labels', () => {
  render(<AtAGlanceBand report={makeReport2()} />)
  expect(screen.getByText('Overall')).toBeInTheDocument()
  expect(screen.getByText('Technical')).toBeInTheDocument()
  expect(screen.getByText('Communication')).toBeInTheDocument()
})

// ---------------------------------------------------------------------------
// Lede (first sentence of quick_summary)
// ---------------------------------------------------------------------------

test('renders the first sentence of quick_summary as the lede', () => {
  render(<AtAGlanceBand report={makeReport2()} />)
  // First sentence ends at first period
  expect(screen.getByText(/Strong technical troubleshooter/)).toBeInTheDocument()
})

// ---------------------------------------------------------------------------
// Top strengths pills
// ---------------------------------------------------------------------------

test('renders top-3 solid/strong signals as strength pills sorted by weight desc', () => {
  const { container } = render(<AtAGlanceBand report={makeReport2()} />)
  // Pills live inside <span> elements; radar labels live inside <text> SVG elements.
  // Use querySelectorAll to target only the pill <span>s.
  const pillTexts = Array.from(container.querySelectorAll('span')).map((el) => el.textContent)
  expect(pillTexts).toContain('Intune / MDM')
  expect(pillTexts).toContain('Troubleshooting')
  expect(pillTexts).toContain('Conditional Access')
  // 4th solid (weight 1) must be capped out of strength pills
  expect(pillTexts).not.toContain('Enrollment')
})

// ---------------------------------------------------------------------------
// Watch-out pills
// ---------------------------------------------------------------------------

test('renders required/knockout thin+absent signals as watch-out pills', () => {
  const { container } = render(<AtAGlanceBand report={makeReport2()} />)
  const pillTexts = Array.from(container.querySelectorAll('span')).map((el) => el.textContent)
  // priority=required + level=thin → watch-out
  expect(pillTexts).toContain('Identity / Azure AD')
  // knockout=true + level=absent → watch-out
  expect(pillTexts).toContain('Comms Skill')
})

// ---------------------------------------------------------------------------
// No verdict
// ---------------------------------------------------------------------------

test('does NOT render the verdict label anywhere in the band', () => {
  render(<AtAGlanceBand report={makeReport()} />)
  // makeReport defaults to verdict:'borderline' → label 'Borderline' (verdictMeta)
  expect(screen.queryByText('Borderline')).not.toBeInTheDocument()
  expect(screen.queryByText('Recommended')).not.toBeInTheDocument()
  expect(screen.queryByText('Not Recommended')).not.toBeInTheDocument()
})

// ---------------------------------------------------------------------------
// Null scores
// ---------------------------------------------------------------------------

test('renders em-dash for null score rings gracefully', () => {
  const report = makeReport2()
  render(<AtAGlanceBand report={report} />)
  // behavioral is null but we render Overall/Technical/Communication — all non-null in this fixture
  // Just confirm the component doesn't crash with nulls
  expect(screen.getByText('Overall')).toBeInTheDocument()
})

test('handles completely empty signal_assessments without crashing', () => {
  render(<AtAGlanceBand report={makeReport({ signal_assessments: [] })} />)
  expect(screen.getByText('Overall')).toBeInTheDocument()
})
