/**
 * Candidate-session API — token-scoped, no Supabase bearer.
 *
 * The candidate JWT lives in the URL path; AuthMiddleware extracts + verifies
 * it on the server. These calls deliberately bypass `apiFetch` so we never
 * attach a recruiter Supabase bearer to candidate-surface requests.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

export type SessionState =
  | 'created'
  | 'pre_check'
  | 'consented'
  | 'active'
  | 'completed'
  | 'cancelled'
  | 'error'

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
}

export interface ConsentBody {
  consented: true
  user_agent: string
}

export interface VerifyOtpBody {
  code: string
}

export interface StartSessionPendingResponse {
  code: 'LIVEKIT_INTEGRATION_PENDING'
  detail: string
  session_id: string
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
  const r = await fetch(`${API_BASE}${path}`, {
    method,
    // Candidate-session responses change on every state transition (pre_check
    // → consented → active, otp_verified_at stamp, etc). Disable every layer
    // of HTTP caching so a post-mutation invalidateQueries refetch always
    // hits the server. Without this, browser heuristic caching can serve a
    // stale /pre-check body and the wizard never advances.
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
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
    _call<StartSessionPendingResponse>(
      'POST',
      `/api/candidate-session/${token}/start`,
    ),
}
