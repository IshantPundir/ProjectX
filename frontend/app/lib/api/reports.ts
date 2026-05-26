import { ApiError, apiFetch } from './client'

// --- Enums (mirror app/modules/reporting/scoring/types.py) ---
export type Verdict = 'advance' | 'borderline' | 'reject'
export type Confidence = 'high' | 'medium' | 'low'
export type Opportunity = 'full' | 'partial' | 'none'
export type SignalState = 'excellent' | 'meets_bar' | 'below_bar' | 'not_assessed'
export type KnockoutStatus = 'passed' | 'failed' | 'insufficient'
export type QuestionLevel = 'below_bar' | 'meets_bar' | 'excellent' | 'not_assessed'
export type HumanDecisionValue = 'advance' | 'reject' | 'hold'

// --- Response shapes (mirror reporting/schemas.py::ReportRead) ---
export interface EvidenceOut {
  quote: string
  timestamp_ms: number
  question_id: string
  grounded: boolean
}

export interface SignalScorecard {
  value: string
  type: string
  weight: number
  knockout: boolean
  state: SignalState
  score: number | null
  opportunity: Opportunity | null
  evidence: EvidenceOut[]
  covered_by: string[]
}

export interface DimensionScoreOut {
  name: string
  score: number | null
  coverage: number
  confidence: Confidence
  note: string | null
}

export interface KnockoutResultOut {
  signal: string
  status: KnockoutStatus
  reason: string
  evidence: EvidenceOut[]
}

export interface QuestionScorecard {
  question_id: string
  question_text: string
  level: QuestionLevel
  evidence: EvidenceOut[]
  red_flags_hit: string[]
  probes_fired: number
  opportunity: Opportunity | null
}

export interface SummaryOut {
  headline: string
  strengths: string[]
  gaps: string[]
  rationale: string
}

export interface ScoringManifest {
  scorer_model: string | null
  reasoning_effort: string | null
  verbosity: string | null
  prompt_version: string | null
  prompt_cache_key: string | null
  scorer_code_version: string | null
  bank_id: string | null
  signal_snapshot_id: string | null
  n_samples: number | null
  cache_hit_rate: number | null
  evidence_grounding_summary: Record<string, unknown> | null
  generated_at: string | null
  correlation_id: string | null
}

export interface HumanDecision {
  decided_by: string
  decision: HumanDecisionValue
  rationale: string
  decided_at: string
}

export interface ReportRead {
  verdict: Verdict
  verdict_reason: string
  overall_score: number | null
  overall_coverage: number
  overall_confidence: Confidence
  dimension_scores: Record<string, DimensionScoreOut>
  knockout_results: KnockoutResultOut[]
  signal_scorecards: SignalScorecard[]
  question_scorecards: QuestionScorecard[]
  summary: SummaryOut
  id: string | null
  session_id: string | null
  status: 'pending' | 'generating' | 'ready' | 'failed'
  engine_version: string | null
  version: number
  scoring_manifest: ScoringManifest | null
  human_decision: HumanDecision | null
  generated_at: string | null
}

export interface HumanDecisionIn {
  decision: HumanDecisionValue
  rationale: string
}

// --- Envelope: the polling-friendly union ---
export type ReportEnvelope =
  | { state: 'ready'; report: ReportRead }
  | { state: 'pending'; status: 'pending' | 'generating' }
  | { state: 'noReport' }

export const reportsApi = {
  /**
   * GET /api/reports/session/{sessionId}.
   * 200 → ready; 202 → pending; 404 → noReport (caught, not thrown);
   * 403 → throws ApiError so the caller can render an access-denied state.
   */
  getBySession: async (
    token: string,
    sessionId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<ReportEnvelope> => {
    try {
      const body = await apiFetch<ReportRead | { status: 'pending' | 'generating' }>(
        `/api/reports/session/${sessionId}`,
        { token, signal: opts?.signal },
      )
      if ('verdict' in body) return { state: 'ready', report: body }
      return { state: 'pending', status: body.status }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return { state: 'noReport' }
      throw err
    }
  },

  regenerate: (token: string, sessionId: string): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(
      `/api/reports/session/${sessionId}/regenerate`,
      { token, method: 'POST', body: JSON.stringify({}) },
    ),

  recordDecision: (
    token: string,
    reportId: string,
    body: HumanDecisionIn,
  ): Promise<ReportRead> =>
    apiFetch<ReportRead>(`/api/reports/${reportId}/decision`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),
}
