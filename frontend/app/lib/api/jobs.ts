import { apiFetch } from './client'

export type SignalItem = {
  value: string
  source: 'ai_extracted' | 'ai_inferred' | 'recruiter'
  inference_basis: string | null
}

export type SignalSnapshot = {
  version: number
  required_skills: SignalItem[]
  preferred_skills: SignalItem[]
  must_haves: SignalItem[]
  good_to_haves: SignalItem[]
  min_experience_years: number
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}

export type JobStatus =
  | 'draft'
  | 'signals_extracting'
  | 'signals_extraction_failed'
  | 'signals_extracted'

export type JobPostingSummary = {
  id: string
  title: string
  org_unit_id: string
  status: JobStatus
  status_error: string | null
  created_at: string
  updated_at: string
}

export type JobPostingWithSnapshot = JobPostingSummary & {
  description_raw: string
  project_scope_raw: string | null
  description_enriched: string | null
  target_headcount: number | null
  deadline: string | null
  latest_snapshot: SignalSnapshot | null
}

export type JobStatusEvent = {
  job_id: string
  status: JobStatus
  error: string | null
  signal_snapshot_version: number | null
}

export type CreateJobBody = {
  org_unit_id: string
  title: string
  description_raw: string
  project_scope_raw: string | null
  target_headcount: number | null
  deadline: string | null
}

// --- API client methods ---
// apiFetch signature: apiFetch<T>(path: string, options: RequestInit & { token?: string })
// Content-Type: application/json is set by default in apiFetch, but we override headers
// per call only when needed. For POST with body we pass headers explicitly so the
// merge in apiFetch picks up any call-site overrides.

export const jobsApi = {
  list: (token: string, orgUnitId?: string): Promise<JobPostingSummary[]> =>
    apiFetch<JobPostingSummary[]>(
      `/api/jobs${orgUnitId ? `?org_unit_id=${orgUnitId}` : ''}`,
      { token },
    ),

  get: (token: string, id: string): Promise<JobPostingWithSnapshot> =>
    apiFetch<JobPostingWithSnapshot>(`/api/jobs/${id}`, { token }),

  create: (token: string, body: CreateJobBody): Promise<JobPostingWithSnapshot> =>
    apiFetch<JobPostingWithSnapshot>('/api/jobs', {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  retry: (token: string, id: string): Promise<JobPostingSummary> =>
    apiFetch<JobPostingSummary>(`/api/jobs/${id}/retry`, {
      token,
      method: 'POST',
    }),
}
