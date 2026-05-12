import { z } from 'zod'

import { apiFetch } from '@/lib/api/client'

// --- Vendor tag ---

export type ATSVendor = 'ceipal'

// --- Response shapes (match backend ConnectionResponse / SyncLogResponse /
// UnmappedUserResponse in app/modules/ats/router.py) ---

export interface CeipalJobStatus {
  id: number
  name: string
}

export interface JobStatusFilter {
  ids: number[]
  names: string[]
}

export interface ATSConnection {
  id: string
  vendor: string
  active: boolean
  last_synced_at: string | null
  next_poll_at: string | null
  last_poll_error: string | null
  disabled_reason: string | null
  created_at: string
  job_status_filter: JobStatusFilter | null
}

export type ATSSyncStatus = 'running' | 'success' | 'partial' | 'failed'

export interface ATSSyncLog {
  id: string
  started_at: string
  completed_at: string | null
  status: ATSSyncStatus
  entity_counts: Record<string, Record<string, number>>
  progress: { jobs?: { processed: number; total: number } }
  error_phase: string | null
  error_summary: string | null
}

export interface ATSUnmappedUser {
  external_user_id: string
  external_user_email: string
  external_user_display_name: string
  external_user_role: string | null
}

// --- Zod schemas for request payloads ---

export const ceipalCredentialsSchema = z.object({
  email: z.string().email('Must be a valid email address'),
  password: z.string().min(1, 'Password is required'),
  api_key: z.string().min(1, 'API key is required'),
})

export type CeipalCredentials = z.infer<typeof ceipalCredentialsSchema>

// Discriminated union — adding Greenhouse later means appending one more
// union member; nothing else changes. Mirrors the backend Pydantic
// discriminator in app/modules/ats/router.py.
export const connectionCreateSchema = z.discriminatedUnion('vendor', [
  z.object({
    vendor: z.literal('ceipal'),
    credentials: ceipalCredentialsSchema,
  }),
])

export type ConnectionCreatePayload = z.infer<typeof connectionCreateSchema>

export const mapUserSchema = z.object({
  internal_user_id: z.string().uuid('Must be a valid user id'),
})

export type MapUserPayload = z.infer<typeof mapUserSchema>

// --- apiFetch wrappers for /api/ats/* endpoints ---
// Mirrors the convention used by lib/api/candidates.ts. Token is passed
// explicitly per call; new callers should retrieve it via
// getFreshSupabaseToken() from lib/auth/tokens.ts.

export async function listConnections(
  token: string,
): Promise<ATSConnection[]> {
  return apiFetch<ATSConnection[]>('/api/ats/connections', { token })
}

export async function getConnection(
  token: string,
  id: string,
): Promise<ATSConnection> {
  return apiFetch<ATSConnection>(`/api/ats/connections/${id}`, { token })
}

export async function createConnection(
  token: string,
  body: ConnectionCreatePayload,
): Promise<ATSConnection> {
  return apiFetch<ATSConnection>('/api/ats/connections', {
    token,
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function deleteConnection(
  token: string,
  id: string,
): Promise<void> {
  await apiFetch<void>(`/api/ats/connections/${id}`, {
    token,
    method: 'DELETE',
  })
}

export async function triggerManualSync(
  token: string,
  id: string,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/ats/connections/${id}/sync`, {
    token,
    method: 'POST',
  })
}

export async function listSyncLogs(
  token: string,
  connectionId: string,
): Promise<ATSSyncLog[]> {
  return apiFetch<ATSSyncLog[]>(
    `/api/ats/connections/${connectionId}/sync-logs`,
    { token },
  )
}

export async function listUnmappedUsers(
  token: string,
  connectionId: string,
): Promise<ATSUnmappedUser[]> {
  return apiFetch<ATSUnmappedUser[]>(
    `/api/ats/connections/${connectionId}/unmapped-users`,
    { token },
  )
}

export async function mapATSUser(
  token: string,
  connectionId: string,
  externalUserId: string,
  body: MapUserPayload,
): Promise<void> {
  await apiFetch<void>(
    `/api/ats/connections/${connectionId}/users/${externalUserId}/map`,
    {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

export async function listJobStatuses(
  token: string,
  connectionId: string,
): Promise<CeipalJobStatus[]> {
  return apiFetch<CeipalJobStatus[]>(
    `/api/ats/connections/${connectionId}/job-statuses`,
    { token },
  )
}

/**
 * `body.ids` is serialized as `status_ids` on the wire to match the backend
 * Pydantic field (`JobStatusFilterRequest.status_ids` in
 * `app/modules/ats/router.py`). The frontend type keeps the shorter `ids`
 * for ergonomics. This is the only wire-rename in `lib/api/`; if the
 * backend field name ever changes, update both ends in lockstep.
 */
export async function updateJobStatusFilter(
  token: string,
  connectionId: string,
  body: JobStatusFilter,
): Promise<void> {
  await apiFetch<void>(
    `/api/ats/connections/${connectionId}/job-status-filter`,
    {
      token,
      method: 'PUT',
      body: JSON.stringify({
        status_ids: body.ids,
        names: body.names,
      }),
    },
  )
}
