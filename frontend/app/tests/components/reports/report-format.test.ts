import { describe, expect, it } from 'vitest'
import {
  scoreToTen, formatTimestamp, verdictMeta, scoreBandTone, tierTone,
  severityMeta, statusBadgeMeta, confidenceLabel, TONE_INK,
} from '@/components/dashboard/reports/report-format'

describe('report-format', () => {
  it('scoreToTen', () => {
    expect(scoreToTen(41)).toBe('4.1')
    expect(scoreToTen(100)).toBe('10.0')
    expect(scoreToTen(null)).toBeNull()
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
  it('scoreBandTone + confidenceLabel still work (kept)', () => {
    expect(scoreBandTone(80)).toBe('ok')
    expect(scoreBandTone(null)).toBe('neutral')
    expect(confidenceLabel('high')).toBe('High')
    expect(formatTimestamp(90000)).toBe('01:30')
    expect(TONE_INK.ok).toMatch(/var\(--px-/)
  })
})
