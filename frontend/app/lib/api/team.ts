import { apiFetch } from './client'

export interface TeamMemberAssignment {
  org_unit_id: string
  org_unit_name: string
  role_name: string
}

/**
 * Provenance string on a team-member row. 'native' for natively-invited
 * users; 'ats_<vendor>' for users imported from an ATS sync. Mirrors the
 * backend `users.source` column tagged at row insert time.
 */
export type TeamMemberSource = 'native' | `ats_${string}`

/**
 * A row returned by GET /api/settings/team/members. Under the unified
 * storage model (spec 2026-05-14), every member — native AND ATS-imported
 * — lives in the same `users` table. Pending invites that don't yet have
 * a matching User row surface as separate rows with `has_auth_account=false`
 * and `invite_state='pending'`.
 *
 * Display states are derived from the booleans, not a single enum:
 *   - has_auth_account=true,  is_active=true   → Active
 *   - has_auth_account=true,  is_active=false  → Inactive (deactivated)
 *   - has_auth_account=false, invite_state='pending' → Invited
 *   - has_auth_account=false, source LIKE 'ats_%'     → ATS-only (not invited)
 *
 * `status` is preserved as a legacy convenience field that still maps
 * 'active' / 'inactive' / 'pending' / 'ats_unlinked'. Phase D consumers
 * should derive state from `has_auth_account` + `is_active` + `invite_state`
 * directly when displaying chips.
 */
export interface TeamMember {
  id: string
  email: string
  full_name: string | null
  source: TeamMemberSource
  external_id: string | null
  external_source_metadata: {
    role?: string
    timezone?: string
    business_unit_id?: number
    external_status?: string
  } | null
  is_active: boolean
  has_auth_account: boolean
  invite_state: 'none' | 'pending' | 'accepted' | 'revoked'
  is_super_admin: boolean
  assignments: TeamMemberAssignment[]
  created_at: string
  /** Legacy enum kept for backwards-compat with older renderers. Derived
   * from the booleans above; new code should ignore it. */
  status: string
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
