import { apiFetch } from './client'

/**
 * Response shape of GET /api/auth/me.
 *
 * Mirrors backend `app/modules/auth/schemas.py::MeResponse` exactly.
 * Roles/permissions live in `assignments`; the JWT only carries
 * `is_super_admin` and `tenant_id`. Any conditional UI based on roles
 * MUST go through the per-request `assignments` data — never trust
 * a JWT claim alone.
 */
export interface MeResponse {
  user_id: string
  email: string
  full_name: string | null
  tenant_id: string
  client_name: string
  is_super_admin: boolean
  onboarding_complete: boolean
  has_org_units: boolean
  workspace_mode: string
  assignments: {
    org_unit_id: string
    org_unit_name: string
    role_name: string
    permissions: string[]
  }[]
}

export interface AcceptInviteRequest {
  raw_token: string
  password: string
}

export interface AcceptInviteResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  redirect_to: string
}

export const authApi = {
  me: (
    token: string,
    opts?: { signal?: AbortSignal },
  ): Promise<MeResponse> =>
    apiFetch<MeResponse>('/api/auth/me', {
      token,
      signal: opts?.signal,
    }),

  acceptInvite: (
    body: AcceptInviteRequest,
    opts?: { signal?: AbortSignal },
  ): Promise<AcceptInviteResponse> =>
    apiFetch<AcceptInviteResponse>('/api/auth/accept-invite', {
      method: 'POST',
      body: JSON.stringify(body),
      signal: opts?.signal,
    }),
}
