import { describe, expect, it } from 'vitest'

import type { ProctoringFlaggedInterval, QuestionOut } from '@/lib/api/reports'
import {
  activeQuestionId,
  activeSegmentIndex,
  buildFlagMarkers,
  buildQuestionMarkers,
  buildRailMarkers,
  pickPosterUrl,
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
  it('spans each violation from its start to its end (widthPct)', () => {
    const out = buildFlagMarkers(flags, 1000, 3)
    const byKind = Object.fromEntries(out.map((f) => [f.kind, f]))
    // multiple_faces 900→1000ms = 90%→100% → 10% wide
    expect(byKind.multiple_faces.positionPct).toBeCloseTo(90)
    expect(byKind.multiple_faces.widthPct).toBeCloseTo(10)
    // off_screen_sustained 300→800ms = 30%→80% → 50% wide
    expect(byKind.off_screen_sustained.positionPct).toBeCloseTo(30)
    expect(byKind.off_screen_sustained.widthPct).toBeCloseTo(50)
  })
  it('clamps width to 0 when duration is unknown (no negative spans)', () => {
    const [f] = buildFlagMarkers([flags[0]], 0, 1)
    expect(f.widthPct).toBe(0)
  })
  it('empty flags → empty', () => {
    expect(buildFlagMarkers([], 1000, 6)).toEqual([])
  })
})

describe('buildRailMarkers', () => {
  it('drops not_attempted questions and orders the rest by asked_at_ms (ties by seq)', () => {
    const markers = buildQuestionMarkers(
      [
        q({ seq: 1, question_id: 'q1', status_badge: 'passed', asked_at_ms: 30_000 }),
        q({ seq: 2, question_id: 'q2', status_badge: 'not_attempted', asked_at_ms: null }),
        q({ seq: 3, question_id: 'q3', status_badge: 'partial', asked_at_ms: 9_000 }),
      ],
      120_000,
    )
    const rail = buildRailMarkers(markers)
    expect(rail.map((m) => m.questionId)).toEqual(['q3', 'q1'])
  })

  it('sorts unknown timings (null asked_at_ms) last', () => {
    const markers = buildQuestionMarkers(
      [
        q({ seq: 1, question_id: 'q1', status_badge: 'partial', asked_at_ms: null }),
        q({ seq: 2, question_id: 'q2', status_badge: 'passed', asked_at_ms: 5_000 }),
      ],
      120_000,
    )
    expect(buildRailMarkers(markers).map((m) => m.questionId)).toEqual(['q2', 'q1'])
  })

  it('does not mutate the input array', () => {
    const markers = buildQuestionMarkers(
      [
        q({ seq: 1, question_id: 'q1', asked_at_ms: 30_000 }),
        q({ seq: 2, question_id: 'q2', asked_at_ms: 5_000 }),
      ],
      120_000,
    )
    const before = markers.map((m) => m.questionId)
    buildRailMarkers(markers)
    expect(markers.map((m) => m.questionId)).toEqual(before)
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

describe('pickPosterUrl', () => {
  it('picks the qualifying question nearest the recording midpoint', () => {
    const questions = [
      q({ question_id: 'q1', asked_at_ms: 10_000, thumbnail_url: 'thumb-q1' }),
      q({ question_id: 'q2', asked_at_ms: 58_000, thumbnail_url: 'thumb-q2' }),
      q({ question_id: 'q3', asked_at_ms: 110_000, thumbnail_url: 'thumb-q3' }),
    ]
    // midpoint = 60_000 → q2 (58_000) is closest
    expect(pickPosterUrl(questions, 120_000)).toBe('thumb-q2')
  })

  it('ignores questions missing a thumbnail or asked_at_ms', () => {
    const questions = [
      // closest to midpoint but no thumbnail → ineligible
      q({ question_id: 'q1', asked_at_ms: 60_000, thumbnail_url: null }),
      // has thumbnail but no timing → ineligible
      q({ question_id: 'q2', asked_at_ms: null, thumbnail_url: 'thumb-q2' }),
      // fully qualifying, further from midpoint
      q({ question_id: 'q3', asked_at_ms: 20_000, thumbnail_url: 'thumb-q3' }),
    ]
    expect(pickPosterUrl(questions, 120_000)).toBe('thumb-q3')
  })

  it('returns null when no question qualifies', () => {
    const questions = [
      q({ asked_at_ms: 30_000, thumbnail_url: null }),
      q({ asked_at_ms: null, thumbnail_url: 'thumb' }),
    ]
    expect(pickPosterUrl(questions, 120_000)).toBeNull()
  })

  it('returns null when duration is missing or zero', () => {
    const questions = [q({ asked_at_ms: 30_000, thumbnail_url: 'thumb' })]
    expect(pickPosterUrl(questions, 0)).toBeNull()
    expect(pickPosterUrl(questions, Number.NaN)).toBeNull()
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
