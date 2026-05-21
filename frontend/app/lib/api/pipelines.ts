import { apiFetch, ApiError } from './client'

// --- Enum types ---

// Stage type v5 — matches backend literal in
// app/modules/pipelines/schemas.py and migration 0016.
export type StageType =
  | 'intake'
  | 'phone_screen'
  | 'ai_screening'
  | 'human_interview'
  | 'debrief'
  | 'take_home'

export type ParticipantRole = 'interviewer' | 'observer' | 'reviewer'

export type StageParticipantInput = {
  user_id: string
  role: ParticipantRole
}

export type StageParticipantResponse = StageParticipantInput & {
  full_name: string
  email: string
}

export type StageDifficulty = 'easy' | 'medium' | 'hard'
export type AdvanceBehavior = 'auto_advance' | 'manual_review'

// --- Signal filter ---

export type SignalFilter = {
  include_types: ('competency' | 'experience' | 'credential' | 'behavioral')[]
}

// --- Pass criteria discriminated union ---

export type PassCriteria =
  | { type: 'all_knockouts_pass' }
  | { type: 'score_threshold'; threshold: number }
  | { type: 'manual_review' }

// --- Stage discriminated union (matches backend §6 capability matrix) ---

// Common fields present on every stage variant.
type StageBase = {
  position: number
  name: string
  /** Per-stage dwell SLA in days. Null = no SLA configured. */
  sla_days?: number | null
  /** Initial participants when creating from scratch. Empty by default. */
  participants?: StageParticipantInput[]
}

// IO stages — intake and debrief do not accept screening config fields.
type IntakeStage = StageBase & {
  stage_type: 'intake'
  // No duration_minutes, difficulty, signal_filter, pass_criteria,
  // advance_behavior, or otp_required — backend enforces this.
}

type DebriefStage = StageBase & {
  stage_type: 'debrief'
  // Same restriction as intake — IO stage only.
}

// Screening stages — phone_screen, ai_screening, human_interview all
// require the full screening config field set.
type ScreeningStageBase = StageBase & {
  duration_minutes: number
  difficulty: StageDifficulty
  signal_filter: SignalFilter
  pass_criteria: PassCriteria
  advance_behavior: AdvanceBehavior
  otp_required?: boolean
}

type PhoneScreenStage    = ScreeningStageBase & { stage_type: 'phone_screen' }
type AiScreeningStage    = ScreeningStageBase & { stage_type: 'ai_screening' }
type HumanInterviewStage = ScreeningStageBase & { stage_type: 'human_interview' }

// Take-home — disabled for now; no configurable fields beyond base.
type TakeHomeStage = StageBase & { stage_type: 'take_home' }

export type PipelineStageInput =
  | IntakeStage
  | PhoneScreenStage
  | AiScreeningStage
  | HumanInterviewStage
  | DebriefStage
  | TakeHomeStage

// Stage update shape — existing stages pass their id, new stages omit it.
// The backend's diff-and-sync uses id to preserve row UUIDs through edits
// so question banks FK'd to stage_id survive pipeline auto-save.
// For participants:
//   undefined/omitted → do not touch existing staffing for this stage
//   []                → explicitly clear all participants
//   [...]             → replace the staffing set
// (undefined is chosen over null so the shape widens from PipelineStageInput
// cleanly — Pydantic on the server treats missing field and null the same.)
//
// The intersection distributes over the union, producing:
//   (IntakeStage & { id?: string }) | (PhoneScreenStage & { id?: string }) | ...
export type PipelineStageUpdateInput = PipelineStageInput & { id?: string }

// Server response shape — the backend always returns all fields for every
// stage type (the discriminator is advisory for display, not for field
// presence in the response). PipelineStageResponse is intentionally a flat
// type so consumers can safely read duration_minutes / difficulty etc.
// without narrowing. Tasks 19/20 will add narrowed response variants.
export type PipelineStageResponse = {
  id: string
  position: number
  name: string
  stage_type: StageType
  duration_minutes: number
  difficulty: StageDifficulty
  signal_filter: SignalFilter
  pass_criteria: PassCriteria
  advance_behavior: AdvanceBehavior
  otp_required?: boolean
  sla_days?: number | null
  participants: StageParticipantResponse[]
}

// --- Template ---

export type PipelineTemplate = {
  id: string
  org_unit_id: string
  name: string
  description: string | null
  is_default: boolean
  from_starter: string | null
  stages: PipelineStageResponse[]
  created_at: string
  updated_at: string
}

// --- Starter (no IDs on stages, no template ID) ---

export type StarterTemplate = {
  key: string
  name: string
  description: string
  stages: PipelineStageInput[]
}

// --- Job pipeline instance ---

export type JobPipelineInstance = {
  id: string
  job_posting_id: string
  source_template_id: string | null
  source_template_name: string | null
  /** Incremented on every PATCH save. Used by SourcePill to show "Edited" badge. */
  pipeline_version: number
  stages: PipelineStageResponse[]
  created_at: string
  updated_at: string
}

// --- Request bodies ---

export type CreateTemplateBody =
  | {
      source: 'scratch'
      name: string
      description: string | null
      is_default: boolean
      stages: PipelineStageInput[]
    }
  | {
      source: 'starter'
      starter_key: string
      name: string
      description: string | null
      is_default: boolean
    }

export type UpdateTemplateBody = {
  name?: string
  description?: string | null
  stages?: PipelineStageUpdateInput[]
}

export type CreateJobPipelineBody =
  | { source: 'template'; template_id: string }
  | { source: 'starter'; starter_key: string }
  | { source: 'scratch'; stages: PipelineStageInput[] }

