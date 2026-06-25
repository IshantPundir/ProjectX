import { describe, expect, it } from 'vitest'
import {
  scoreToTen, formatTen, formatTimestamp, verdictMeta, scoreBandTone, tierTone,
  severityMeta, statusBadgeMeta, confidenceLabel, TONE_INK,
} from '@/components/dashboard/reports/report-format'

describe('report-format', () => {
  it('scoreToTen (deprecated 0–100 ÷10 helper)', () => {
    expect(scoreToTen(41)).toBe('4.1')
    expect(scoreToTen(100)).toBe('10.0')
    expect(scoreToTen(null)).toBeNull()
  })
  it('formatTen — formats an already-0-10 value, no division', () => {
    expect(formatTen(4.1)).toBe('4.1')
    expect(formatTen(10)).toBe('10.0')
    expect(formatTen(0)).toBe('0.0')
    expect(formatTen(null)).toBeNull()
  })
  it('verdictMeta relabels to recruiter-facing words', () => {
    expect(verdictMeta('advance').label).toBe('Recommended')
    expect(verdictMeta('borderline').label).toBe('Borderline')
    expect(verdictMeta('reject').label).toBe('Not Recommended')
    expect(verdictMeta('advance').tone).toBe('ok')
    expect(verdictMeta('reject').tone).toBe('danger')
  })
  it('severityMeta maps severity to label + tone', () => {
    expect(severityMeta('deal_breaker')).toEqual({ label: 'Deal-breaker', tone: 'danger' })
    expect(severityMeta('major').tone).toBe('caution')
    expect(severityMeta('moderate').tone).toBe('neutral')
  })
  it('statusBadgeMeta maps each badge to label + tone', () => {
    expect(statusBadgeMeta('passed')).toEqual({ label: 'Passed', tone: 'ok' })
    expect(statusBadgeMeta('failed_required').tone).toBe('danger')
    expect(statusBadgeMeta('not_fully_assessed').label).toBe('Not fully assessed')
  })
  it('tierTone passes through valid tones, else neutral', () => {
    expect(tierTone('ok')).toBe('ok')
    expect(tierTone('danger')).toBe('danger')
    expect(tierTone('bogus')).toBe('neutral')
  })
  it('scoreBandTone uses 0–10 thresholds (6.5 / 4.0)', () => {
    expect(scoreBandTone(8)).toBe('ok')
    expect(scoreBandTone(6.5)).toBe('ok')
    expect(scoreBandTone(6.4)).toBe('caution')
    expect(scoreBandTone(4.0)).toBe('caution')
    expect(scoreBandTone(3.9)).toBe('danger')
    expect(scoreBandTone(0)).toBe('danger')
    expect(scoreBandTone(null)).toBe('neutral')
  })
  it('confidenceLabel + formatTimestamp + TONE_INK still work', () => {
    expect(confidenceLabel('high')).toBe('High')
    expect(formatTimestamp(90000)).toBe('01:30')
    expect(TONE_INK.ok).toMatch(/var\(--px-/)
  })
})
