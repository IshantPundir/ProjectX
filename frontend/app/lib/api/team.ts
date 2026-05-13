import { apiFetch } from './client'

export interface TeamMemberAssignment {
  org_unit_id: string
  org_unit_name: string
  role_name: string
}

/**
 * A row returned by GET /api/settings/team/members. Covers active users,
 * outstanding invites, and ATS-imported users not yet on the team. Callers
 * branch on `source`:
 *   - 'user'   → real User row, may have role assignments
 *   - 'invite' → pending UserInvite awaiting claim
 *   - 'ats'    → ATSUserMapping with internal_user_id IS NULL (not yet
 *                a member or pending invite). The Send-invite action on
 *                these rows hits the same /api/settings/team/invite as
 *                a manual invite — on accept, the invite-accept handler
 *                wires up internal_user_id automatically.
 *
 * Status values:
 *   - 'active'        for source='user' (active accounts)
 *   - 'inactive'      for source='user' (deactivated)
 *   - 'pending'       for source='invite'
 *   - 'ats_unlinked'  for source='ats'
 */
export interface TeamMember {
  id: string
  email: string
  full_name: string | null
  is_active: boolean
  is_super_admin: boolean
  assignments: TeamMemberAssignment[]
  source: 'user' | 'invite' | 'ats'
  status: string
  created_at: string
  external_user_id: string | null
  ats_vendor: string | null
  external_role: string | null
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
