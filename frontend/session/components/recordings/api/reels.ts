import { apiFetch } from './client'

// Mirrors backend app/modules/reel/schemas.py::ReelPlayback.
export type ReelStatus = 'absent' | 'pending' | 'generating' | 'ready' | 'failed'

export interface ReelChapter {
  kind: string
  label: string
  /** Offset from the reel's start (ms). */
  start_ms: number
}

export interface ReelPlayback {
  status: ReelStatus
  /** Short-lived presigned GET URL; only present when status === 'ready'. */
  signed_url: string | null
  expires_at: string | null
  duration_seconds: number | null
  chapters: ReelChapter[]
  generation_error: string | null
  /** Whether a reel can be generated (verdict + report-ready + recording-ready). */
  eligible: boolean
  ineligible_reason: string | null
  version: number
}

export const reelApi = {
  /** GET /api/reports/session/{sessionId}/reel — status 'absent' when none yet. */
  get: (
    token: string,
    sessionId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<ReelPlayback> =>
    apiFetch<ReelPlayback>(
      `/api/reports/session/${sessionId}/reel`,
      { token, signal: opts?.signal },
    ),

  /** POST .../reel/generate — first trigger. 422 if ineligible. */
  generate: (token: string, sessionId: string): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(
      `/api/reports/session/${sessionId}/reel/generate`,
      { token, method: 'POST', body: JSON.stringify({}) },
    ),

  /** POST .../reel/regenerate — force a re-render (version bump). */
  regenerate: (token: string, sessionId: string): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(
      `/api/reports/session/${sessionId}/reel/regenerate`,
      { token, method: 'POST', body: JSON.stringify({}) },
    ),
}
