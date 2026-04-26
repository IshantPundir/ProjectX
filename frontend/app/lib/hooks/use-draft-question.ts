'use client'

import { useMutation } from '@tanstack/react-query'

import { questionsApi, type DraftRequest, type DraftResponse } from '@/lib/api/questions'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useDraftQuestion(jobId: string, stageId: string) {
  return useMutation<DraftResponse, Error, DraftRequest>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionsApi.draft(token, jobId, stageId, body)
    },
  })
}
