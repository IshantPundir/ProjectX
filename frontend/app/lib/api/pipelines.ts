import { apiFetch, ApiError } from './client'

// --- Enum types ---

export type StageType =
  | 'phone_screen'
  | 'ai_interview'
  | 'human_interview'
  | 'panel_interview'
  | 'take_home'

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

// --- Stage ---

export type PipelineStageInput = {
  position: number
  name: string
  stage_type: StageType
  duration_minutes: number
  difficulty: StageDifficulty
  signal_filter: SignalFilter
  pass_criteria: PassCriteria
  advance_behavior: AdvanceBehavior
}

// Stage update shape — existing stages pass their id, new stages omit it.
// The backend's diff-and-sync uses id to preserve row UUIDs through edits
// so question banks FK'd to stage_id survive pipeline auto-save.
export type PipelineStageUpdateInput = PipelineStageInput & {
  id?: string
}

export type PipelineStageResponse = PipelineStageInput & {
  id: string
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

export type UpdateJobPipelineBody = {
  stages: PipelineStageUpdateInput[]
}

export type SaveAsTemplateBody = {
  name: string
  description: string | null
  is_default: boolean
}

// --- API methods ---

export const pipelinesApi = {
  // Starter pack
  getStarterPack: (token: string): Promise<StarterTemplate[]> =>
    apiFetch<StarterTemplate[]>('/api/pipeline-templates/starter-pack', { token }),

  // Template library
  listTemplates: (token: string, unitId: string): Promise<PipelineTemplate[]> =>
    apiFetch<PipelineTemplate[]>(`/api/org-units/${unitId}/pipeline-templates`, { token }),

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
  ): Promise<JobPipelineInstance | null> => {
    try {
      return await apiFetch<JobPipelineInstance>(
        `/api/jobs/${jobId}/pipeline`,
        { token },
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
}
