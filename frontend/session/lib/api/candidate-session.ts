/**
 * Candidate-session API — token-scoped, no Supabase bearer.
 *
 * The candidate JWT lives in the URL path; AuthMiddleware extracts + verifies
 * it on the server. These calls deliberately bypass `apiFetch` so we never
 * attach a recruiter Supabase bearer to candidate-surface requests.
 */

import { env } from '@/lib/env'

const API_BASE = env.NEXT_PUBLIC_API_URL

export type SessionState =
  | 'created'
  | 'pre_check'
  | 'consented'
  | 'active'
  | 'completed'
  | 'cancelled'
  | 'error'
  | 'terminated' // proctoring policy ended the session mid-interview

export interface PreCheckResponse {
  session_id: string
  company_name: string
  job_title: string
  stage_name: string
  duration_minutes: number
  consent_text: string
  state: SessionState
  otp_required: boolean
  otp_verified_at: string | null
  otp_issued_at: string | null
  proctoring_enabled: boolean
  // Terminating reason when state === 'terminated' (violation kind or
  // 'soft_threshold_exceeded'); null otherwise.
  proctoring_outcome: string | null
}

export interface ConsentBody {
  consented: true
  user_agent: string
}

export interface VerifyOtpBody {
  code: string
}

export interface AudioProcessingHints {
  noise_suppression: boolean
  echo_cancellation: boolean
  auto_gain_control: boolean
}

export type ProctoringKind =
  | 'tab_switch'
  | 'focus_loss'
  | 'fullscreen_abandoned'
  | 'devtools'
  | 'fullscreen_exit'
  | 'keyboard'

export interface ProctoringConfig {
  enabled: boolean
  soft_violation_limit: number
  fullscreen_grace_seconds: number
}

export interface ProctoringEventBody {
  kind: ProctoringKind
  occurred_at: string // ISO-8601
}

export interface ProctoringEventResult {
  terminated: boolean
  violation_count: number
  soft_violation_count: number
  already_terminal?: boolean
}

export interface StartSessionResponse {
  livekit_url: string
  livekit_token: string
  room_name: string
  session_id: string
  audio_processing_hints: AudioProcessingHints
  proctoring: ProctoringConfig
}

export interface CandidateSessionState {
  state: SessionState
  error_code: string | null
  state_changed_at: string // ISO-8601
}

export interface CandidateSessionError extends Error {
  status: number
  code?: string
  attempts_remaining?: number
  retry_after_seconds?: number
}

async function _call<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  // `ngrok-skip-browser-warning` bypasses ngrok free-tier's HTML interstitial
  // on cross-origin XHR. No-op against non-ngrok hosts, so safe in production.
  const headers: Record<string, string> = {
    'ngrok-skip-browser-warning': '1',
  }
  if (body) headers['Content-Type'] = 'application/json'

  const r = await fetch(`${API_BASE}${path}`, {
    method,
    // Candidate-session responses change on every state transition (pre_check
    // → consented → active, otp_verified_at stamp, etc). Disable every layer
    // of HTTP caching so a post-mutation invalidateQueries refetch always
    // hits the server. Without this, browser heuristic caching can serve a
    // stale /pre-check body and the wizard never advances.
    cache: 'no-store',
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let parsed: Record<string, unknown> = {}
    try {
      parsed = (await r.json()) as Record<string, unknown>
    } catch {
      // body wasn't JSON — fall through with an empty object
    }
    const message =
      typeof parsed.detail === 'string' ? parsed.detail : `HTTP ${r.status}`
    const err = new Error(message) as CandidateSessionError
    err.status = r.status
    // Cherry-pick known fields rather than spreading attacker-influenced JSON
    // (which could shadow Error.prototype.stack / .name / .message).
    if (typeof parsed.code === 'string') err.code = parsed.code
    if (typeof parsed.attempts_remaining === 'number')
      err.attempts_remaining = parsed.attempts_remaining
    if (typeof parsed.retry_after_seconds === 'number')
      err.retry_after_seconds = parsed.retry_after_seconds
    throw err
  }
  if (r.status === 204) return undefined as T
  return (await r.json()) as T
}

export const candidateSessionApi = {
  preCheck: (token: string) =>
    _call<PreCheckResponse>(
      'GET',
      `/api/candidate-session/${token}/pre-check`,
    ),
  consent: (token: string, body: ConsentBody) =>
    _call<void>('POST', `/api/candidate-session/${token}/consent`, body),
  requestOtp: (token: string) =>
    _call<void>('POST', `/api/candidate-session/${token}/request-otp`),
  verifyOtp: (token: string, body: VerifyOtpBody) =>
    _call<void>('POST', `/api/candidate-session/${token}/verify-otp`, body),
  start: (token: string) =>
    _call<StartSessionResponse>(
      'POST',
      `/api/candidate-session/${token}/start`,
    ),
  rejoin: (token: string) =>
    _call<StartSessionResponse>(
      'POST',
      `/api/candidate-session/${token}/rejoin`,
    ),
  /**
   * Minimal state snapshot for the post-/start fallback poll. Used by
   * useSessionStateFallback to surface engine failures that crashed
   * before publishing the session_outcome LK room attribute (pre-room-
   * connect crashes).
   */
  getState: (token: string) =>
    _call<CandidateSessionState>(
      'GET',
      `/api/candidate-session/${token}/state`,
    ),
  /**
   * Report a single proctoring violation. Backend is authoritative on the
   * escalation threshold and termination; the response says whether the
   * session was ended. Carries no PII — only the violation kind + timestamp.
   */
  proctoringEvent: (token: string, body: ProctoringEventBody) =>
    _call<ProctoringEventResult>(
      'POST',
      `/api/candidate-session/${token}/proctoring/event`,
      body,
    ),
}
