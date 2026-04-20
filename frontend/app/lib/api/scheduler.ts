/** Scheduler API — dashboard-side, Supabase-bearer authenticated. */
import { apiFetch } from '@/lib/api/client'

export interface InviteCreateBody {
  assignment_id: string
  otp_required?: boolean
}

export interface InviteResponse {
  session_id: string
  token_expires_at: string
}

export type SessionState =
  | 'created'
  | 'pre_check'
  | 'consented'
  | 'active'
  | 'completed'
  | 'cancelled'
  | 'error'

export interface SessionDetail {
  id: string
  assignment_id: string
  stage_id: string
  stage_name: string
  state: SessionState
  state_changed_at: string
  otp_required: boolean
  consent_recorded_at: string | null
  scheduled_for: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface SessionListPage {
  items: SessionDetail[]
  total: number
  offset: number
  limit: number
}

export const schedulerApi = {
  sendInvite: (token: string, body: InviteCreateBody) =>
    apiFetch<InviteResponse>('/api/scheduler/invites', {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),
  resendInvite: (token: string, sessionId: string) =>
    apiFetch<InviteResponse>(`/api/scheduler/invites/${sessionId}/resend`, {
      token,
      method: 'POST',
    }),
  revokeInvite: (token: string, sessionId: string) =>
    apiFetch<void>(`/api/scheduler/invites/${sessionId}/revoke`, {
      token,
      method: 'POST',
    }),
  listSessions: (
    token: string,
    filters: { assignment_id?: string; state?: string } = {},
  ) => {
    const params = new URLSearchParams()
    if (filters.assignment_id) params.set('assignment_id', filters.assignment_id)
    if (filters.state) params.set('state', filters.state)
    const qs = params.toString()
    return apiFetch<SessionListPage>(
      `/api/sessions${qs ? `?${qs}` : ''}`,
      { token },
    )
  },
}
