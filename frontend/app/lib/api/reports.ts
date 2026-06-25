import { ApiError, apiFetch } from './client'
import type { ReelPlayback } from './reels'

// --- Enums (mirror app/modules/reporting/scoring/types.py) ---
export type Verdict = 'advance' | 'borderline' | 'reject'        // enum unchanged; UI-relabeled
export type Confidence = 'high' | 'medium' | 'low'
export type Severity = 'deal_breaker' | 'major' | 'moderate'
export type StatusBadge =
  | 'passed' | 'partial' | 'failed_required'
  | 'not_demonstrated' | 'not_attempted' | 'not_fully_assessed'
export type HumanDecisionValue = 'advance' | 'reject' | 'hold'

// --- Response shapes (mirror reporting/schemas.py::ReportRead) ---

export interface ReportHeader {
  candidate_name: string
  candidate_email: string | null
  job_title: string
  stage_label: string
  session_started_at: string | null
  duration_seconds: number | null
  skills: string[]
  reference_photo_url: string | null
}

export interface WhyColumn { title: string; body: string }
export interface DecisionOut { headline: string; why_positive: WhyColumn; why_negative: WhyColumn }
export interface ScoreOut {
  /** Dimension score on a 0–10 scale; null when not yet scored. */
  score: number | null
  tier_label: string
  tone: string
  confidence: Confidence
  coverage: number
  session_score?: number | null
  holistic_delta?: number | null
}
export interface StrengthOut { title: string; detail: string }
export interface ConcernOut { title: string; detail: string; severity: Severity }
export type QuestionLevel = 'strong' | 'solid' | 'thin' | 'absent' | 'not_reached'
export type QuestionDifficulty = 'easy' | 'medium' | 'hard'

export interface QuestionOut {
  seq: number
  question_id: string
  title: string
  status_badge: StatusBadge
  status_tone: string
  question_text: string
  candidate_quote: string
  our_read: string
  /** ms since session start; null for legacy sessions (engine tagged it). */
  asked_at_ms: number | null
  /** Presigned R2 GET for the question's video frame; null until generated. */
  thumbnail_url: string | null
  /** Rubric-anchored level for the question (bank card grading). */
  level?: QuestionLevel
  /** Engine per-question closure — satisfied|tapped_out|absent|truncated; null if never asked. */
  closure?: string | null
  /** Difficulty label from the bank card; null if not set. */
  difficulty?: QuestionDifficulty | null
  /** Bank card listen-for criteria that the candidate hit. */
  listen_for_hits?: string[]
  /** Bank card red-flag criteria that were tripped. */
  red_flags_tripped?: string[]
  /** Number of follow-up probes the engine used for this question. */
  probes_used?: number
  /** Total probes available for this question in the bank card. */
  probes_available?: number
  /** Rubric-anchored 0–10 score; null when not assessed. */
  score?: number | null
}
export interface MethodologyOut { note: string; charity_flags: string[] }
export interface SignalAssessmentOut {
  signal: string
  type: string
  weight: number
  knockout: boolean
  priority: string
  /** How the signal was covered during the session. */
  provenance: 'not_reached' | 'asked_directly' | 'cross_credited' | 'probed_absent'
  /** Rubric-anchored quality level for this signal. */
  level: QuestionLevel
  /** Rubric-anchored 0–10 score for this signal; null when not scored. */
  score: number | null
  evidence: string[]
  overridden: boolean
  override_reason: string | null
  /** True when a cross-signal credit bump was applied during scoring. */
  cross_credit_applied?: boolean
  /** Human-readable explanation of how the level was determined (e.g. "dedicated: thin; +1 cross-credit → solid"). */
  level_basis?: string
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
  header: ReportHeader | null
  verdict: Verdict
  verdict_reason: string
  /** Role-fit score on a 0–10 scale; null when not yet scored. */
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
  /** Presigned R2 GET for the candidate reference photo (captured on the camera
   *  step). Used as the main session/video poster. Null for sessions captured
   *  before the feature, or when the presign fails. */
  reference_photo_url: string | null
}

