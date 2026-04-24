import { apiFetch } from './client'

export interface TeamMemberAssignment {
  org_unit_id: string
  org_unit_name: string
  role_name: string
}

/**
 * A row returned by GET /api/settings/team/members. Covers both users
 * (with role assignments) and outstanding invites (awaiting claim).
 * Callers partition on `source`.
 */
export interface TeamMember {
  id: string
  email: string
  full_name: string | null
  is_active: boolean
  is_super_admin: boolean
  assignments: TeamMemberAssignment[]
  source: 'user' | 'invite'
  status: string
  created_at: string
}

export interface InviteTeamMemberRequest {
  email: string
}

/**
 * Response from POST /api/settings/team/invite. `invite_url` is only
 * populated in dry-run mode (empty string in production — the real link
 * is emailed to the invitee).
 */
export interface InviteTeamMemberResponse {
  invite_id: string
  email: string
  invite_url: string
}

/**
 * Response from POST /api/settings/team/resend/{invite_id}. The old
 * invite is superseded and a new invite row is created. `invite_url`
 * is only populated in dry-run mode.
 */
export interface ResendInviteResponse {
  new_invite_id: string
  invite_url: string
}

export interface RevokeInviteResponse {
  status: string
}

export interface DeactivateUserResponse {
  status: string
}

export const teamApi = {
  list: (token: string, opts?: { signal?: AbortSignal }): Promise<TeamMember[]> =>
    apiFetch<TeamMember[]>('/api/settings/team/members', {
      token,
      signal: opts?.signal,
    }),

  invite: (
    token: string,
    body: InviteTeamMemberRequest,
    opts?: { signal?: AbortSignal },
  ): Promise<InviteTeamMemberResponse> =>
    apiFetch<InviteTeamMemberResponse>('/api/settings/team/invite', {
      method: 'POST',
      token,
      body: JSON.stringify(body),
      signal: opts?.signal,
    }),

  resend: (
    token: string,
    inviteId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<ResendInviteResponse> =>
    apiFetch<ResendInviteResponse>(`/api/settings/team/resend/${inviteId}`, {
      method: 'POST',
      token,
      signal: opts?.signal,
    }),

  revoke: (
    token: string,
    inviteId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<RevokeInviteResponse> =>
    apiFetch<RevokeInviteResponse>(`/api/settings/team/revoke/${inviteId}`, {
      method: 'POST',
      token,
      signal: opts?.signal,
    }),

  deactivate: (
    token: string,
    userId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<DeactivateUserResponse> =>
    apiFetch<DeactivateUserResponse>(`/api/settings/team/deactivate/${userId}`, {
      method: 'POST',
      token,
      signal: opts?.signal,
    }),
}
