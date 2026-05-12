import { z } from 'zod'

// --- Vendor tag ---

export type ATSVendor = 'ceipal'

// --- Response shapes (match backend ConnectionResponse / SyncLogResponse /
// UnmappedUserResponse in app/modules/ats/router.py) ---

export interface ATSConnection {
  id: string
  vendor: string
  active: boolean
  last_synced_at: string | null
  next_poll_at: string | null
  last_poll_error: string | null
  disabled_reason: string | null
  created_at: string
}

export type ATSSyncStatus = 'running' | 'success' | 'partial' | 'failed'

export interface ATSSyncLog {
  id: string
  started_at: string
  completed_at: string | null
  status: ATSSyncStatus
  entity_counts: Record<string, Record<string, number>>
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
