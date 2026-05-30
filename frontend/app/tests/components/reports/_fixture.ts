import type { ReportRead } from '@/lib/api/reports'

/** A complete, valid new-shape ReportRead for component tests. Override per test. */
export function makeReport(overrides: Partial<ReportRead> = {}): ReportRead {
  return {
    verdict: 'borderline',
    verdict_reason: 'Could not confirm a must-have.',
    overall_score: 41,
    overall_coverage: 0.47,
    overall_confidence: 'medium',
    decision: {
      headline: 'Credible baseline, but key requirements unproven.',
      why_positive: { title: 'Foundations are there', body: 'Meets the experience bar.' },
      why_negative: { title: 'But depth was not shown', body: 'Technical answers stayed thin.' },
    },
    scores: {
      overall: { score: 41, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium', coverage: 0.47 },
      technical: { score: 41, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium', coverage: 0.55 },
      behavioral: { score: null, tier_label: 'Not Assessed', tone: 'neutral', confidence: 'low', coverage: 0 },
      communication: { score: 70, tier_label: 'Meets Bar', tone: 'ok', confidence: 'medium', coverage: 1 },
    },
    quick_summary: 'This candidate sits right on the line.',
    strengths: [{ title: 'Meets the experience bar', detail: 'Around six years overall.' }],
    concerns: [
      { title: 'No core skill reached the bar', detail: 'Every technical answer stayed thin.', severity: 'major' },
      { title: 'A required skill is unproven', detail: 'Programming depth not shown.', severity: 'deal_breaker' },
    ],
    questions: [
      {
        seq: 1, question_id: 'q1', title: 'Experience & background',
        status_badge: 'passed', status_tone: 'ok',
        question_text: 'How many years of experience do you have?',
        candidate_quote: 'Around six years.', our_read: 'Comfortably clears the four-year minimum.',
        asked_at_ms: null, thumbnail_url: null,
      },
      {
        seq: 2, question_id: 'q2', title: 'API rate limits', status_badge: 'partial', status_tone: 'caution',
        question_text: 'How would you handle API rate limits?',
        candidate_quote: 'Track the call count and handle errors.', our_read: 'Right concerns, thin on strategy.',
        asked_at_ms: null, thumbnail_url: null,
      },
    ],
    methodology: {
      note: 'Reached 7 of 8 planned questions; closed normally.',
      charity_flags: ['A long mid-interview silence may be a technical issue — worth confirming.'],
    },
    signal_assessments: [
      {
        signal: '4+ years total professional experience', type: 'experience', weight: 3, knockout: true,
        priority: 'required', engine_state: 'sufficient', final_state: 'sufficient', grade: null, score: 70,
        evidence: ['Around six years.'], overridden: false, override_reason: null,
      },
    ],
    id: 'r1', session_id: 's1', status: 'ready', engine_version: 'v2', version: 1,
    scoring_manifest: {
      scorer_model: 'gpt-5.4', reasoning_effort: 'medium', prompt_version: 'v3',
      evidence_grounding_summary: null, generated_at: '2026-05-27T11:00:00Z', correlation_id: 'abcd1234',
    },
    human_decision: null, generated_at: '2026-05-27T11:00:00Z',
    ...overrides,
  }
}