// --- Picker (Task 6 + Task 21) -----------------------------------------------

export type PipelineSourceTemplate = { source: 'template'; template_id: string }
export type PipelineSourceStarter = {
  source: 'starter'
  starter_key: 'standard_technical' | 'fast_track' | 'screening_only' | 'senior_leadership'
}
export type PipelineSourceScratch = { source: 'scratch'; stages: PipelineStageInput[] }

// Alias of CreateJobPipelineBody — same discriminated shape, named for the picker UI.
export type PipelineCreateRequest =
  | PipelineSourceTemplate
  | PipelineSourceStarter
  | PipelineSourceScratch

// --- Activation (Task 12 + Task 22) -----------------------------------------

export type ActivationPredicateFailure = {
  code: string
  message: string
  stage_id: string | null
}

export type ActivationFailedResponse = {
  code: 'activation_predicates_failed'
  predicates_failed: ActivationPredicateFailure[]
}

// --- Edit-category classifier (Task 9 preview-changes) ----------------------

export type EditCategory = 'A' | 'B' | 'C' | 'D'

export type PreviewChangesResponse = {
  category: EditCategory
  warnings: string[]
  in_flight: Record<string, number>
}

export type UpdateJobPipelineBody = {
  stages: PipelineStageUpdateInput[]
}

export type SaveAsTemplateBody = {
  name: string
  description: string | null
  is_default: boolean
}

export type AssignableUser = {
  user_id: string
  full_name: string
  email: string
  role_labels: string[]
  org_unit_name: string
}

// --- API methods ---

export const pipelinesApi = {
  // Starter pack
  getStarterPack: (
    token: string,
    opts?: { signal?: AbortSignal },
  ): Promise<StarterTemplate[]> =>
    apiFetch<StarterTemplate[]>('/api/pipeline-templates/starter-pack', {
      token,
      signal: opts?.signal,
    }),

  // Template library
  listTemplates: (
    token: string,
    unitId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<PipelineTemplate[]> =>
    apiFetch<PipelineTemplate[]>(`/api/org-units/${unitId}/pipeline-templates`, {
      token,
      signal: opts?.signal,
    }),

  createTemplate: (
    token: string,
    unitId: string,
    body: CreateTemplateBody,
  ): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/org-units/${unitId}/pipeline-templates`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateTemplate: (
    token: string,
    templateId: string,
    body: UpdateTemplateBody,
  ): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/pipeline-templates/${templateId}`, {
      token,
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  setDefault: (token: string, templateId: string): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/pipeline-templates/${templateId}/set-default`, {
      token,
      method: 'POST',
    }),

  deleteTemplate: (token: string, templateId: string): Promise<void> =>
    apiFetch<void>(`/api/pipeline-templates/${templateId}`, {
      token,
      method: 'DELETE',
    }),

  // Job pipeline
  getJobPipeline: async (
    token: string,
    jobId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<JobPipelineInstance | null> => {
    try {
      return await apiFetch<JobPipelineInstance>(
        `/api/jobs/${jobId}/pipeline`,
        { token, signal: opts?.signal },
      )
    } catch (err) {
      // Backend returns 404 when no pipeline has been created yet.
      // Status-based check (not substring matching on err.message) so
      // backend detail message changes don't silently swallow unrelated
      // errors here.
      if (err instanceof ApiError && err.status === 404) return null
      throw err
    }
  },

  createJobPipeline: (
    token: string,
    jobId: string,
    body: CreateJobPipelineBody,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateJobPipeline: (
    token: string,
    jobId: string,
    body: UpdateJobPipelineBody,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline`, {
      token,
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  resetJobPipeline: (token: string, jobId: string): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline/reset`, {
      token,
      method: 'POST',
    }),

  saveAsTemplate: (
    token: string,
    jobId: string,
    body: SaveAsTemplateBody,
  ): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/jobs/${jobId}/pipeline/save-as-template`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateSourceTemplate: (token: string, jobId: string): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/jobs/${jobId}/pipeline/update-source-template`, {
      token,
      method: 'POST',
    }),

  swapJobPipeline: (
    token: string,
    jobId: string,
    body: CreateJobPipelineBody,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline/swap`, {
      method: 'POST',
      token,
      body: JSON.stringify(body),
    }),

  getAssignableUsers: (
    token: string,
    jobId: string,
    role: ParticipantRole,
    opts?: { signal?: AbortSignal },
  ): Promise<AssignableUser[]> =>
    apiFetch<AssignableUser[]>(
      `/api/jobs/${jobId}/pipeline/assignable-users?role=${role}`,
      { token, signal: opts?.signal },
    ),

  previewChanges: (
    token: string,
    jobId: string,
    body: { stages: PipelineStageUpdateInput[] },
  ): Promise<PreviewChangesResponse> =>
    apiFetch<PreviewChangesResponse>(
      `/api/jobs/${jobId}/pipeline/preview-changes`,
      { method: 'POST', body: JSON.stringify(body), token },
    ),

  activate: (
    token: string,
    jobId: string,
  ): Promise<{ status: 'active'; job_id: string }> =>
    apiFetch<{ status: 'active'; job_id: string }>(
      `/api/jobs/${jobId}/activate`,
      { method: 'POST', token },
    ),

  pauseStage: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/pause`,
      { method: 'POST', token },
    ),

  unpauseStage: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/unpause`,
      { method: 'POST', token },
    ),

  setStageOtpRequired: (
    token: string,
    jobId: string,
    stageId: string,
    otpRequired: boolean,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/otp-required`,
      {
        method: 'PATCH',
        token,
        body: JSON.stringify({ otp_required: otpRequired }),
      },
    ),
}
