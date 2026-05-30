import { describe, expect, it } from 'vitest'

import type { ProctoringFlaggedInterval, QuestionOut } from '@/lib/api/reports'
import {
  activeQuestionId,
  activeSegmentIndex,
  buildFlagMarkers,
  buildQuestionMarkers,
  densityBuckets,
} from '@/components/dashboard/reports/theater/timeline-model'

function q(partial: Partial<QuestionOut>): QuestionOut {
  return {
    seq: 1, question_id: 'q1', title: 'Q', status_badge: 'passed', status_tone: 'ok',
    question_text: 'Q?', candidate_quote: 'a', our_read: '', asked_at_ms: null,
    thumbnail_url: null, ...partial,
  }
}

describe('buildQuestionMarkers', () => {
  it('positions a question by asked_at_ms / duration', () => {
    const [m] = buildQuestionMarkers([q({ asked_at_ms: 30_000 })], 120_000)
    expect(m.positionPct).toBeCloseTo(25)
    expect(m.tone).toBe('ok')
  })
  it('null asked_at_ms → null position (filmstrip-only)', () => {
    const [m] = buildQuestionMarkers([q({ asked_at_ms: null })], 120_000)
    expect(m.positionPct).toBeNull()
  })
  it('zero/absent duration → null position (no divide-by-zero)', () => {
    const [m] = buildQuestionMarkers([q({ asked_at_ms: 30_000 })], 0)
    expect(m.positionPct).toBeNull()
  })
  it('maps status_badge to tone via statusBadgeMeta', () => {
    const [m] = buildQuestionMarkers([q({ status_badge: 'failed_required' })], 1000)
    expect(m.tone).toBe('danger')
  })
})

describe('buildFlagMarkers', () => {
  const flags: ProctoringFlaggedInterval[] = [
    { kind: 'down_glance', start_ms: 100, end_ms: 200, confidence: 0.6 },
    { kind: 'multiple_faces', start_ms: 900, end_ms: 1000, confidence: 0.9, thumbnail_url: 'u' },
    { kind: 'off_screen_sustained', start_ms: 300, end_ms: 800, confidence: 0.65 },
  ]
  it('selects top-N by severity then confidence and positions them', () => {
    const out = buildFlagMarkers(flags, 1000, 2)
    expect(out.map((f) => f.kind)).toEqual(['multiple_faces', 'off_screen_sustained'])
    expect(out[0].positionPct).toBeCloseTo(90)
    expect(out[0].thumbnailUrl).toBe('u')
  })
  it('empty flags → empty', () => {
    expect(buildFlagMarkers([], 1000, 6)).toEqual([])
  })
})

describe('densityBuckets', () => {
  it('marks buckets covered by a flag interval as hot', () => {
    const flags: ProctoringFlaggedInterval[] = [
      { kind: 'off_screen_sustained', start_ms: 0, end_ms: 1000, confidence: 0.65 },
    ]
    const out = densityBuckets(flags, 4000, 4)
    expect(out).toHaveLength(4)
    expect(out[0]).toBeGreaterThan(0)
    expect(out[3]).toBe(0)
  })
  it('zero duration → all-zero buckets of the requested length', () => {
    expect(densityBuckets([], 0, 4)).toEqual([0, 0, 0, 0])
  })
})

describe('activeQuestionId', () => {
  it('returns the latest question whose asked_at_ms <= currentMs', () => {
    const markers = buildQuestionMarkers(
      [q({ question_id: 'q1', asked_at_ms: 1000 }), q({ question_id: 'q2', asked_at_ms: 5000 })],
      10_000,
    )
    expect(activeQuestionId(markers, 4000)).toBe('q1')
    expect(activeQuestionId(markers, 6000)).toBe('q2')
    expect(activeQuestionId(markers, 0)).toBeNull()
  })
  it('ignores markers with null asked_at_ms', () => {
    const markers = buildQuestionMarkers([q({ question_id: 'q1', asked_at_ms: null })], 10_000)
    expect(activeQuestionId(markers, 9999)).toBeNull()
  })
})

describe('activeSegmentIndex', () => {
  it('returns the last segment whose t_ms <= currentMs', () => {
    const segs = [{ role: 'agent', text: 'a', t_ms: 0 }, { role: 'c', text: 'b', t_ms: 1000 }]
    expect(activeSegmentIndex(segs, 500)).toBe(0)
    expect(activeSegmentIndex(segs, 1500)).toBe(1)
    expect(activeSegmentIndex(segs, -1)).toBe(-1)
  })
})
