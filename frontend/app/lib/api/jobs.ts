import { apiFetch } from './client'

// Signal enums
export type SignalType = 'competency' | 'experience' | 'credential' | 'behavioral'
export type SignalPriority = 'required' | 'preferred'
export type SignalStage = 'screen' | 'interview'
export type EvaluationMethod =
  | 'verbal_response'
  | 'code_exercise'
  | 'scenario_walkthrough'
  | 'credential_verify'
  | 'behavioral_question'

// Job metadata enums
export type EmploymentType =
  | 'full_time'
  | 'part_time'
  | 'contract'
  | 'contract_to_hire'
  | 'internship'
export type WorkArrangement = 'onsite' | 'remote' | 'hybrid'
export type SalaryCurrency = 'USD' | 'EUR' | 'GBP' | 'INR' | 'CAD' | 'AUD'
export type TravelRequired = 'none' | 'occasional' | 'moderate' | 'extensive'
export type StartDatePref =
  | 'immediate'
  | 'within_30_days'
  | 'within_60_days'
  | 'flexible'

export type SignalItem = {
  value: string
  type: SignalType
  priority: SignalPriority
  weight: 1 | 2 | 3
  knockout: boolean
  stage: SignalStage
  evaluation_method: EvaluationMethod
  evaluation_hint: string | null
  source: 'ai_extracted' | 'ai_inferred' | 'recruiter'
  inference_basis: string | null
}

export type SignalSnapshot = {
  version: number
  signals: SignalItem[]
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
  confirmed_by: string | null
  confirmed_at: string | null
}

export type JobStatus =
  | 'draft'
  | 'signals_extracting'
  | 'signals_extraction_failed'
  | 'signals_extracted'
  | 'signals_confirmed'
  | 'pipeline_built'
  | 'active'
  | 'archived'
  /**
   * ATS-imported jobs land here when the client_account org_unit's
   * company profile is still pending. The unblock cascade (PUT
   * /api/org-units/{id} → company_profile_completion_status pending →
   * complete) transitions them to 'draft' and enqueues extraction.
   */
  | 'blocked_pending_client_setup'

/**
 * Provenance for imported jobs. 'native' is the recruiter-created default;
 * 'manual' is reserved for explicit recruiter import (Phase 15+);
 * 'ats_ceipal' is set by the Ceipal poll-sync.
 */
export type JobSource = 'native' | 'manual' | 'ats_ceipal' | string

export type JobPostingSummary = {
  id: string
  title: string
  // Nullable for ATS-imported jobs with no matching client mapping; the
  // list page renders a 'Not set up' chip in that case until a recruiter
  // links the job to an org_unit.
  org_unit_id: string | null
  org_unit_name: string | null
  created_by_email: string | null
  updated_by_email: string | null
  status: JobStatus
  status_error: string | null
  created_at: string
  updated_at: string
  /**
   * Total signals on the latest snapshot. 0 if no snapshot yet
   * (draft / extracting / failed status).
   */
  signal_count: number
  /**
   * Number of AI-inferred signals flagged for human review
   * (same heuristic the JD Review page applies for the
   * "double-check" chip: source === 'ai_inferred' AND weight < 2).
   */
  needs_review_count: number
  /**
   * Provenance — 'native' for recruiter-created, 'ats_<vendor>' for
   * importer-created. Frontend uses this to render the From-ATS chip.
   */
  source: JobSource
  external_id: string | null
  external_status: string | null
}

export type EnrichmentStatus = 'idle' | 'streaming' | 'completed' | 'failed'

/**
 * Detail row for GET /api/jobs/{id}. `org_unit_id` is inherited from
 * JobPostingSummary as `string | null`; the detail page's signal /
 * pipeline / enrichment surfaces don't work for unlinked jobs and should
 * guard accordingly.
 */
export type JobPostingWithSnapshot = JobPostingSummary & {
  description_raw: string
  project_scope_raw: string | null
  description_enriched: string | null
  target_headcount: number | null
  deadline: string | null
  latest_snapshot: SignalSnapshot | null
  enrichment_status: EnrichmentStatus
  enrichment_error: string | null
  is_confirmed: boolean
  can_manage: boolean
  employment_type: EmploymentType | null
  work_arrangement: WorkArrangement | null
  location: string | null
  salary_range_min: number | null
  salary_range_max: number | null
  salary_currency: SalaryCurrency | null
  travel_required: TravelRequired | null
  start_date_pref: StartDatePref | null
}

export type JobStatusEvent = {
  job_id: string
  status: JobStatus
  error: string | null
  signal_snapshot_version: number | null
  enrichment_status: EnrichmentStatus
  is_confirmed: boolean
}

export type CreateJobBody = {
  org_unit_id: string
  title: string
  description_raw: string
  project_scope_raw: string | null
  target_headcount: number | null
  deadline: string | null
  employment_type: EmploymentType | null
  work_arrangement: WorkArrangement | null
  location: string | null
  salary_range_min: number | null
  salary_range_max: number | null
  salary_currency: SalaryCurrency | null
  travel_required: TravelRequired | null
  start_date_pref: StartDatePref | null
  skip_enrichment?: boolean
}

export type SaveSignalsBody = {
  signals: SignalItem[]
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}

// --- API client methods ---
// apiFetch signature: apiFetch<T>(path: string, options: RequestInit & { token?: string })
// Content-Type: application/json is set by default in apiFetch, but we override headers
// per call only when needed. For POST with body we pass headers explicitly so the
// merge in apiFetch picks up any call-site overrides.

export const jobsApi = {
  list: (
    token: string,
    orgUnitId?: string,
    opts?: { signal?: AbortSignal },
  ): Promise<JobPostingSummary[]> =>
    apiFetch<JobPostingSummary[]>(
      `/api/jobs${orgUnitId ? `?org_unit_id=${orgUnitId}` : ''}`,
      { token, signal: opts?.signal },
    ),

  get: (
    token: string,
    id: string,
    opts?: { signal?: AbortSignal },
  ): Promise<JobPostingWithSnapshot> =>
    apiFetch<JobPostingWithSnapshot>(`/api/jobs/${id}`, {
      token,
      signal: opts?.signal,
    }),

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

  saveSignals: (token: string, id: string, body: SaveSignalsBody): Promise<SignalSnapshot> =>
    apiFetch<SignalSnapshot>(`/api/jobs/${id}/signals`, {
      token,
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  confirmSignals: (token: string, id: string): Promise<JobPostingSummary> =>
    apiFetch<JobPostingSummary>(`/api/jobs/${id}/signals/confirm`, {
      token,
      method: 'POST',
    }),

  triggerEnrich: (token: string, id: string): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(`/api/jobs/${id}/enrich`, {
      token,
      method: 'POST',
    }),

  delete: (token: string, id: string): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(`/api/jobs/${id}`, {
      token,
      method: 'DELETE',
    }),
}
