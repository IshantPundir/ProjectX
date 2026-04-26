import { apiFetch } from './client'

export interface MeResponse {
  user_id: string
  email: string
  full_name: string | null
  tenant_id: string
  client_name: string
  is_super_admin: boolean
  onboarding_complete: boolean
  has_org_units: boolean
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

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  redirect_to: string
}

export const authApi = {
  me: (token: string, opts?: { signal?: AbortSignal }): Promise<MeResponse> =>
    apiFetch<MeResponse>('/api/auth/me', { token, signal: opts?.signal }),

  acceptInvite: (
    body: AcceptInviteRequest,
    opts?: { signal?: AbortSignal },
  ): Promise<AcceptInviteResponse> =>
    apiFetch<AcceptInviteResponse>('/api/auth/accept-invite', {
      method: 'POST',
      body: JSON.stringify(body),
      signal: opts?.signal,
    }),

  login: (
    body: LoginRequest,
    opts?: { signal?: AbortSignal },
  ): Promise<LoginResponse> =>
    apiFetch<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(body),
      signal: opts?.signal,
    }),

  completeOnboarding: (
    token: string,
    opts?: { signal?: AbortSignal },
  ): Promise<{ status: string }> =>
    apiFetch<{ status: string }>('/api/auth/onboarding/complete', {
      method: 'POST',
      token,
      signal: opts?.signal,
    }),
}
