import { z } from 'zod'

import { apiFetch } from '@/lib/api/client'

// ────────────────────────────── Vendor tag ──────────────────────────────

/** ATS vendor identifier. Always prefixed with `ats_` (matches the backend
 * `users.source` / `organizational_units.source` / `job_postings.source`
 * convention from migration 0036). Adding Greenhouse later → append
 * `'ats_greenhouse'`. */
export type ATSVendor = 'ats_ceipal'

// ────────────────────────────── Sync mode ───────────────────────────────

/** Three values map 1:1 to the backend's `ats_connections.status_sync_mode`
 * CHECK constraint. Each connection sits in exactly one mode at a time. */
export type ATSStatusSyncMode = 'advisory' | 'mirror' | 'one_way'

// ─────────────────────────────── Shapes ─────────────────────────────────

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
  vendor: ATSVendor
  active: boolean
  status_sync_mode: ATSStatusSyncMode
  tenant_timezone: string | null
  last_synced_at: string | null
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
  /** Shape is a flat ``Record<string, number>`` (jobs_imported /
   * jobs_updated / jobs_unchanged / submissions_imported / …). Treat as
   * opaque counter map at the API boundary; UI projects what it cares
   * about. */
  entity_counts: Record<string, number>
  progress: Record<string, unknown>
  error_phase: string | null
  error_summary: string | null
}

/** One audit-log row matching `ats.*` actions. Used to render the per-sync
 * activity timeline (when `correlation_id` is provided) and the
 * per-resource activity feed (when `resource_id` is provided). */
export interface ATSSyncLogEvent {
  id: string
  event_type: string
  resource_type: 'job' | 'user' | 'org_unit' | 'submission' | 'candidate'
  resource_id: string | null
  payload: Record<string, unknown>
  correlation_id: string
  created_at: string
}

export interface ATSStageMapping {
  id: string
  external_status_label: string
  projectx_stage_id: string
  action_on_match: 'move_to_stage' | 'reject' | 'archive' | 'no_op'
}

export interface ATSAdvisoryAction {
  id: string
  assignment_id: string
  external_status_before: string | null
  external_status_after: string
  suggested_target_stage_id: string | null
  suggested_action: 'move_to_stage' | 'reject' | 'archive'
  resolution: 'pending' | 'applied' | 'dismissed' | 'superseded'
  created_at: string
}

// ────────────────────────────── Zod schemas ─────────────────────────────

export const ceipalCredentialsSchema = z.object({
  email: z.string().email('Must be a valid email address'),
  password: z.string().min(1, 'Password is required'),
  api_key: z.string().min(1, 'API key is required'),
})

export type CeipalCredentials = z.infer<typeof ceipalCredentialsSchema>

/** Discriminated union — adding Greenhouse later means appending one more
 * union member; nothing else changes. Mirrors the backend Pydantic
 * discriminator in app/modules/ats/router.py. */
export const connectionCreateSchema = z.discriminatedUnion('vendor', [
  z.object({
    vendor: z.literal('ats_ceipal'),
    credentials: ceipalCredentialsSchema,
  }),
])

export type ConnectionCreatePayload = z.infer<typeof connectionCreateSchema>

// ───────────────────────────── Connections ──────────────────────────────

export async function listConnections(token: string): Promise<ATSConnection[]> {
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

export async function deleteConnection(token: string, id: string): Promise<void> {
  await apiFetch<void>(`/api/ats/connections/${id}`, {
    token,
    method: 'DELETE',
  })
}

// ───────────────────────────── Manual sync ──────────────────────────────

/** Empty payload — the new single-trigger sync has no parameters. First
 * sync (cursor IS NULL) implicitly walks the full filter; to force a
 * subsequent full re-scan call `resetCursor` then re-trigger. */
export interface ATSManualSyncRequest {
  _empty?: never
}

export async function triggerManualSync(
  token: string,
  id: string,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(
    `/api/ats/connections/${id}/sync`,
    {
      token,
      method: 'POST',
    },
  )
}

// ─────────────────────────── Reset cursor ───────────────────────────────

export async function resetCursor(
  token: string,
  connectionId: string,
  reason: string,
): Promise<void> {
  await apiFetch<void>(
    `/api/ats/connections/${connectionId}/reset-cursor`,
    {
      token,
      method: 'POST',
      body: JSON.stringify({ reason }),
    },
  )
}

// ─────────────────────── Status sync mode ───────────────────────────────

export async function updateStatusSyncMode(
  token: string,
  connectionId: string,
  mode: ATSStatusSyncMode,
): Promise<void> {
  await apiFetch<void>(
    `/api/ats/connections/${connectionId}/status-sync-mode`,
    {
      token,
      method: 'PUT',
      body: JSON.stringify({ mode }),
    },
  )
}

// ────────────────────────────── Sync logs ───────────────────────────────

export async function listSyncLogs(
  token: string,
  connectionId: string,
): Promise<ATSSyncLog[]> {
  return apiFetch<ATSSyncLog[]>(
    `/api/ats/connections/${connectionId}/sync-logs`,
    { token },
  )
}

// ─────────────────────────── Job statuses ───────────────────────────────

export async function listJobStatuses(
  token: string,
  connectionId: string,
): Promise<CeipalJobStatus[]> {
  return apiFetch<CeipalJobStatus[]>(
    `/api/ats/connections/${connectionId}/job-statuses`,
    { token },
  )
}

/** `body.ids` is serialized as `status_ids` on the wire to match the
 * backend Pydantic field. */
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

// ───────────────────────── Stage mappings ───────────────────────────────

export async function listStageMappings(
  token: string,
  connectionId: string,
): Promise<ATSStageMapping[]> {
  return apiFetch<ATSStageMapping[]>(
    `/api/ats/connections/${connectionId}/stage-mappings`,
    { token },
  )
}

export interface StageMappingCreatePayload {
  external_status_label: string
  projectx_stage_id: string
  action_on_match: 'move_to_stage' | 'reject' | 'archive' | 'no_op'
}

export async function createStageMapping(
  token: string,
  connectionId: string,
  body: StageMappingCreatePayload,
): Promise<ATSStageMapping> {
  return apiFetch<ATSStageMapping>(
    `/api/ats/connections/${connectionId}/stage-mappings`,
    {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

export async function deleteStageMapping(
  token: string,
  mappingId: string,
): Promise<void> {
  await apiFetch<void>(`/api/ats/stage-mappings/${mappingId}`, {
    token,
    method: 'DELETE',
  })
}

// ───────────────────────── Advisory actions ─────────────────────────────

export async function listAdvisoryActions(
  token: string,
  assignmentId: string,
): Promise<ATSAdvisoryAction[]> {
  return apiFetch<ATSAdvisoryAction[]>(
    `/api/ats/advisory-actions?assignment_id=${encodeURIComponent(assignmentId)}`,
    { token },
  )
}

// ───────────────────────── Retry-import (quarantine) ────────────────────

export async function retryJobImport(
  token: string,
  jobId: string,
): Promise<void> {
  await apiFetch<void>(`/api/ats/jobs/${jobId}/retry-import`, {
    token,
    method: 'POST',
  })
}
