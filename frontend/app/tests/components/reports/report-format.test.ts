import { describe, expect, it } from 'vitest'
import {
  scoreToTen, formatTimestamp, verdictMeta, scoreBandTone,
  signalStateTone, knockoutStatusTone, confidenceLabel, TONE_INK,
} from '@/components/dashboard/reports/report-format'

describe('report-format', () => {
  it('scoreToTen: 36 -> "3.6", null -> null', () => {
    expect(scoreToTen(36)).toBe('3.6')
    expect(scoreToTen(100)).toBe('10.0')
    expect(scoreToTen(null)).toBeNull()
  })
  it('formatTimestamp: ms -> mm:ss', () => {
    expect(formatTimestamp(90000)).toBe('01:30')
    expect(formatTimestamp(0)).toBe('00:00')
    expect(formatTimestamp(252000)).toBe('04:12')
  })
  it('verdictMeta maps each verdict to a tone + label', () => {
    expect(verdictMeta('advance').tone).toBe('ok')
    expect(verdictMeta('borderline').tone).toBe('human')
    expect(verdictMeta('reject').tone).toBe('danger')
    expect(verdictMeta('borderline').label).toBe('Borderline')
  })
  it('scoreBandTone: >=75 ok, 55-74 caution, <55 danger, null neutral', () => {
    expect(scoreBandTone(80)).toBe('ok')
    expect(scoreBandTone(60)).toBe('caution')
    expect(scoreBandTone(30)).toBe('danger')
    expect(scoreBandTone(null)).toBe('neutral')
  })
  it('signalStateTone + knockoutStatusTone', () => {
    expect(signalStateTone('excellent')).toBe('ok')
    expect(signalStateTone('below_bar')).toBe('danger')
    expect(signalStateTone('not_assessed')).toBe('neutral')
    expect(knockoutStatusTone('passed')).toBe('ok')
    expect(knockoutStatusTone('failed')).toBe('danger')
    expect(knockoutStatusTone('insufficient')).toBe('caution')
  })
  it('confidenceLabel + TONE_INK has a var for every tone', () => {
    expect(confidenceLabel('high')).toBe('High')
    expect(TONE_INK.ok).toMatch(/var\(--px-/)
  })
})
