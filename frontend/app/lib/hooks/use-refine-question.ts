'use client'

import { useMutation } from '@tanstack/react-query'

import { questionsApi, type RefineRequest, type RefineResponse } from '@/lib/api/questions'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useRefineQuestion(jobId: string, stageId: string, questionId: string) {
  return useMutation<RefineResponse, Error, RefineRequest>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionsApi.refine(token, jobId, stageId, questionId, body)
    },
  })
}
