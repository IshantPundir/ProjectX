import { apiFetch } from '@/lib/api/client'

// --- Types ---

export type BankStatus =
  | 'draft'
  | 'generating'
  | 'reviewing'
  | 'confirmed'
  | 'failed'

export type QuestionSource = 'ai_generated' | 'ai_regenerated' | 'recruiter'

export type QuestionRubric = {
  excellent: string
  meets_bar: string
  below_bar: string
}

export type QuestionResponse = {
  id: string
  bank_id: string
  position: number
  source: QuestionSource
  text: string
  signal_values: string[]
  estimated_minutes: number
  is_mandatory: boolean
  follow_ups: string[]
  positive_evidence: string[]
  red_flags: string[]
  rubric: QuestionRubric
  evaluation_hint: string
  edited_by_recruiter: boolean
  created_at: string
  updated_at: string
}

export type BankResponse = {
  id: string
  stage_id: string
  job_posting_id: string
  signal_snapshot_id: string
  status: BankStatus
  prompt_version: string
  generation_error: string | null
  coverage_notes: string | null
  generated_at: string | null
  generated_by: string | null
  confirmed_at: string | null
  confirmed_by: string | null
  question_count: number
  total_minutes: number
  is_stale: boolean
  created_at: string
  updated_at: string
}

export type BankWithQuestionsResponse = BankResponse & {
  questions: QuestionResponse[]
}

export type BanksOverviewResponse = {
  banks: BankResponse[]
}

export type GenerateResponse = {
  bank_id: string | null
  status: BankStatus
}

export type CreateQuestionBody = {
  text: string
  signal_values: string[]
  estimated_minutes: number
  is_mandatory?: boolean
  follow_ups?: string[]
  positive_evidence?: string[]
  red_flags?: string[]
  rubric: QuestionRubric
  evaluation_hint: string
  position?: number
}

export type UpdateQuestionBody = Partial<{
  text: string
  signal_values: string[]
  estimated_minutes: number
  is_mandatory: boolean
  follow_ups: string[]
  positive_evidence: string[]
  red_flags: string[]
  rubric: QuestionRubric
  evaluation_hint: string
  position: number
}>

export type ReorderBody = {
  question_ids: string[]
}

export type RegenerateQuestionBody = {
  replace_signal_values?: string[]
}

// --- API client methods ---

export const questionBanksApi = {
  listBanks: (token: string, jobId: string): Promise<BanksOverviewResponse> =>
    apiFetch<BanksOverviewResponse>(`/api/jobs/${jobId}/pipeline/questions`, {
      token,
    }),

  getBank: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<BankWithQuestionsResponse> =>
    apiFetch<BankWithQuestionsResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions`,
      { token },
    ),

  generateStage: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<GenerateResponse> =>
    apiFetch<GenerateResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/generate`,
      { token, method: 'POST', body: JSON.stringify({}) },
    ),

  generateAll: (token: string, jobId: string): Promise<GenerateResponse> =>
    apiFetch<GenerateResponse>(
      `/api/jobs/${jobId}/pipeline/questions/generate-all`,
      { token, method: 'POST', body: JSON.stringify({}) },
    ),

  regenerateQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    questionId: string,
    body: RegenerateQuestionBody,
  ): Promise<GenerateResponse> =>
    apiFetch<GenerateResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${questionId}/regenerate`,
      { token, method: 'POST', body: JSON.stringify(body) },
    ),

  createQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    body: CreateQuestionBody,
  ): Promise<QuestionResponse> =>
    apiFetch<QuestionResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions`,
      { token, method: 'POST', body: JSON.stringify(body) },
    ),

  updateQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    questionId: string,
    body: UpdateQuestionBody,
  ): Promise<QuestionResponse> =>
    apiFetch<QuestionResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${questionId}`,
      { token, method: 'PATCH', body: JSON.stringify(body) },
    ),

  deleteQuestion: (
    token: string,
    jobId: string,
    stageId: string,
    questionId: string,
  ): Promise<void> =>
    apiFetch<void>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${questionId}`,
      { token, method: 'DELETE' },
    ),

  reorderQuestions: (
    token: string,
    jobId: string,
    stageId: string,
    body: ReorderBody,
  ): Promise<BankWithQuestionsResponse> =>
    apiFetch<BankWithQuestionsResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/reorder`,
      { token, method: 'PATCH', body: JSON.stringify(body) },
    ),

  confirmBank: (
    token: string,
    jobId: string,
    stageId: string,
  ): Promise<BankResponse> =>
    apiFetch<BankResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/confirm`,
      { token, method: 'POST', body: JSON.stringify({}) },
    ),
}
