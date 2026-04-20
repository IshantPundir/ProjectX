import { apiFetch } from './client'

// --- Enums ---

export type CandidateSource =
  | 'manual'
  | 'csv'
  | 'ceipal'
  | 'greenhouse'
  | 'workday'

export type AssignmentStatus =
  | 'active'
  | 'archived'
  | 'hired'
  | 'rejected'
  | 'withdrawn'

// --- Request bodies ---

export interface CandidateCreate {
  name: string
  email: string
  phone?: string | null
  location?: string | null
  current_title?: string | null
  linkedin_url?: string | null
  notes?: string | null
  source?: CandidateSource
  external_id?: string | null
  source_metadata?: Record<string, unknown> | null
}

export interface CandidateUpdate {
  name?: string
  phone?: string | null
  location?: string | null
  current_title?: string | null
  linkedin_url?: string | null
  notes?: string | null
}

export interface AssignmentCreate {
  job_posting_id: string
  target_stage_id?: string
}

export interface AssignmentUpdate {
  status: AssignmentStatus
}

export interface StageTransition {
  target_stage_id: string
  reason?: string
  override?: boolean
}

// --- Response shapes ---

export interface CandidateResponse {
  id: string
  name: string | null
  email: string | null
  phone: string | null
  location: string | null
  current_title: string | null
  linkedin_url: string | null
  resume_s3_key: string | null
  resume_uploaded_at: string | null
  notes: string | null
  source: string
  external_id: string | null
  created_at: string
  updated_at: string
  pii_redacted_at: string | null
}

export interface CandidateListPage {
  items: CandidateResponse[]
  total: number
  offset: number
  limit: number
}

export interface AssignmentResponse {
  id: string
  candidate_id: string
  job_posting_id: string
  job_title: string
  current_stage_id: string
  current_stage_name: string
  status: AssignmentStatus
  status_changed_at: string
  assigned_at: string
}

export interface ResumeUploadUrl {
  upload_url: string
  s3_key: string
  expires_in_seconds: number
}

export interface KanbanCandidateCard {
  candidate_id: string
  assignment_id: string
  name: string | null
  email: string | null
  status: AssignmentStatus
  current_stage_id: string
  latest_session_state: string | null
}

export interface KanbanColumn {
  stage_id: string
  stage_name: string
  position: number
  candidates: KanbanCandidateCard[]
}

export interface KanbanBoardResponse {
  job_posting_id: string
  stages: KanbanColumn[]
}

// --- List filter shape ---

export interface CandidatesListFilters {
  q?: string
  job_id?: string
  stage_id?: string
  status?: AssignmentStatus | string
  offset?: number
  limit?: number
}

// --- API client namespace ---
// apiFetch signature: apiFetch<T>(path: string, options: RequestInit & { token?: string })
// JSON bodies must be stringified — apiFetch sets Content-Type: application/json by default.

export const candidatesApi = {
  list: (
    token: string,
    filters: CandidatesListFilters = {},
  ): Promise<CandidateListPage> => {
    const params = new URLSearchParams()
    if (filters.q) params.set('q', filters.q)
    if (filters.job_id) params.set('job_id', filters.job_id)
    if (filters.stage_id) params.set('stage_id', filters.stage_id)
    if (filters.status) params.set('status', filters.status)
    if (filters.offset != null) params.set('offset', String(filters.offset))
    if (filters.limit != null) params.set('limit', String(filters.limit))
    const qs = params.toString()
    return apiFetch<CandidateListPage>(
      `/api/candidates${qs ? `?${qs}` : ''}`,
      { token },
    )
  },

  get: (token: string, id: string): Promise<CandidateResponse> =>
    apiFetch<CandidateResponse>(`/api/candidates/${id}`, { token }),

  listAssignments: (
    token: string,
    candidateId: string,
  ): Promise<AssignmentResponse[]> =>
    apiFetch<AssignmentResponse[]>(
      `/api/candidates/${candidateId}/assignments`,
      { token },
    ),

  create: (token: string, body: CandidateCreate): Promise<CandidateResponse> =>
    apiFetch<CandidateResponse>('/api/candidates', {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  update: (
    token: string,
    id: string,
    body: CandidateUpdate,
  ): Promise<CandidateResponse> =>
    apiFetch<CandidateResponse>(`/api/candidates/${id}`, {
      token,
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  redactPii: (token: string, id: string): Promise<void> =>
    apiFetch<void>(`/api/candidates/${id}/redact-pii`, {
      token,
      method: 'POST',
      body: JSON.stringify({
        confirmation: 'I understand this permanently removes PII',
      }),
    }),

  requestResumeUpload: (
    token: string,
    id: string,
  ): Promise<ResumeUploadUrl> =>
    apiFetch<ResumeUploadUrl>(`/api/candidates/${id}/resume`, {
      token,
      method: 'POST',
      body: JSON.stringify({}),
    }),

  confirmResumeUpload: (
    token: string,
    id: string,
    s3_key: string,
  ): Promise<void> =>
    apiFetch<void>(`/api/candidates/${id}/resume/confirm`, {
      token,
      method: 'POST',
      body: JSON.stringify({ s3_key }),
    }),

  deleteResume: (token: string, id: string): Promise<void> =>
    apiFetch<void>(`/api/candidates/${id}/resume`, {
      token,
      method: 'DELETE',
    }),

  createAssignment: (
    token: string,
    candidateId: string,
    body: AssignmentCreate,
  ): Promise<AssignmentResponse> =>
    apiFetch<AssignmentResponse>(
      `/api/candidates/${candidateId}/assignments`,
      {
        token,
        method: 'POST',
        body: JSON.stringify(body),
      },
    ),

  updateAssignmentStatus: (
    token: string,
    candidateId: string,
    assignmentId: string,
    body: AssignmentUpdate,
  ): Promise<AssignmentResponse> =>
    apiFetch<AssignmentResponse>(
      `/api/candidates/${candidateId}/assignments/${assignmentId}`,
      {
        token,
        method: 'PATCH',
        body: JSON.stringify(body),
      },
    ),

  transitionStage: (
    token: string,
    candidateId: string,
    assignmentId: string,
    body: StageTransition,
  ): Promise<AssignmentResponse> =>
    apiFetch<AssignmentResponse>(
      `/api/candidates/${candidateId}/assignments/${assignmentId}/transition`,
      {
        token,
        method: 'POST',
        body: JSON.stringify(body),
      },
    ),

  kanban: (token: string, jobId: string): Promise<KanbanBoardResponse> =>
    apiFetch<KanbanBoardResponse>(
      `/api/jobs/${jobId}/candidates/kanban`,
      { token },
    ),
}
