import { describe, expect, it } from 'vitest'
import { makeReport } from './_fixture'

describe('makeReport fixture', () => {
  it('builds a ready borderline report with the new shape', () => {
    const r = makeReport()
    expect(r.verdict).toBe('borderline')
    expect(r.scores.overall.score).toBe(4.1)
    expect(r.decision.why_positive.title).toBeTruthy()
    expect(r.questions[0].status_badge).toBe('passed')
    expect(r.concerns.some((c) => c.severity === 'deal_breaker')).toBe(true)
  })
  it('applies overrides', () => {
    expect(makeReport({ verdict: 'reject' }).verdict).toBe('reject')
  })
})