export interface HumanDecisionIn {
  decision: HumanDecisionValue
  rationale: string
}

export interface ShareReportResponse {
  share_id: string
  status: string
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

// --- Session recording playback ---
export type RecordingStatus = 'absent' | 'recording' | 'ready' | 'failed'

export interface RecordingTranscriptSegment {
  role: string
  text: string
  /** Milliseconds since interview start (engine timeline). */
  t_ms: number
}

export interface RecordingPlayback {
  status: RecordingStatus
  /** Short-lived presigned GET URL; only present when status === 'ready'. */
  signed_url: string | null
  expires_at: string | null
  duration_seconds: number | null
  /** Add to a transcript t_ms to map it onto the video timeline. */
  offset_ms: number
  transcript: RecordingTranscriptSegment[]
}

export type ProctoringStatus =
  | 'absent' | 'pending' | 'running' | 'ready' | 'failed' | 'unscorable'
export type RiskBand = 'low' | 'medium' | 'high' | 'insufficient_data'

export interface ProctoringFlaggedInterval {
  start_ms: number
  end_ms: number
  kind: string
  confidence: number
  /** Presigned R2 GET for the flag's video frame; present only for top flags. */
  thumbnail_url?: string | null
}

export interface ProctoringDetectorSummary {
  off_screen_pct: number
  down_glance_count: number
  reading_sweep_intervals: number
  max_faces: number
  multi_face_intervals: { start_ms: number; end_ms: number; max_faces: number }[]
}

export interface ProctoringHeatmap {
  grid: number[][]
  scorable_frames?: number
  off_screen_timeline: number[]
}

export interface ProctoringAnalysis {
  status: ProctoringStatus
  risk_band: RiskBand | null
  detector_summary: ProctoringDetectorSummary | null
  gaze_heatmap: ProctoringHeatmap | null
  flagged_intervals: ProctoringFlaggedInterval[]
  gaze_signal_quality: string | null
  unscorable_pct: number | null
}

/** Mirror of reporting/schemas.py::PublicRecordingsEnvelope. */
export interface PublicRecordingsEnvelope {
  candidate_name: string
  job_title: string
  stage_label: string
  report: ReportRead
  recording: RecordingPlayback
  proctoring: ProctoringAnalysis
  reel: ReelPlayback
}

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

  /**
   * GET /api/reports/session/{sessionId}/recording.
   * Returns playback status + (when ready) a short-lived signed URL and the
   * timestamped transcript. Pull-based: the backend reconciles egress status
   * on read, so poll while `status === 'recording'`.
   */
  getRecording: (
    token: string,
    sessionId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<RecordingPlayback> =>
    apiFetch<RecordingPlayback>(
      `/api/reports/session/${sessionId}/recording`,
      { token, signal: opts?.signal },
    ),

  getProctoring: (
    token: string,
    sessionId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<ProctoringAnalysis> =>
    apiFetch<ProctoringAnalysis>(
      `/api/reports/session/${sessionId}/proctoring`,
      { token, signal: opts?.signal },
    ),

  /** POST /api/reports/session/{sessionId}/share — email the report PDF. */
  share: (
    token: string,
    sessionId: string,
    recipientEmail: string,
  ): Promise<ShareReportResponse> =>
    apiFetch<ShareReportResponse>(`/api/reports/session/${sessionId}/share`, {
      token,
      method: 'POST',
      body: JSON.stringify({ recipient_email: recipientEmail }),
    }),

  /**
   * GET /api/public/recordings/{token} — PUBLIC, no auth. Resolves the opaque
   * share-link token to the full playback envelope. 404 (expired/revoked/
   * unknown) throws ApiError. No `token` option → no Authorization header.
   */
  getPublicRecordings: (
    token: string,
    opts?: { signal?: AbortSignal },
  ): Promise<PublicRecordingsEnvelope> =>
    apiFetch<PublicRecordingsEnvelope>(
      `/api/public/recordings/${token}`,
      { signal: opts?.signal },
    ),
}
