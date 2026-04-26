import { apiFetch } from './client'

export type RefineRequest = { instruction: string }

export type RefineResponse = {
  proposed_text: string
  proposed_signal_probed: string
  proposed_mandatory: boolean
  rationale?: string
}

export type DraftRequest = { instruction: string }

export type DraftResponse = RefineResponse & {
  proposed_position: number
}

export type AcceptRefineBody = {
  text: string
  signal_probed: string
  mandatory: boolean
}

export type AcceptDraftBody = AcceptRefineBody & {
  position: number
}

export const questionsApi = {
  refine: (token: string, jobId: string, stageId: string, qid: string, body: RefineRequest) =>
    apiFetch<RefineResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}/refine`,
      { method: 'POST', body: JSON.stringify(body), token },
    ),

  draft: (token: string, jobId: string, stageId: string, body: DraftRequest) =>
    apiFetch<DraftResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/draft`,
      { method: 'POST', body: JSON.stringify(body), token },
    ),

  acceptRefine: (token: string, jobId: string, stageId: string, qid: string, body: AcceptRefineBody) =>
    apiFetch<unknown>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}`,
      { method: 'PATCH', body: JSON.stringify(body), token },
    ),

  acceptDraft: (token: string, jobId: string, stageId: string, body: AcceptDraftBody) =>
    apiFetch<{ id: string }>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions`,
      { method: 'POST', body: JSON.stringify(body), token },
    ),

  toggleMandatory: (token: string, jobId: string, stageId: string, qid: string, mandatory: boolean) =>
    apiFetch<unknown>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}`,
      { method: 'PATCH', body: JSON.stringify({ mandatory }), token },
    ),

  remove: (token: string, jobId: string, stageId: string, qid: string) =>
    apiFetch<unknown>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}`,
      { method: 'DELETE', token },
    ),
}
