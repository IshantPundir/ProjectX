import { ApiError, apiFetch } from './client'

// --- Enums (mirror app/modules/reporting/scoring/types.py) ---
export type Verdict = 'advance' | 'borderline' | 'reject'        // enum unchanged; UI-relabeled
export type Confidence = 'high' | 'medium' | 'low'
export type Severity = 'deal_breaker' | 'major' | 'moderate'
export type StatusBadge =
  | 'passed' | 'partial' | 'failed_required'
  | 'not_demonstrated' | 'not_attempted' | 'not_fully_assessed'
export type HumanDecisionValue = 'advance' | 'reject' | 'hold'

// --- Response shapes (mirror reporting/schemas.py::ReportRead) ---
export interface WhyColumn { title: string; body: string }
export interface DecisionOut { headline: string; why_positive: WhyColumn; why_negative: WhyColumn }
export interface ScoreOut {
  score: number | null
  tier_label: string
  tone: string
  confidence: Confidence
  coverage: number
}
export interface StrengthOut { title: string; detail: string }
export interface ConcernOut { title: string; detail: string; severity: Severity }
export interface QuestionOut {
  seq: number
  question_id: string
  title: string
  status_badge: StatusBadge
  status_tone: string
  question_text: string
  candidate_quote: string
  our_read: string
}
export interface MethodologyOut { note: string; charity_flags: string[] }
export interface SignalAssessmentOut {
  signal: string
  type: string
  weight: number
  knockout: boolean
  priority: string
  engine_state: string
  final_state: string
  grade: string | null
  score: number | null
  evidence: string[]
  overridden: boolean
  override_reason: string | null
}

export interface ScoringManifest {
  scorer_model: string | null
  reasoning_effort: string | null
  prompt_version: string | null
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
  decision: DecisionOut
  scores: Record<string, ScoreOut>
  quick_summary: string
  strengths: StrengthOut[]
  concerns: ConcernOut[]
  questions: QuestionOut[]
  methodology: MethodologyOut
  signal_assessments: SignalAssessmentOut[]
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

export interface ReportIndexItem {
  session_id: string
  candidate_id: string | null
  candidate_name: string | null
  job_title: string | null
  stage_name: string | null
  completed_at: string | null
  report_status: 'none' | 'pending' | 'generating' | 'ready' | 'failed'
  verdict: Verdict | null
  overall_score: number | null
}

export interface ReportIndexPage {
  items: ReportIndexItem[]
  total: number
  offset: number
  limit: number
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

  list: (
    token: string,
    opts?: { offset?: number; limit?: number; signal?: AbortSignal },
  ): Promise<ReportIndexPage> => {
    const params = new URLSearchParams()
    if (opts?.offset != null) params.set('offset', String(opts.offset))
    if (opts?.limit != null) params.set('limit', String(opts.limit))
    const qs = params.toString()
    return apiFetch<ReportIndexPage>(`/api/reports${qs ? `?${qs}` : ''}`, {
      token,
      signal: opts?.signal,
    })
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
